"""Durable generation activity independent of the active sample checkpoint."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter


class GenerationLog:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS generation_log_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE, event TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS generation_log_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), imported INTEGER NOT NULL)''')
            if db.execute('SELECT imported FROM generation_log_state WHERE singleton=1').fetchone():
                return
            # Old checkpoints contain overlapping cumulative histories. Import
            # the union in time order, once, including histories missing from the
            # most recently restored checkpoint. Invalid checkpoint JSON must
            # not prevent unrelated generations from being recovered.
            rows = db.execute('''SELECT j.id,e.value FROM generation_jobs j,
                json_each(CASE WHEN json_valid(j.snapshot) THEN
                    CASE WHEN json_type(j.snapshot,'$.logs')='array'
                    THEN json_extract(j.snapshot,'$.logs') ELSE '[]' END ELSE '[]' END) e
                WHERE e.type='object'
                ORDER BY json_extract(e.value,'$.time'),j.rowid,e.key''')
            occurrences = Counter()
            current_time = object()
            for job_id, raw in rows:
                event = json.loads(raw)
                if event.get('time') != current_time:
                    occurrences.clear()
                    current_time = event.get('time')
                canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                occurrences[job_id, digest] += 1
                # Preserve repeated identical events within one history while
                # deduplicating their copies in later cumulative checkpoints.
                key = f'legacy:{digest}:{occurrences[job_id, digest]}'
                db.execute('INSERT OR IGNORE INTO generation_log_events(event_key,event) VALUES (?,?)', (key, raw))
            db.execute('INSERT INTO generation_log_state VALUES (1,1)')

    def append(self, event, *, db=None):
        statement = 'INSERT INTO generation_log_events(event_key,event) VALUES (?,?)'
        values = (uuid.uuid4().hex, json.dumps(event, ensure_ascii=False))
        if db is not None:
            db.execute(statement, values)
            return
        with self.store.connect() as db:
            db.execute(statement, values)

    def entries(self, through=None):
        with self.store.connect() as db:
            for row in db.execute('SELECT event FROM generation_log_events WHERE sequence<=? ORDER BY sequence',
                                  (through if through is not None else 2**63-1,)):
                yield json.loads(row[0])

    def last_sequence(self):
        with self.store.connect() as db:
            return db.execute('SELECT coalesce(max(sequence),0) FROM generation_log_events').fetchone()[0]

    def clear(self):
        with self.store.connect() as db:
            db.execute('DELETE FROM generation_log_events')
        # Leave the import marker in place: old checkpoints must not resurrect
        # history that the user explicitly cleared.

    def text(self, through):
        for entry in self.entries(through):
            context = ''.join(f' [{label}={entry[key]}]' for key, label in (
                ('sample_id', 'sample'), ('job_id', 'job'), ('batch_id', 'batch'),
            ) if entry.get(key))
            yield (f"{entry.get('time', '')} [{entry.get('level', 'info')}]"
                   f"{(' ' + entry['step']) if entry.get('step') else ''}{context}\n"
                   f"{entry.get('message', '')}\n\n")
