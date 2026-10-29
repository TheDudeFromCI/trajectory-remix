"""One-pass recovery of existing generated drafts, with durable per-entry results."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid


# Read metadata projections, not every trajectory's replay. Checkpoint ownership
# excludes manual copies that retained an automated source's metadata.
CANDIDATES = """
FROM samples s JOIN trajectory_metadata m ON m.trajectory_uuid=s.uuid
LEFT JOIN generation_jobs j ON j.id=json_extract(m.properties,'$.generation_job')
WHERE json_extract(m.properties,'$.generation_phase')='planning'
  AND json_extract(m.properties,'$.origin')='automation'
  AND json_type(m.properties,'$.generation_job')='text'
  AND json_extract(m.properties,'$.generation_job')!=''
  AND (j.id IS NULL OR CASE WHEN json_valid(j.snapshot)
       THEN coalesce(json_extract(j.snapshot,'$.checkpoint.sample.uuid')=s.uuid,1)
       ELSE 1 END)
"""


class GenerationResumeQueue:
    def __init__(self, owner):
        self.owner = owner
        self.store = owner.state.store
        self.status = {'state': 'idle', 'running': False, 'target': 0,
                       'processed': 0, 'completed': 0, 'failed': 0, 'skipped': 0,
                       'current_id': None, 'issues': []}
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS generation_resume_runs (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS generation_resume_items (
                run_id TEXT NOT NULL REFERENCES generation_resume_runs(id),
                position INTEGER NOT NULL, trajectory_uuid TEXT NOT NULL,
                sample_id TEXT, job_id TEXT NOT NULL, state TEXT NOT NULL,
                error TEXT, PRIMARY KEY(run_id,position))''')
            db.execute("CREATE INDEX IF NOT EXISTS trajectory_generation_pending ON trajectory_metadata(json_extract(properties,'$.generation_phase'),json_extract(properties,'$.origin'))")
            row = db.execute('SELECT status FROM generation_resume_runs ORDER BY rowid DESC LIMIT 1').fetchone()
            if row:
                self.status = json.loads(row[0])
                if self.status['running']:
                    self.status.update(state='interrupted', running=False, current_id=None)
                    db.execute("UPDATE generation_resume_items SET state='interrupted' WHERE run_id=? AND state='running'", (self.status['id'],))
                    db.execute('UPDATE generation_resume_runs SET status=? WHERE id=?', (owner.clean(self.status), self.status['id']))

    def count(self):
        with self.store.connect() as db:
            return db.execute('SELECT count(*) ' + CANDIDATES).fetchone()[0]

    def persist(self):
        with self.store.connect() as db:
            db.execute('UPDATE generation_resume_runs SET status=? WHERE id=?',
                       (self.owner.clean(self.status), self.status['id']))

    def start(self):
        owner = self.owner
        if owner.status['running'] or owner.legacy.status['running'] or self.status['running']:
            raise ValueError('Generation is already running')
        validator = getattr(owner.legacy, 'validation_runner', None)
        if validator and validator.status.get('running'):
            raise ValueError('Finish or cancel trajectory validation before generation')
        migration = getattr(owner.legacy, 'tokenizer_migration', None)
        if migration and migration.running:
            raise ValueError('Wait for tokenizer migration to finish')
        run_id = str(uuid.uuid4())
        status = {'id': run_id, 'state': 'running', 'running': True, 'target': 0,
                  'processed': 0, 'completed': 0, 'failed': 0, 'skipped': 0,
                  'current_id': None, 'issues': []}
        with self.store.connect() as db:
            db.execute('INSERT INTO generation_resume_runs VALUES (?,?)', (run_id, owner.clean(status)))
            db.execute('''INSERT INTO generation_resume_items
                SELECT ?,s.position,s.uuid,s.sample_id,json_extract(m.properties,'$.generation_job'),'pending',NULL
                ''' + CANDIDATES + ' ORDER BY s.position', (run_id,))
            status['target'] = db.execute('SELECT count(*) FROM generation_resume_items WHERE run_id=?', (run_id,)).fetchone()[0]
            if not status['target']:
                raise ValueError('No unfinished generated trajectories are available')
            db.execute('UPDATE generation_resume_runs SET status=? WHERE id=?', (owner.clean(status), run_id))
        self.status = status
        owner.status.update(running=True, phase='resuming_all', paused=False,
                            pause_requested=False, active=[], last_error=None)
        owner.resume_event.set()
        owner.job = asyncio.create_task(self.run())
        return run_id

    def results(self, offset=0, limit=100):
        if not self.status.get('id'):
            return {'total': 0, 'items': []}
        with self.store.connect() as db:
            rows = db.execute('''SELECT sample_id,trajectory_uuid,state,error FROM generation_resume_items
                WHERE run_id=? ORDER BY position LIMIT ? OFFSET ?''',
                (self.status['id'], limit, offset)).fetchall()
        return {'total': self.status['target'], 'items': [dict(zip(('sample_id','uuid','state','error'), row)) for row in rows]}

    def finish_item(self, item, state, error=None):
        error = self.owner.clean(error) if error else None
        self.status['processed'] += 1
        self.status[{'complete': 'completed', 'failed': 'failed', 'skipped': 'skipped'}[state]] += 1
        if error:
            self.status['issues'] = (self.status['issues'] + [{'sample_id': item['sample_id'], 'state': state, 'error': error}])[-25:]
        with self.store.connect() as db:
            db.execute('UPDATE generation_resume_items SET state=?,error=?,sample_id=? WHERE run_id=? AND position=?',
                       (state, error, item['sample_id'], self.status['id'], item['position']))
            db.execute('UPDATE generation_resume_runs SET status=? WHERE id=?',
                       (self.owner.clean(self.status), self.status['id']))

    async def run(self):
        owner, current = self.owner, None
        try:
            while True:
                owner.status['running'] = True
                owner.status.update(paused=not owner.resume_event.is_set(), pause_requested=not owner.resume_event.is_set())
                await asyncio.sleep(0)
                await owner.gate()
                with self.store.connect() as db:
                    row = db.execute('''SELECT position,trajectory_uuid,sample_id,job_id FROM generation_resume_items
                        WHERE run_id=? AND state='pending' ORDER BY position LIMIT 1''', (self.status['id'],)).fetchone()
                if row is None:
                    break
                current = dict(zip(('position','uuid','sample_id','job_id'), row))
                self.status['current_id'] = current['sample_id']
                self.persist()
                with self.store.connect() as db:
                    row = db.execute('SELECT sample_id FROM samples WHERE uuid=?', (current['uuid'],)).fetchone()
                if row is None:
                    self.finish_item(current, 'skipped', 'Trajectory was deleted before its turn')
                    current = None
                    continue
                current['sample_id'] = row[0]
                index = self.store.samples.index_of(row[0])
                sample = owner.state['samples'][index]
                metadata = sample.get('metadata', {})
                if metadata.get('origin') != 'automation' or metadata.get('generation_phase') != 'planning' or metadata.get('generation_job') != current['job_id']:
                    self.finish_item(current, 'skipped', 'Trajectory is no longer the queued unfinished generation')
                    current = None
                    continue
                try:
                    # No await between releasing the flag and restoring the draft.
                    # The queue task remains owner.job, so Pause/Cancel target it.
                    owner.status['running'] = False
                    # Failed restoration must not attach the previous builder
                    # to a corrupt checkpoint when the queue finally persists.
                    prior = owner.__dict__.copy()
                    prior['status'] = copy.deepcopy(owner.status)
                    try:
                        owner.resume_saved(current['sample_id'], _schedule=False)
                    except Exception:
                        owner.__dict__.clear()
                        owner.__dict__.update(prior)
                        raise
                    owner.status['batch'] = {'id': metadata.get('generation_batch', self.status['id']),
                        'target': 1, 'completed': 0, 'attempts': 1, 'attempt_limit': 1, 'state': 'running'}
                    with self.store.connect() as db:
                        db.execute("UPDATE generation_resume_items SET state='running' WHERE run_id=? AND position=?", (self.status['id'], current['position']))
                    owner.persist()
                    await owner.run_attempt()
                    current['sample_id'] = owner.status['draft_id']
                    succeeded = owner.status['phase'] == 'complete'
                    owner.status['batch'].update(completed=int(succeeded), state='complete' if succeeded else 'exhausted')
                    self.finish_item(current, 'complete' if succeeded else 'failed', None if succeeded else owner.status.get('last_error') or owner.status['phase'])
                except ValueError as error:
                    # Edited/missing/incompatible checkpoints are recoverable in
                    # the editor and must never be overwritten automatically.
                    self.finish_item(current, 'skipped', str(error))
                except Exception as error:
                    self.finish_item(current, 'failed', str(error))
                current = None
            self.status.update(state='complete', current_id=None)
            owner.status.update(phase='resume_complete', last_error=None)
        except asyncio.CancelledError:
            self.status.update(state='cancelled', current_id=None)
            owner.status['phase'] = 'cancelled'
            if current:
                with self.store.connect() as db:
                    db.execute("UPDATE generation_resume_items SET state='interrupted',error='Cancelled; checkpoint retained' WHERE run_id=? AND position=?", (self.status['id'], current['position']))
            raise
        except Exception as error:
            self.status.update(state='error', current_id=None)
            owner.status.update(phase='error', last_error=owner.clean(str(error)))
        finally:
            self.status['running'] = False
            owner.status.update(running=False, active=[], paused=False, pause_requested=False)
            self.persist()
            owner.persist()
