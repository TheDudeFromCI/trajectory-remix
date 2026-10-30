"""Explicit recovery choices for failed/skipped generation entries."""
from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid

from .llm import SampleBuilder
from .scenarios import Flow, Scenario, check_scenario_endpoints
from .storage import StorageConflict


class GenerationRecovery:
    def __init__(self, commands):
        self.commands = commands
        self.owner = commands.runner.scenario_runner
        self.store = commands.state.store
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS generation_recoveries (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, position INTEGER NOT NULL,
                trajectory_uuid TEXT NOT NULL, action TEXT NOT NULL, source TEXT NOT NULL,
                checkpoint TEXT, job_id TEXT, state TEXT NOT NULL, error TEXT, created REAL NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS generation_recovery_entry ON generation_recoveries(run_id,position,created)')
            db.execute("UPDATE generation_recoveries SET state='interrupted',error='Recovery interrupted; saved work is retained' WHERE state='running'")

    def busy(self):
        owner = self.owner
        return (owner.status['running'] or owner.legacy.status['running'] or
                getattr(owner.legacy, 'validation_runner', None) and owner.legacy.validation_runner.status.get('running') or
                getattr(owner.legacy, 'tokenizer_migration', None) and owner.legacy.tokenizer_migration.running)

    def bundle(self, sample, saved):
        if saved and saved.get('checkpoint', {}).get('sample', {}).get('uuid') == sample['uuid']:
            bundle = {k: copy.deepcopy(saved[k]) for k in ('flow', 'prompts', 'context') if k in saved}
        else:
            bundle = copy.deepcopy(sample.get('metadata', {}).get('generation_record', {}).get('requirements', {}))
        if not all(isinstance(bundle.get(k), dict) for k in ('flow','prompts','context')):
            raise ValueError('Original prompts and sampled context are unavailable; restart cannot reproduce the original inputs')
        Flow.model_validate(bundle['flow'])
        if not all(k in bundle['context'] for k in ('scenario','roles','tools')):
            raise ValueError('Original scenario context is incomplete')
        bundle['context']['steps'] = {}
        return bundle

    def view(self):
        summary = dict(self.owner.resume_queue.status)
        if not summary.get('id'):
            return summary
        # Count in SQLite and materialize at most 25 small issue descriptions.
        # Full source/checkpoint parsing belongs to an explicit recovery action.
        clause = '''
            FROM generation_resume_items i
            JOIN samples s ON s.uuid=i.trajectory_uuid
            JOIN trajectory_metadata m ON m.trajectory_uuid=s.uuid
            LEFT JOIN generation_jobs j ON j.id=json_extract(m.properties,'$.generation_job')
            LEFT JOIN generation_recoveries r ON r.id=(SELECT id FROM generation_recoveries
                WHERE run_id=i.run_id AND position=i.position ORDER BY created DESC,rowid DESC LIMIT 1)
            WHERE i.run_id=? AND i.state IN ('failed','skipped')
              AND json_extract(m.properties,'$.origin')='automation'
              AND json_extract(m.properties,'$.generation_phase')='planning'
              AND (json_extract(m.properties,'$.generation_job')=i.job_id
                   OR json_extract(m.properties,'$.generation_job')=r.job_id)
        '''
        with self.store.connect() as db:
            unresolved = db.execute('SELECT count(*) ' + clause, (summary['id'],)).fetchone()[0]
            rows = db.execute('''SELECT i.position,i.trajectory_uuid,s.sample_id,s.content_hash,i.state,i.error,
                CASE WHEN json_valid(j.snapshot) THEN json_extract(j.snapshot,'$.checkpoint.sample.uuid') END,
                CASE WHEN json_valid(j.snapshot) THEN json_type(j.snapshot,'$.flow') END,
                CASE WHEN json_valid(j.snapshot) THEN json_type(j.snapshot,'$.context') END,
                CASE WHEN json_valid(j.snapshot) THEN json_type(j.snapshot,'$.prompts') END,
                json_type(m.properties,'$.generation_record.requirements.flow'),
                json_type(m.properties,'$.generation_record.requirements.prompts'),
                json_type(m.properties,'$.generation_record.requirements.context'),
                r.id,r.state,r.error
                ''' + clause + ' ORDER BY i.position DESC LIMIT 25', (summary['id'],)).fetchall()
        issues = []
        for row in reversed(rows):
            (position, ident, label, digest, state, error, checkpoint_uuid,
             flow, context, prompts, archived_flow, archived_prompts, archived_context,
             recovery_id, recovery_state, recovery_error) = row
            has_bundle = flow == context == prompts == 'object' and checkpoint_uuid == ident
            can_restart = has_bundle or archived_flow == archived_prompts == archived_context == 'object'
            issues.append({'position': position, 'uuid': ident, 'sample_id': label,
                'source_hash': digest, 'state': state, 'error': recovery_error or error,
                'recovery_id': recovery_id, 'recovery_state': recovery_state,
                'actions': {'force_resume': has_bundle, 'restart': can_restart, 'delete': True},
                'action_note': None if has_bundle else 'The original checkpoint is missing or incomplete. Only actions supported by the archived inputs are available.'})
        summary.update(issues=issues, attention_count=unresolved)
        return summary

    def archive(self, ident):
        with self.store.connect() as db:
            row = db.execute('SELECT id,action,source,checkpoint,job_id,state,error FROM generation_recoveries WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('Recovery archive not found')
        return dict(zip(('id','action','source','checkpoint','job_id','state','error'),
                        (row[0],row[1],json.loads(row[2]),json.loads(row[3]) if row[3] else None,*row[4:])))

    def update(self, ident, state, error=None):
        with self.store.connect() as db:
            db.execute('UPDATE generation_recoveries SET state=?,error=? WHERE id=?',
                       (state, self.owner.clean(error) if error else None, ident))

    def start(self, run_id, position, action, source_hash):
        if action not in ('force_resume','restart','delete'):
            raise ValueError('Choose force_resume, restart or delete')
        if type(position) is not int or position < 0 or not isinstance(run_id, str) or not isinstance(source_hash, str) or not source_hash:
            raise ValueError('A current issue and source_hash are required')
        if self.busy():
            raise ValueError('Finish or stop generation, validation or tokenizer migration before recovery')
        if run_id != self.owner.resume_queue.status.get('id'):
            raise StorageConflict('Resume results changed; reload the current issues')
        revision = self.owner.state['revision']
        with self.store.connect() as db:
            row = db.execute('''SELECT i.trajectory_uuid,i.job_id,s.sample_id,s.source,s.content_hash,
                (SELECT job_id FROM generation_recoveries r WHERE r.run_id=i.run_id AND r.position=i.position ORDER BY created DESC,rowid DESC LIMIT 1)
                FROM generation_resume_items i LEFT JOIN samples s ON s.uuid=i.trajectory_uuid
                WHERE i.run_id=? AND i.position=? AND i.state IN ('failed','skipped')''', (run_id, position)).fetchone()
        if row is None or row[2] is None:
            raise ValueError('This issue no longer has a saved trajectory')
        ident, old_job, label, raw, digest, recovery_job = row
        if digest != source_hash:
            raise StorageConflict('Trajectory changed; reload the issue before choosing a recovery action')
        sample = json.loads(raw)
        metadata = sample.get('metadata', {})
        if (metadata.get('origin') != 'automation' or metadata.get('generation_phase') != 'planning'
                or metadata.get('generation_job') not in (old_job, recovery_job)):
            raise ValueError('This is no longer the unfinished generated trajectory from this issue')
        with self.store.connect() as db:
            checkpoint = db.execute('SELECT snapshot FROM generation_jobs WHERE id=?', (metadata['generation_job'],)).fetchone()
        saved = None
        if checkpoint:
            try:
                saved = json.loads(checkpoint[0])
            except (ValueError, TypeError):
                pass
        if action == 'force_resume' and (not saved or saved.get('checkpoint', {}).get('sample', {}).get('uuid') != ident):
            raise ValueError('Force resume requires the original checkpoint owned by this trajectory')
        bundle = self.bundle(sample, saved) if action == 'restart' else None
        recovery_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4()) if action == 'restart' else metadata['generation_job']
        with self.store.connect() as db:
            db.execute('INSERT INTO generation_recoveries VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (recovery_id, run_id, position, ident, action, self.owner.clean(sample),
                 self.owner.clean(saved) if saved else None, job_id, 'running', None, time.time()))
        owner = self.owner
        prior = owner.__dict__.copy()
        prior['status'] = copy.deepcopy(owner.status)
        try:
            if action == 'delete':
                self.commands._editable(revision, label)
                updated = owner.state['samples'].copy()
                updated.revision = revision
                updated.pop(self.store.samples.index_of(label))
                owner.history.commit(updated, f'Delete generation draft: {label}')
                self.update(recovery_id, 'deleted')
                return {'recovery_id': recovery_id, 'state': 'deleted'}
            if action == 'restart':
                owns = saved and saved.get('checkpoint', {}).get('sample', {}).get('uuid') == ident
                self.prepare_restart(sample, saved if owns else None, bundle, job_id, recovery_id)
            else:
                owner.resume_saved(label, force=True, _schedule=False)
                owner.status.update(activity='force-resume')
                owner.expected_draft = copy.deepcopy(sample)
                owner.snapshot.setdefault('recovery_history', []).append(recovery_id)
                owner.builder.sample['metadata'].setdefault('generation_recoveries', []).append(recovery_id)
            owner.status['batch'] = {'id': metadata.get('generation_batch', job_id), 'target': 1,
                'completed': 0, 'attempts': 1, 'attempt_limit': 1, 'state': 'running'}
            owner.status['recovery_id'] = recovery_id
            owner.publish()
            owner.active_recovery_id = recovery_id
            owner.job = asyncio.create_task(self.run(recovery_id))
            return {'recovery_id': recovery_id, 'state': 'running'}
        except Exception as error:
            owner.__dict__.clear()
            owner.__dict__.update(prior)
            self.update(recovery_id, 'failed', str(error))
            raise

    def prepare_restart(self, sample, saved, bundle, job_id, recovery_id):
        owner = self.owner
        flow = Flow.model_validate(bundle['flow'])
        config = copy.deepcopy(owner.state['config'])
        name = (saved or {}).get('status', {}).get('scenario') or sample['metadata']['scenario']
        check_scenario_endpoints(name, flow, config['endpoints'], content_first=flow.generation_mode == 'content-first')
        builder = SampleBuilder(owner.compiler, config)
        original = (saved or {}).get('checkpoint', {}).get('sample', sample)
        builder.sample.update({k: copy.deepcopy(original[k]) for k in ('schema-version','tick-policy','defaults') if k in original})
        builder.sample.update(id=sample['id'], uuid=sample['uuid'])
        metadata = copy.deepcopy(original.get('metadata', {}))
        for key in ('concept','generation_record','generation_metrics','generation_elapsed_seconds','evaluations','approval','evaluation_error','external_reviews','external_repairs'):
            metadata.pop(key, None)
        metadata.update(origin='automation', validation_level=0, generation_phase='planning', generation_job=job_id,
            generation_mode=flow.generation_mode, scenario=name,
            roles=copy.deepcopy(bundle['context']['roles']), context_tags=copy.deepcopy(bundle['context']['scenario']['tags']))
        metadata['generation_recoveries'] = [*sample['metadata'].get('generation_recoveries', []), recovery_id]
        builder.sample['metadata'] = metadata
        builder.add_stream('system', flow.system_prompt, op='append', source='system')
        owner.compiler.compile(builder.sample)
        owner.scenario = object.__new__(Scenario)
        owner.scenario.name, owner.scenario.flow, owner.scenario.prompts = name, flow, bundle['prompts']
        owner.config, owner.context, owner.builder = config, bundle['context'], builder
        owner.endpoints = {e['id']: e for e in config['endpoints']}
        owner.status.update(running=True, phase='generating', activity='restart', paused=False, pause_requested=False,
            active=[], completed=0, total=0, draft_id=sample['id'], scenario=name,
            model_requests=0, completed_model_seconds=0, total_model_seconds=0, metrics={}, last_error=None,
            attempts=owner.status.get('attempts', 0)+1)
        owner.snapshot = {'id': job_id, 'flow': flow.model_dump(), 'prompts': bundle['prompts'], 'context': owner.context,
            'steps': {}, 'status': owner.status, 'logs': [], 'retain_identifier': True,
            'tool_fingerprint': owner.compiler.tools.fingerprint, 'tokenizer_fingerprint': owner.compiler.fingerprint,
            'max_parallel': (saved or {}).get('max_parallel', config.get('single_generation', {}).get('max_parallel', 1)),
            'recovery_history': [recovery_id]}
        owner.expected_draft = copy.deepcopy(sample)
        owner.state_override = None
        owner.manual_tags = (saved or {}).get('manual_tags')
        owner.first_prompts, owner.first_prompt_paths = {}, {}
        owner.limit = asyncio.Semaphore(owner.snapshot['max_parallel'])
        owner.resume_event.set()

    async def run(self, recovery_id):
        owner = self.owner
        try:
            await owner.run_attempt()
            success = owner.status['phase'] == 'complete'
            owner.status['batch'].update(state='complete' if success else 'exhausted', completed=int(success))
            self.update(recovery_id, 'complete' if success else 'failed', None if success else owner.status.get('last_error') or owner.status['phase'])
        except asyncio.CancelledError:
            self.update(recovery_id, 'interrupted', 'Recovery cancelled; draft and checkpoint retained')
            raise
        except Exception as error:
            self.update(recovery_id, 'failed', str(error))
            owner.status.update(phase='error', last_error=owner.clean(str(error)))
        finally:
            owner.active_recovery_id = None
            owner.status.update(running=False, active=[])
            owner.persist()
