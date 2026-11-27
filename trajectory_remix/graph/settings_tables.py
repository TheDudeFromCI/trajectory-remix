"""Current project settings, stored as tables rather than one JSON document.

Tokenizer definitions (often several megabytes) are stored once, keyed by the
SHA-256 of their canonical JSON. Profiles, endpoints, endpoint roles and tools
get a row each; the remaining, small top-level sections share a key/value table.

Settings snapshots (see settings_history.py) and frozen job configurations use
the *stored form*: settings JSON whose tokenizer profiles carry a
``definition_hash`` instead of the definition itself.
"""

import hashlib
import json

from .models import canonical

DDL = (
    """CREATE TABLE IF NOT EXISTS graph_tokenizer_definitions (
    hash TEXT PRIMARY KEY, definition TEXT NOT NULL CHECK(json_valid(definition)),
    bytes INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS graph_tokenizer_profiles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    definition_hash TEXT NOT NULL REFERENCES graph_tokenizer_definitions(hash),
    special_tokens TEXT NOT NULL CHECK(json_valid(special_tokens)))""",
    """CREATE TABLE IF NOT EXISTS graph_settings_endpoints (
    id TEXT PRIMARY KEY, config TEXT NOT NULL CHECK(json_valid(config)))""",
    """CREATE TABLE IF NOT EXISTS graph_settings_endpoint_roles (
    role TEXT PRIMARY KEY, endpoint_id TEXT NOT NULL REFERENCES graph_settings_endpoints(id))""",
    """CREATE TABLE IF NOT EXISTS graph_settings_tools (
    id TEXT PRIMARY KEY, definition TEXT NOT NULL CHECK(json_valid(definition)))""",
    """CREATE TABLE IF NOT EXISTS graph_settings_values (
    key TEXT PRIMARY KEY, value TEXT NOT NULL CHECK(json_valid(value)))""",
)
TABLED = ("tokenizer_profiles", "endpoints", "endpoint_roles", "tools")


def create(db):
    for statement in DDL:
        db.execute(statement)


def definition_hash(definition) -> str:
    return hashlib.sha256(canonical(definition).encode()).hexdigest()


def store_definition(db, definition) -> str:
    """Insert a tokenizer definition if it is new; return its hash."""
    text = canonical(definition)
    key = hashlib.sha256(text.encode()).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO graph_tokenizer_definitions VALUES(?,?,?)",
        (key, text, len(text.encode())),
    )
    return key


def load_definition(db, key):
    row = db.execute(
        "SELECT definition FROM graph_tokenizer_definitions WHERE hash=?", (key,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Tokenizer definition {key} is no longer stored")
    return json.loads(row[0])


def _map_profiles(data, change):
    profiles = data.get("tokenizer_profiles") or {}
    return {**data, "tokenizer_profiles": {k: change(dict(p)) for k, p in profiles.items()}}


def _hashed(profile, key):
    profile.pop("definition", None)
    return {**profile, "definition_hash": key}


def stored_form(db, data):
    """Settings JSON with each tokenizer definition stored and replaced by its hash."""
    return _map_profiles(
        data,
        lambda p: p if "definition" not in p else _hashed(p, store_definition(db, p["definition"])),
    )


def frozen_form(data):
    """Like stored_form, without writing: for definitions already in the store."""
    return _map_profiles(
        data,
        lambda p: p if "definition" not in p else _hashed(p, definition_hash(p["definition"])),
    )


def resolved_form(db, data):
    """Settings JSON with every ``definition_hash`` replaced by its definition."""

    def resolve(profile):
        if "definition_hash" not in profile:
            return profile
        key = profile.pop("definition_hash")
        return {**profile, "definition": load_definition(db, key)}

    return _map_profiles(data, resolve)


def write(db, data):
    """Replace the current settings tables with ``data`` (settings JSON, either form)."""
    for table in ("graph_settings_endpoint_roles", "graph_settings_endpoints"):
        db.execute(f"DELETE FROM {table}")
    for table in ("graph_tokenizer_profiles", "graph_settings_tools", "graph_settings_values"):
        db.execute(f"DELETE FROM {table}")
    for ident, profile in stored_form(db, data).get("tokenizer_profiles", {}).items():
        db.execute(
            "INSERT INTO graph_tokenizer_profiles VALUES(?,?,?,?)",
            (
                ident,
                profile["name"],
                profile["definition_hash"],
                canonical(profile["special_tokens"]),
            ),
        )
    rows = [(k, canonical(v)) for k, v in data.get("endpoints", {}).items()]
    db.executemany("INSERT INTO graph_settings_endpoints VALUES(?,?)", rows)
    rows = list(data.get("endpoint_roles", {}).items())
    db.executemany("INSERT INTO graph_settings_endpoint_roles VALUES(?,?)", rows)
    rows = [(k, canonical(v)) for k, v in data.get("tools", {}).items()]
    db.executemany("INSERT INTO graph_settings_tools VALUES(?,?)", rows)
    rows = [(k, canonical(v)) for k, v in data.items() if k not in TABLED]
    db.executemany("INSERT INTO graph_settings_values VALUES(?,?)", rows)


def read(db, *, resolve=True):
    """Current settings JSON from the tables, with definitions unless ``resolve`` is False."""
    data = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM graph_settings_values")}
    data["tokenizer_profiles"] = {
        ident: dict(name=name, definition_hash=key, special_tokens=json.loads(tokens))
        for ident, name, key, tokens in db.execute(
            "SELECT id,name,definition_hash,special_tokens FROM graph_tokenizer_profiles ORDER BY id"
        )
    }
    data["endpoints"] = {
        k: json.loads(v)
        for k, v in db.execute("SELECT id,config FROM graph_settings_endpoints ORDER BY id")
    }
    data["endpoint_roles"] = dict(
        db.execute("SELECT role,endpoint_id FROM graph_settings_endpoint_roles ORDER BY role")
    )
    data["tools"] = {
        k: json.loads(v)
        for k, v in db.execute("SELECT id,definition FROM graph_settings_tools ORDER BY id")
    }
    return resolved_form(db, data) if resolve else data
