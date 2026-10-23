"""SQLite project storage and sparse, revision-guarded trajectory edits.

Only requested trajectories are decoded. JSON remains the interchange format for
individual trajectories; it is never used as an in-memory project database.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import MutableSequence
from contextlib import closing, contextmanager
from pathlib import Path

from .dataset import public_config

GAP = 1 << 32


class StorageConflict(ValueError):
    code = "conflict"


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class DatasetStore:
    SCHEMA_VERSION = 2

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError(f"Project does not exist: {self.path}; run 'trajectory-remix init --dataset PATH --tokenizer FILE' first")
        with self.path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\0":
                raise ValueError("Not an initialized SQLite project; initialize a new project and import legacy JSON through the dashboard")
        with self.connect() as db:
            try:
                row = db.execute("SELECT version FROM project WHERE singleton=1").fetchone()
            except sqlite3.DatabaseError as error:
                raise ValueError("Not an initialized Trajectory Remix project") from error
            if row is None or row[0] != self.SCHEMA_VERSION:
                raise ValueError("Unsupported project schema; initialize a new project with its tokenizer and import trajectories")
            db.execute("PRAGMA journal_mode=WAL")
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
                            special_tokens TEXT NOT NULL
                        );
                        CREATE TABLE samples (
                            position INTEGER PRIMARY KEY,
                            sample_id TEXT UNIQUE,
                            source TEXT NOT NULL,
                            attributes TEXT NOT NULL,
                            events INTEGER NOT NULL,
                            tags TEXT NOT NULL,
                            search TEXT NOT NULL
                        );
                    """)
                    db.execute(
                        "INSERT INTO project(singleton,version,config,tokenizer,special_tokens) VALUES(1,?,?,?,?)",
                        (cls.SCHEMA_VERSION, config, tokenizer, special_tokens),
                    )
            return cls(path)
        except BaseException:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            raise

    def load_compiler(self):
        from tokenizers import Tokenizer
        from .compiler import Compiler

        with self.connect() as db:
            tokenizer, special = db.execute("SELECT tokenizer,special_tokens FROM project WHERE singleton=1").fetchone()
        try:
            return Compiler(Tokenizer.from_str(tokenizer), **json.loads(special))
        except Exception as error:
            raise ValueError(f"Invalid project tokenizer: {error}") from error

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
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
                raise ValueError("Duplicate sample IDs; rename duplicates before importing") from error

    @staticmethod
    def row(sample):
        if not isinstance(sample, dict):
            raise ValueError("sample must be an object")
        attributes = {k: v for k, v in sample.items() if k != "replay"}
        replay, metadata = sample.get("replay"), sample.get("metadata")
        events = sum(len(v) for v in replay.values() if isinstance(v, list)) if isinstance(replay, dict) else 0
        tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
        if not isinstance(tags, list):
            tags = []
        ident = sample.get("id")
        return (
            ident if isinstance(ident, str) else None,
            encode(sample), encode(attributes), events, encode(tags),
            (str(ident or "") + encode(metadata or {})).casefold(),
        )

    @staticmethod
    def position(db, index):
        row = db.execute("SELECT position FROM samples ORDER BY position LIMIT 1 OFFSET ?", (index,)).fetchone()
        if row is None:
            raise IndexError("Sample not found")
        return row[0]

    def insert(self, db, index, sample):
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
        db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?)", ((left + right) // 2, *self.row(sample)))
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
                db.execute("UPDATE samples SET sample_id=?,source=?,attributes=?,events=?,tags=?,search=? WHERE position=?", (*self.row(op["value"]), self.position(db, index)))
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

    def listing(self, query="", offset=0, limit=100, dashboard=False):
        clause, args = ("WHERE instr(search, ?) > 0", [query.casefold()]) if query else ("", [])
        with self.connect() as db:
            db.execute("BEGIN")
            total = db.execute(f"SELECT count(*) FROM samples {clause}", args).fetchone()[0] if query else db.execute("SELECT sample_count FROM project").fetchone()[0]
            attributes_column = "NULL" if dashboard else "attributes"
            rows = db.execute(f"SELECT position,sample_id,{attributes_column},events,tags FROM samples {clause} ORDER BY position LIMIT ? OFFSET ?", [*args, limit, offset])
            result = []
            for position, ident, attributes, events, tags in rows:
                if dashboard:
                    index = db.execute("SELECT count(*) FROM samples WHERE position < ?", (position,)).fetchone()[0] if query else offset + len(result)
                    result.append({"index": index, "id": ident if ident is not None else "untitled", "events": events, "tags": json.loads(tags)})
                else:
                    result.append(json.loads(attributes))
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
