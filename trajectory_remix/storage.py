"""SQLite project storage and sparse, revision-guarded trajectory edits.

Only requested trajectories are decoded. JSON remains the interchange format for
individual trajectories; it is never used as an in-memory project database.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from collections.abc import MutableSequence
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .dataset import prepare_sample, public_config
from .search import SearchQuery, diagnostic_flags, readable_text, register_search_functions, used_tools

GAP = 1 << 32


class StorageConflict(ValueError):
    code = "conflict"


class TokenizerMigrationError(ValueError):
    def __init__(self, sample, error):
        super().__init__(f"Trajectory {sample.get('id', sample['uuid'])}: {error}")
        self.sample_id = sample.get("id")
        self.diagnostic = error.diagnostic()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class DatasetStore:
    SCHEMA_VERSION = 5

    def __init__(self, path: Path):
        self.path = Path(path)
        self.search_compiler = None
        # Recheck once per open so compiler code changes cannot leave stale flags.
        self._diagnostics_session = uuid.uuid4().hex
        if not self.path.is_file():
            raise ValueError(f"Project does not exist: {self.path}; run 'trajectory-remix init --dataset PATH --tokenizer FILE' first")
        with self.path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\0":
                raise ValueError("Not an initialized SQLite project; initialize a new project and import legacy JSON through the dashboard")
        with self.connect() as db:
            try:
                row = db.execute("SELECT version,max_window_slots,slot_policy FROM project WHERE singleton=1").fetchone()
            except sqlite3.DatabaseError as error:
                raise ValueError("Not an initialized Trajectory Remix project") from error
            if row is None or row[0] not in (3, 4, self.SCHEMA_VERSION):
                raise ValueError("Unsupported project schema; initialize a new project with its tokenizer and import trajectories")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS external_tools (
                    name TEXT PRIMARY KEY, schema_json TEXT NOT NULL, file_hash TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS tool_dependencies (
                    trajectory_uuid TEXT NOT NULL REFERENCES samples(uuid) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL, schema_json TEXT NOT NULL, file_hash TEXT NOT NULL,
                    PRIMARY KEY(trajectory_uuid, tool_name)
                );
                CREATE TABLE IF NOT EXISTS tool_compilations (
                    trajectory_uuid TEXT PRIMARY KEY REFERENCES samples(uuid) ON DELETE CASCADE,
                    source_hash TEXT NOT NULL, tools_hash TEXT NOT NULL,
                    error TEXT, plan TEXT
                );
                CREATE TABLE IF NOT EXISTS trajectory_search_tools (
                    trajectory_uuid TEXT NOT NULL REFERENCES samples(uuid) ON DELETE CASCADE,
                    tool TEXT NOT NULL,
                    PRIMARY KEY(trajectory_uuid, tool)
                );
                CREATE TABLE IF NOT EXISTS trajectory_search_diagnostics (
                    trajectory_uuid TEXT PRIMARY KEY REFERENCES samples(uuid) ON DELETE CASCADE,
                    source_hash TEXT NOT NULL, cache_key TEXT NOT NULL,
                    errors INTEGER NOT NULL, warnings INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trajectory_metrics (
                    trajectory_uuid TEXT PRIMARY KEY REFERENCES samples(uuid) ON DELETE CASCADE,
                    source_hash TEXT NOT NULL, cache_key TEXT NOT NULL,
                    metrics TEXT NOT NULL CHECK(json_valid(metrics)),
                    error TEXT
                );
            """)
            if row[0] < self.SCHEMA_VERSION:
                db.execute("BEGIN IMMEDIATE")
                # Another opener may have completed the migration while we waited.
                if db.execute("SELECT version FROM project").fetchone()[0] < self.SCHEMA_VERSION:
                    # Stream one source at a time. Ordinary startup/listing never
                    # reads replay JSON; only this one-time upgrade builds it.
                    for source, in db.execute("SELECT source FROM samples"):
                        sample = json.loads(source)
                        db.execute("UPDATE trajectory_metadata SET search=? WHERE trajectory_uuid=?",
                                   (readable_text(sample), sample["uuid"]))
                        self.update_search_tools(db, sample)
                    db.execute("UPDATE project SET version=?", (self.SCHEMA_VERSION,))
        self.max_window_slots, self.slot_policy = row[1], row[2]
        self.samples = SQLiteSamples(self)

    @classmethod
    def initialize(cls, path: Path, compiler, config=None):
        """Create a new project with a fixed tokenizer; never overwrite a file."""
        from .llm import default_config

        path = Path(path)
        tokenizer = compiler.tokenizer.to_str()
        special_tokens = encode(compiler.special)
        config = encode(public_config(default_config() if config is None else config))
        path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve exclusively before SQLite opens the file. A failed init removes
        # only the file this invocation created, never an existing project.
        with path.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.executescript("""
                        BEGIN IMMEDIATE;
                        CREATE TABLE project (
                            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                            version INTEGER NOT NULL,
                            revision INTEGER NOT NULL DEFAULT 0,
                            sample_revision INTEGER NOT NULL DEFAULT 0,
                            sample_count INTEGER NOT NULL DEFAULT 0,
                            config TEXT NOT NULL,
                            tokenizer TEXT NOT NULL,
                            special_tokens TEXT NOT NULL,
                            max_window_slots INTEGER CHECK(max_window_slots IS NULL OR max_window_slots > 0),
                            slot_policy TEXT NOT NULL
                        );
                        CREATE TABLE samples (
                            uuid TEXT PRIMARY KEY NOT NULL,
                            position INTEGER UNIQUE NOT NULL,
                            sample_id TEXT UNIQUE,
                            source TEXT NOT NULL CHECK(json_valid(source)),
                            attributes TEXT NOT NULL,
                            content_hash TEXT NOT NULL,
                            CHECK(json_type(source,'$.uuid') IS 'text' AND json_extract(source,'$.uuid') = uuid)
                        );
                        CREATE TRIGGER immutable_trajectory_uuid
                        BEFORE UPDATE OF uuid ON samples WHEN NEW.uuid != OLD.uuid
                        BEGIN SELECT RAISE(ABORT, 'Trajectory UUID is immutable'); END;
                        CREATE TABLE trajectory_metadata (
                            trajectory_uuid TEXT PRIMARY KEY NOT NULL REFERENCES samples(uuid) ON DELETE CASCADE,
                            properties TEXT CHECK(properties IS NULL OR json_valid(properties)),
                            tags TEXT NOT NULL CHECK(json_valid(tags) AND json_type(tags)='array'),
                            event_count INTEGER NOT NULL,
                            window_count INTEGER NOT NULL,
                            tag_count INTEGER NOT NULL,
                            search TEXT NOT NULL
                        );
                        CREATE INDEX metadata_event_count ON trajectory_metadata(event_count,trajectory_uuid);
                        CREATE INDEX metadata_window_count ON trajectory_metadata(window_count,trajectory_uuid);
                        CREATE INDEX metadata_tag_count ON trajectory_metadata(tag_count,trajectory_uuid);
                        CREATE TABLE metadata_tags (
                            trajectory_uuid TEXT NOT NULL REFERENCES trajectory_metadata(trajectory_uuid) ON DELETE CASCADE,
                            tag TEXT NOT NULL,
                            PRIMARY KEY(trajectory_uuid,tag)
                        );
                        CREATE INDEX metadata_tag_lookup ON metadata_tags(tag,trajectory_uuid);
                    """)
                    db.execute(
                        "INSERT INTO project(singleton,version,config,tokenizer,special_tokens,max_window_slots,slot_policy) VALUES(1,?,?,?,?,?,?)",
                        (cls.SCHEMA_VERSION, config, tokenizer, special_tokens, compiler.max_window_slots, compiler.slot_policy),
                    )
            return cls(path)
        except BaseException:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            raise

    def load_compiler(self, tools=None):
        from tokenizers import Tokenizer
        from .compiler import Compiler

        with self.connect() as db:
            tokenizer, special = db.execute("SELECT tokenizer,special_tokens FROM project WHERE singleton=1").fetchone()
        try:
            return Compiler(Tokenizer.from_str(tokenizer), **json.loads(special), max_window_slots=self.max_window_slots, slot_policy=self.slot_policy, tools=tools, tool_store=self)
        except Exception as error:
            raise ValueError(f"Invalid project tokenizer: {error}") from error

    def migrate_tokenizer(self, compiler, expected_revision, progress):
        """Compile a snapshot one source at a time, then atomically install it.

        Stage tool plans on disk so a failed migration never updates live caches
        and a large dataset never accumulates compiled trajectories in RAM.
        Writers remain free during compilation; any source/config change makes
        the final revision check fail instead of installing a stale migration.
        """
        from .compiler import CompileError

        store = self
        with TemporaryDirectory(prefix="remix-tokenizer-") as temp:
            with closing(sqlite3.connect(Path(temp) / "plans.sqlite")) as staged:
                staged.execute("CREATE TABLE plans(uuid TEXT, source_hash TEXT, tools_hash TEXT, error TEXT, plan TEXT)")

                class MigrationTools:
                    def verify_tool_dependencies(self, sample, registry):
                        store.verify_tool_dependencies(sample, registry)

                    def record_tool_compilation(self, sample, registry, plan):
                        if any(registry.get(name).external for name in store.tool_names(sample, registry)):
                            try:
                                cached = encode(plan)
                            except (TypeError, ValueError):
                                cached = None
                            staged.execute("INSERT INTO plans VALUES(?,?,?,NULL,?)",
                                           (sample["uuid"], store.row(sample)[-1], registry.fingerprint, cached))

                compiler.tool_store = MigrationTools()
                try:
                    with self.connect() as snapshot:
                        snapshot.execute("BEGIN")
                        revision, total = snapshot.execute("SELECT revision,sample_count FROM project").fetchone()
                        if revision != expected_revision:
                            raise StorageConflict("Dataset changed; reload before migrating the tokenizer")
                        progress(0, total)
                        for converted, (raw,) in enumerate(snapshot.execute("SELECT source FROM samples ORDER BY position"), 1):
                            sample = json.loads(raw)
                            try:
                                compiler.compile(sample)
                            except CompileError as error:
                                raise TokenizerMigrationError(sample, error) from error
                            progress(converted, total)
                    with self.transaction(expected_revision) as db:
                        db.execute("UPDATE project SET tokenizer=?,special_tokens=?,sample_revision=revision+1,revision=revision+1",
                                   (compiler.tokenizer.to_str(), encode(compiler.special)))
                        db.execute("DELETE FROM tool_compilations")
                        db.executemany("INSERT INTO tool_compilations VALUES(?,?,?,?,?)", staged.execute("SELECT * FROM plans"))
                        return db.execute("SELECT revision FROM project").fetchone()[0]
                finally:
                    compiler.tool_store = self

    def tool_names(self, sample, registry):
        names = set()
        replay = sample.get("replay", {})
        if not isinstance(replay, dict):
            return names
        for group in replay.values():
            if not isinstance(group, list):
                continue
            for event in group:
                if not isinstance(event, dict):
                    continue
                if event.get("op") in ("tool-call", "invisible-tool-call") and isinstance(event.get("tool"), str):
                    names.add(event["tool"])
                elif event.get("op") == "show_tools":
                    if isinstance(event.get("tools"), list):
                        names.update(name for name in event["tools"] if isinstance(name, str))
                    if event.get("randomly-add-additional-tools") is True:
                        names.update(name for name, tool in registry.tools.items() if not tool.invisible)
        return names

    @staticmethod
    def schema_text(schema):
        return json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def verify_tool_dependencies(self, sample, registry):
        from .compiler import CompileError
        from .dataset import trajectory_uuid
        from .tools import ToolError
        names = self.tool_names(sample, registry)
        with self.connect() as db:
            previous = dict(db.execute("SELECT tool_name,schema_json FROM tool_dependencies WHERE trajectory_uuid=?", (trajectory_uuid(sample),)))
        if any(isinstance(event, dict) and event.get("op") == "show_tools" and event.get("randomly-add-additional-tools") is True
               for group in sample.get("replay", {}).values() if isinstance(group, list) for event in group):
            names.update(previous)
        for name in names:
            try:
                tool = registry.get(name)
            except ToolError as error:
                raise CompileError(str(error), "tools") from error
            if name in previous and previous[name] != self.schema_text(tool.schema):
                raise CompileError(f"Tool schema changed: {name}; repair the trajectory's tool dependency before recompiling", "tools")

    def record_tool_compilation(self, sample, registry, plan):
        from .dataset import trajectory_uuid
        ident = trajectory_uuid(sample)
        names = self.tool_names(sample, registry)
        with self.connect() as db:
            row = db.execute("SELECT source,content_hash FROM samples WHERE uuid=?", (ident,)).fetchone()
            # Compiling an unsaved editor draft must not pin dependencies or replace a saved cache.
            if row is None or hashlib.sha256(json.dumps(sample, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest() != row[1]:
                return
            old = {name for name, in db.execute("SELECT tool_name FROM tool_dependencies WHERE trajectory_uuid=?", (ident,))}
            for name in old - names:
                db.execute("DELETE FROM tool_dependencies WHERE trajectory_uuid=? AND tool_name=?", (ident, name))
            for name in names:
                tool = registry.get(name)
                if tool.external or name in old:
                    db.execute("INSERT INTO tool_dependencies VALUES(?,?,?,?) ON CONFLICT(trajectory_uuid,tool_name) DO UPDATE SET file_hash=excluded.file_hash",
                               (ident, name, self.schema_text(tool.schema), tool.file_hash))
            if any(registry.get(name).external for name in names):
                try:
                    cached_plan = encode(plan)
                except (TypeError, ValueError):
                    cached_plan = None
                db.execute("INSERT OR REPLACE INTO tool_compilations VALUES(?,?,?,?,?)",
                           (ident, row[1], registry.fingerprint, None, cached_plan))
            else:
                db.execute("DELETE FROM tool_compilations WHERE trajectory_uuid=?", (ident,))

    def configure_tools(self, registry):
        """Register startup files and recompile affected saved trajectories individually."""
        from .compiler import CompileError
        with self.connect() as db:
            previous = {name: (schema, digest, available) for name, schema, digest, available in db.execute("SELECT * FROM external_tools")}
            db.execute("UPDATE external_tools SET available=0")
            for tool in registry.tools.values():
                if tool.external:
                    db.execute("INSERT OR REPLACE INTO external_tools VALUES(?,?,?,1)",
                               (tool.name, self.schema_text(tool.schema), tool.file_hash))
        current = {tool.name: (self.schema_text(tool.schema), tool.file_hash, 1) for tool in registry.tools.values() if tool.external}
        changed = {name for name in previous.keys() | current.keys() if previous.get(name) != current.get(name)}
        compiler = self.load_compiler(registry)
        self.search_compiler = compiler
        diagnostics = []
        if not previous and not current:
            return compiler, diagnostics
        # Filter in SQLite; do not decode unrelated trajectories at startup.
        tracked = sorted(previous.keys() | current.keys())
        placeholders = ",".join("?" for _ in tracked)
        def candidates():
            with self.connect() as db:
                yield from db.execute(f"""
                    SELECT s.source, s.content_hash, c.source_hash, c.error
                    FROM samples s LEFT JOIN tool_compilations c ON c.trajectory_uuid=s.uuid
                    WHERE EXISTS (SELECT 1 FROM tool_dependencies d WHERE d.trajectory_uuid=s.uuid)
                       OR EXISTS (
                          SELECT 1 FROM json_each(CASE WHEN json_type(s.source,'$.replay')='object'
                               THEN json_extract(s.source,'$.replay') ELSE '{{}}' END) g,
                               json_each(CASE WHEN g.type='array' THEN g.value ELSE '[]' END) e
                          WHERE e.type='object' AND (
                             json_extract(e.value,'$.tool') IN ({placeholders}) OR
                             json_extract(e.value,'$.op')='show_tools')
                       )
                """, tracked)
        for raw, source_hash, cached_source, cached_error in candidates():
            sample = json.loads(raw)
            names = self.tool_names(sample, registry)
            from .dataset import trajectory_uuid
            ident = trajectory_uuid(sample)
            with self.connect() as db:
                dependencies = dict(db.execute("SELECT tool_name,file_hash FROM tool_dependencies WHERE trajectory_uuid=?", (ident,)))
            stale_files = any(name in registry.tools and digest != registry.get(name).file_hash for name, digest in dependencies.items())
            if not (names & changed or dependencies.keys() - registry.tools.keys() or stale_files or names & current.keys() and cached_source != source_hash):
                if cached_error:
                    diagnostics.append({"sample_id": sample.get("id"), "severity": "error", "path": "tools", "message": cached_error})
                continue
            try:
                compiler.compile(sample)
            except CompileError as error:
                diagnostics.append({"sample_id": sample.get("id"), **error.diagnostic()})
                with self.connect() as db:
                    db.execute("INSERT OR REPLACE INTO tool_compilations VALUES(?,?,?,?,NULL)", (ident, source_hash, registry.fingerprint, str(error)))
        return compiler, diagnostics

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            register_search_functions(db)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            db.execute("PRAGMA cache_size=-4096")
            db.execute("PRAGMA temp_store=FILE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def info(self):
        with self.connect() as db:
            return db.execute(
                "SELECT revision,sample_revision,sample_count,config FROM project"
            ).fetchone()

    @contextmanager
    def transaction(self, expected_revision=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM project").fetchone()[0]
            if expected_revision is not None and expected_revision != revision:
                raise StorageConflict("Dataset changed; read it again before editing")
            try:
                yield db
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Duplicate trajectory ID or UUID: {error}" if "UNIQUE constraint" in str(error) else str(error)) from error

    def normalize(self, sample, existing=None):
        from .compiler import CompileError, window_names

        sample = prepare_sample(sample, existing)
        count = len(window_names(sample))
        if self.max_window_slots is not None and count > self.max_window_slots:
            raise CompileError(f"Trajectory uses {count} window slots; project limit is {self.max_window_slots}", "replay")
        return sample

    @staticmethod
    def row(sample):
        attributes = {k: v for k, v in sample.items() if k not in ("replay", "metadata")}
        digest = hashlib.sha256(json.dumps(sample, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        ident = sample.get("id")
        return (sample["uuid"], ident if isinstance(ident, str) else None, encode(sample), encode(attributes), digest)

    @staticmethod
    def update_search_tools(db, sample):
        db.execute("DELETE FROM trajectory_search_tools WHERE trajectory_uuid=?", (sample["uuid"],))
        db.executemany("INSERT INTO trajectory_search_tools VALUES(?,?)",
                       [(sample["uuid"], tool) for tool in used_tools(sample)])

    @staticmethod
    def update_metadata(db, sample):
        from .compiler import window_names

        replay, metadata = sample.get("replay"), sample.get("metadata")
        events = sum(len(v) for v in replay.values() if isinstance(v, list)) if isinstance(replay, dict) else 0
        tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
        tags = list(dict.fromkeys(t for t in tags if isinstance(t, str))) if isinstance(tags, list) else []
        properties = encode(metadata) if "metadata" in sample else None
        db.execute("""
            INSERT INTO trajectory_metadata VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(trajectory_uuid) DO UPDATE SET
                properties=excluded.properties,tags=excluded.tags,
                event_count=excluded.event_count,window_count=excluded.window_count,
                tag_count=excluded.tag_count,search=excluded.search
        """, (sample["uuid"], properties, encode(tags), events, len(window_names(sample)), len(tags), readable_text(sample)))
        DatasetStore.update_search_tools(db, sample)
        catalog = {name: (schema, digest) for name, schema, digest in db.execute("SELECT name,schema_json,file_hash FROM external_tools WHERE available=1")}
        names = set()
        if isinstance(replay, dict):
            for group in replay.values():
                if not isinstance(group, list):
                    continue
                for event in group:
                    if not isinstance(event, dict):
                        continue
                    if event.get("op") in ("tool-call", "invisible-tool-call") and isinstance(event.get("tool"), str):
                        names.add(event["tool"])
                    elif event.get("op") == "show_tools":
                        if isinstance(event.get("tools"), list):
                            names.update(name for name in event["tools"] if isinstance(name, str))
                        if event.get("randomly-add-additional-tools") is True:
                            names.update(catalog)
        for name, in db.execute("SELECT tool_name FROM tool_dependencies WHERE trajectory_uuid=?", (sample["uuid"],)).fetchall():
            if name not in names:
                db.execute("DELETE FROM tool_dependencies WHERE trajectory_uuid=? AND tool_name=?", (sample["uuid"], name))
        for name in names & catalog.keys():
            schema, digest = catalog[name]
            db.execute("INSERT OR IGNORE INTO tool_dependencies VALUES(?,?,?,?)", (sample["uuid"], name, schema, digest))
        db.execute("DELETE FROM tool_compilations WHERE trajectory_uuid=?", (sample["uuid"],))
        db.execute("DELETE FROM metadata_tags WHERE trajectory_uuid=?", (sample["uuid"],))
        db.executemany("INSERT INTO metadata_tags VALUES(?,?)", [(sample["uuid"], tag) for tag in tags])

    @staticmethod
    def position(db, index):
        row = db.execute("SELECT position FROM samples ORDER BY position LIMIT 1 OFFSET ?", (index,)).fetchone()
        if row is None:
            raise IndexError("Sample not found")
        return row[0]

    def insert(self, db, index, sample):
        sample = self.normalize(sample)
        count = db.execute("SELECT sample_count FROM project").fetchone()[0]
        if not 0 <= index <= count:
            raise IndexError("Sample not found")
        left = (db.execute("SELECT coalesce(max(position),0) FROM samples").fetchone()[0] if index == count else self.position(db, index - 1)) if index else 0
        right = self.position(db, index) if index < count else left + 2 * GAP
        if right - left < 2:
            # Rare repeated middle insertions exhaust a gap. Rebalance in SQL,
            # without loading rows into Python or colliding with primary keys.
            db.execute("CREATE TEMP TABLE ordering(position INTEGER PRIMARY KEY, new_position INTEGER NOT NULL)")
            db.execute("INSERT INTO ordering SELECT position, row_number() OVER (ORDER BY position)*? FROM samples", (GAP,))
            db.execute("UPDATE samples SET position=-position")
            db.execute("UPDATE samples SET position=(SELECT new_position FROM ordering WHERE ordering.position=-samples.position)")
            db.execute("DROP TABLE ordering")
            left = self.position(db, index - 1) if index else 0
            right = self.position(db, index) if index < count else left + 2 * GAP
        db.execute("INSERT INTO samples(uuid,sample_id,source,attributes,content_hash,position) VALUES(?,?,?,?,?,?)", (*self.row(sample), (left + right) // 2))
        self.update_metadata(db, sample)
        db.execute("UPDATE project SET sample_count=sample_count+1")

    def apply(self, db, operations):
        for op in operations:
            index = int(op["path"].lstrip("/"))
            if op["op"] == "add":
                self.insert(db, index, op["value"])
            elif op["op"] == "remove":
                db.execute("DELETE FROM samples WHERE position=?", (self.position(db, index),))
                db.execute("UPDATE project SET sample_count=sample_count-1")
            elif op["op"] == "replace":
                position = self.position(db, index)
                existing = json.loads(db.execute("SELECT source FROM samples WHERE position=?", (position,)).fetchone()[0])
                sample = self.normalize(op["value"], existing)
                db.execute("UPDATE samples SET sample_id=?,source=?,attributes=?,content_hash=? WHERE position=?", (*self.row(sample)[1:], position))
                self.update_metadata(db, sample)
            else:
                raise ValueError("Unsupported storage operation")

    def write(self, operations, expected_revision, config=None):
        with self.transaction(expected_revision) as db:
            self.apply(db, operations)
            if config is not None:
                db.execute("UPDATE project SET config=?", (encode(public_config(config)),))
            db.execute("UPDATE project SET sample_revision=revision+1,revision=revision+1")
            return db.execute("SELECT revision FROM project").fetchone()[0]

    def set_config(self, config, expected_revision=None):
        with self.transaction(expected_revision) as db:
            db.execute("UPDATE project SET config=?,revision=revision+1", (encode(public_config(config)),))

    def replace(self, samples, expected_revision, config=None):
        with self.transaction(expected_revision) as db:
            db.execute("DELETE FROM samples")
            db.execute("UPDATE project SET sample_count=0")
            for index, sample in enumerate(samples):
                self.insert(db, index, sample)
            if config is not None:
                db.execute("UPDATE project SET config=?", (encode(public_config(config)),))
            db.execute("UPDATE project SET sample_revision=revision+1,revision=revision+1")
            return db.execute("SELECT revision FROM project").fetchone()[0]

    def refresh_search_diagnostics(self, db):
        """Compile stale sources one at a time, only for diagnostic searches."""
        if self.search_compiler is None:
            self.search_compiler = self.load_compiler()
        compiler = self.search_compiler
        cache_key = self._diagnostics_session + compiler.tools.fingerprint
        rows = db.execute("""
            SELECT s.uuid,s.source,s.content_hash FROM samples s
            LEFT JOIN trajectory_search_diagnostics d ON d.trajectory_uuid=s.uuid
            WHERE d.source_hash IS NOT s.content_hash OR d.cache_key IS NOT ?
        """, (cache_key,))
        for ident, source, digest in rows:
            errors, warnings = diagnostic_flags(json.loads(source), compiler)
            db.execute("INSERT OR REPLACE INTO trajectory_search_diagnostics VALUES(?,?,?,?,?)",
                       (ident, digest, cache_key, errors, warnings))

    def dataset_tags(self):
        """Return distinct stored tags across the full dataset."""
        with self.connect() as db:
            return sorted((row[0] for row in db.execute("SELECT DISTINCT tag FROM metadata_tags")),
                          key=lambda tag: (tag.casefold(), tag))

    def listing(self, query="", offset=0, limit=100, dashboard=False):
        search = SearchQuery(query)
        predicate, args = search.sql()
        clause = f"WHERE {predicate}" if query.strip() else ""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE" if search.requires_diagnostics else "BEGIN")
            if search.requires_diagnostics:
                self.refresh_search_diagnostics(db)
            total = db.execute(f"SELECT count(*) FROM samples s JOIN trajectory_metadata m ON m.trajectory_uuid=s.uuid {clause}", args).fetchone()[0] if clause else db.execute("SELECT sample_count FROM project").fetchone()[0]
            attributes_column = "NULL" if dashboard else "s.attributes"
            properties_column = "json_extract(m.properties, '$.validation_level')" if dashboard else "m.properties"
            rows = db.execute(f"SELECT s.position,s.sample_id,{attributes_column},m.event_count,m.tags,{properties_column},s.uuid FROM samples s JOIN trajectory_metadata m ON m.trajectory_uuid=s.uuid {clause} ORDER BY s.position LIMIT ? OFFSET ?", [*args, limit, offset])
            result = []
            for position, ident, attributes, events, tags, properties, ident_uuid in rows:
                if dashboard:
                    index = db.execute("SELECT count(*) FROM samples WHERE position < ?", (position,)).fetchone()[0] if query else offset + len(result)
                    result.append({"index": index, "id": ident if ident is not None else "untitled", "uuid": ident_uuid, "events": events, "tags": json.loads(tags), "validation_level": properties if properties is not None else -3})
                else:
                    item = json.loads(attributes)
                    if properties is not None:
                        item["metadata"] = json.loads(properties)
                    result.append(item)
            return total, result

    @contextmanager
    def read_samples(self, indices=None):
        """A consistent read snapshot; yield one trajectory at a time."""
        with self.connect() as db:
            db.execute("BEGIN")
            # Establish the snapshot before the caller starts consuming it.
            db.execute("SELECT revision FROM project").fetchone()
            def records():
                if indices is None:
                    for row in db.execute("SELECT source FROM samples ORDER BY position"):
                        yield json.loads(row[0])
                else:
                    for index in indices:
                        row = db.execute("SELECT source FROM samples ORDER BY position LIMIT 1 OFFSET ?", (index,)).fetchone()
                        if row is None:
                            raise ValueError("Sample not found")
                        yield json.loads(row[0])
            yield records()


class SQLiteSamples(MutableSequence):
    def __init__(self, store):
        self.store = store

    def __len__(self):
        return self.store.info()[2]

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self))) ]
        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError("Sample not found")
        with self.store.connect() as db:
            row = db.execute("SELECT source FROM samples ORDER BY position LIMIT 1 OFFSET ?", (index,)).fetchone()
            if row is None:
                raise IndexError("Sample not found")
            return json.loads(row[0])

    def find(self, ident):
        with self.store.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT position,source FROM samples WHERE sample_id=?", (ident,)).fetchone()
            if row is None:
                return None
            index = db.execute("SELECT count(*) FROM samples WHERE position < ?", (row[0],)).fetchone()[0]
            return index, json.loads(row[1])

    def index_of(self, ident):
        with self.store.connect() as db:
            row = db.execute("SELECT count(*) FROM samples WHERE position < (SELECT position FROM samples WHERE sample_id=?) HAVING EXISTS(SELECT 1 FROM samples WHERE sample_id=?)", (ident, ident)).fetchone()
            return row[0] if row else None

    def __iter__(self):
        with self.store.read_samples() as samples:
            yield from samples

    def copy(self):
        return SampleDraft(self)

    def __deepcopy__(self, memo):
        # Explicit snapshots for callers/tests; the server never uses this.
        return list(self)

    def __eq__(self, other):
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __setitem__(self, index, sample):
        draft = self.copy()
        draft[index] = sample
        self.store.write(draft.operations, draft.revision)

    def __delitem__(self, index):
        draft = self.copy()
        del draft[index]
        self.store.write(draft.operations, draft.revision)

    def insert(self, index, sample):
        draft = self.copy()
        draft.insert(index, sample)
        self.store.write(draft.operations, draft.revision)


class SampleDraft(MutableSequence):
    """Copy-on-write sequence: retain only edited samples and their inverses."""
    def __init__(self, base):
        self.base = base
        info = base.store.info()
        self.revision, self.length = info[0], info[2]
        self.operations, self.inverse = [], []

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError("Sample not found")
        for op in reversed(self.operations):
            at = int(op["path"][1:])
            if op["op"] in ("replace", "add") and at == index:
                return copy.deepcopy(op["value"])
            if op["op"] == "add" and at < index:
                index -= 1
            elif op["op"] == "remove" and at <= index:
                index += 1
        return self.base[index]

    def find(self, ident):
        found = self.base.find(ident)
        index = found[0] if found else None
        for op in self.operations:
            at = int(op["path"][1:])
            if op["op"] == "add" and index is not None and at <= index:
                index += 1
            elif op["op"] == "remove" and index is not None:
                index = None if at == index else index - (at < index)
            elif op["op"] == "replace" and at == index:
                index = None
            if op["op"] != "remove" and op["value"].get("id") == ident:
                index = at
        return (index, self[index]) if index is not None else None

    def _record(self, operation, inverse):
        self.operations.append(copy.deepcopy(operation))
        self.inverse.insert(0, copy.deepcopy(inverse))

    def __setitem__(self, index, sample):
        before = self[index]
        sample = self.base.store.normalize(sample, before)
        if index < 0:
            index += self.length
        self._record({"op": "replace", "path": f"/{index}", "value": sample}, {"op": "replace", "path": f"/{index}", "value": before})

    def __delitem__(self, index):
        before = self[index]
        if index < 0:
            index += self.length
        self._record({"op": "remove", "path": f"/{index}"}, {"op": "add", "path": f"/{index}", "value": before})
        self.length -= 1

    def insert(self, index, sample):
        sample = self.base.store.normalize(sample)
        index = max(0, min(index, self.length))
        self._record({"op": "add", "path": f"/{index}", "value": sample}, {"op": "remove", "path": f"/{index}"})
        self.length += 1

    def copy(self):
        other = SampleDraft.__new__(SampleDraft)
        other.base, other.revision, other.length = self.base, self.revision, self.length
        other.operations, other.inverse = copy.deepcopy((self.operations, self.inverse))
        return other

    def __deepcopy__(self, memo):
        return self.copy()

    def changed_samples(self):
        ids = {op["value"].get("id") for op in self.operations if "value" in op}
        for ident in ids:
            found = self.find(ident)
            if found:
                yield found[1]


class DatasetState(dict):
    """Session configuration/credentials plus live database revisions."""
    def __init__(self, store, config):
        self.store = store
        self.config_text = store.info()[3]
        super().__init__(samples=store.samples, config=config, revision=store.info()[0])

    def __getitem__(self, key):
        if key == "revision":
            return self.store.info()[0]
        if key == "config":
            text = self.store.info()[3]
            if text != self.config_text:
                from .llm import validate_config
                config = validate_config(json.loads(text))
                keys = {e["id"]: e.get("api_key") for e in super().__getitem__("config")["endpoints"]}
                for endpoint in config["endpoints"]:
                    if keys.get(endpoint["id"]):
                        endpoint["api_key"] = keys[endpoint["id"]]
                super().__setitem__("config", config)
                self.config_text = text
        return super().__getitem__(key)

    def set_config(self, config, expected_revision=None):
        self.store.set_config(config, expected_revision)
        self.config_text = encode(public_config(config))
        super().__setitem__("config", config)

    def __deepcopy__(self, memo):
        return {"samples": list(self["samples"]), "config": copy.deepcopy(self["config"]), "revision": self["revision"]}

    def __eq__(self, other):
        return all(self[k] == other[k] for k in self)
