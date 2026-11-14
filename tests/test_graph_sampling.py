"""Small deterministic original/adaptor/registry checks. No distribution sampling."""
import copy
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.models import GraphSource, ProjectSettings, digest
from trajectory_remix.graph.storage import GraphStore, RevisionConflict
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.sampling import ROOT, SamplerService, default_matrix, original
from trajectory_remix.graph.metrics import MetricService
from trajectory_remix.graph.export import resolve_splits
from trajectory_remix.graph.studio_api import create_studio_app


@pytest.fixture
def services(tmp_path):
    store = GraphStore.initialize(tmp_path / 'test.sqlite')
    sampler = SamplerService(JobService(store))
    return store, sampler, MetricService(sampler)


def source(metadata=None):
    return GraphSource(format="kidra-trajectory-graph/1", id=str(uuid4()), windows={'w': {'purpose': 'reasoning', 'agent_writable': True}},
        events={'a': {'kind': 'agent_text', 'window': 'w', 'content': 'a'}}, metadata=metadata or {})


def test_originals_and_reference():
    expected = {'kidra_sampler.py': 'e00a66f5be1131f83f390ff8784edc6d2207202262cea5a1a51b833b582b175a',
                'kidra_sampling_matrix.v1.json': '59e30228558b9db8f19e73b782be6070589a1dc6a241555a400286fcdc1bb853',
                'kidra_example_definition.json': '952a3d5c9468df8225e54a689305e576c0099f8388c67d29543dc32629d120c7'}
    for file, sha in expected.items():
        assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == sha
    m = default_matrix()
    assert digest(m) == '5e41e190ff9349f57411461ba5626126ff9466a9b27da3c4fea8449c5ed28ff7'
    e = json.loads((ROOT / 'kidra_example_definition.json').read_text())
    p = e['sampling_provenance']
    assert original.sample_one(m, p['seed'], p['world_group_id'], p['requested_overrides']) == e


def test_identity_pins_and_immutable_snapshots(services):
    store, s, _ = services
    a = s.propose(seed=137, world_group_id='same-world')
    b = s.propose(seed=137, world_group_id='same-world', overrides={'domain': 'software_engineering'})
    assert a['id'] != b['id']
    assert a['proposal']['definition_id'] == b['proposal']['definition_id']
    assert a['payload_digest'] != b['payload_digest']
    assert b['proposal']['metrics']['sampling']['domain'] == 'software_engineering'
    assert s.read(a['id']) == a
    a['proposal']['status'] = 'retired'
    assert s.read(a['id'])['proposal']['status'] == 'proposed'
    for overrides in ({'unknown': 'x'}, {'complexity': 'impossible'}, {'user_tone': 2},
                      {'focus.family': 'strategic_planning', 'lifecycle': 'closure', 'complexity': 'trivial'}):
        with pytest.raises(ValueError):
            s.propose(seed=1, overrides=overrides)
    for pins in ({'complexity': 'medium'}, {'user_patience': 'impatient'}):
        expected = original.sample_one(default_matrix(), 137, 'fixed', pins)
        assert s.propose(seed=137, world_group_id='fixed', overrides=pins)['proposal'] == expected
    assert s.propose(seed=1)['family_id'] is None
    with store.connect() as db, pytest.raises(Exception, match='immutable'):
        db.execute('UPDATE graph_proposals SET snapshot=?', ('{}',))


def test_registry_future_runs_and_targets(services):
    store, s, metrics = services
    frozen = s.editor()
    assert frozen['configuration']['sampler_sha256'] == 'e00a66f5be1131f83f390ff8784edc6d2207202262cea5a1a51b833b582b175a'
    job = s.run([{'seed': 137}, {'seed': 9, 'world_group_id': 'shared'}], {'prompt': 'frozen'})
    attempts = s.jobs.attempts(job)
    m = default_matrix(); m['dimensions']['user_tone'][0]['description'] = 'Edited'
    current = s.update(m, {'authored_windows': {'tolerance': {'absolute': 0, 'relative': .05}}}, 1)
    assert current['configuration']['matrix_digest'] != frozen['configuration']['matrix_digest']
    assert store.settings().settings.matrix.digest == digest(m)
    assert store.settings().settings.metric_registry.digest == current['configuration']['registry_digest']
    with pytest.raises(RevisionConflict):
        s.update(m, {}, 1)
    assert s.jobs.attempts(job) == attempts
    for attempt in attempts:
        assert attempt['proposal']['frozen'] == frozen
    snap = attempts[1]['proposal']
    r = store.create(source(s.metadata(snap)))
    assert r.source.metadata['family_id'] == 'shared'
    assert r.source.metadata['targets'] == snap['proposal']['metrics']
    assert s.propose(seed=137, frozen=frozen)['frozen'] == frozen
    defs = current['configuration']['definitions']
    assert [x['id'] for x in m['dimensions']['user_tone']] == defs['user_tone']['choices']
    assert metrics.view(r, defs)['values']['actual_outcome']['status'] == 'unavailable'


def test_observations_provenance_unknown_stale_and_drift(services):
    store, s, metrics = services
    c = s.update(default_matrix(), {'authored_windows': {'target': 'window_target', 'tolerance': {'absolute': 0, 'relative': .1}}}, 1)['configuration']
    r = store.create(source({'targets': {'numeric_targets': {'window_target': 2}}}))
    values = metrics.calculate(r, c)
    assert values['raw_event_fragments']['value'] == 1
    assert 'actual_outcome' not in values and 'peak_active_windows' not in values
    data = r.source.to_dict(); data['metadata']['metrics'] = values
    r = store.replace(r.source.id, data, 1)
    v = metrics.view(r, c['definitions'])['values']
    assert v['authored_windows']['status'] == 'available' and v['authored_windows']['drift'] is True
    assert values['authored_windows']['source_revision'] == 1
    with pytest.raises(ValueError, match='profile'):
        metrics.annotate(r.source.id, 2, 'observed_context_tokens', 2, 'export')
    annotated = metrics.annotate(r.source.id, 2, 'observed_context_tokens', 3, 'export', 'profile-sha', 'compiler-v')
    assert annotated['values']['observed_context_tokens']['status'] == 'available'
    r = store.read(r.source.id); data = r.source.to_dict(); data['events']['a']['content'] = 'changed'
    r = store.replace(r.source.id, data, r.revision)
    assert metrics.view(r, c['definitions'])['values']['authored_windows']['status'] == 'stale'


def test_population_and_missing_bulk_retains_annotations(services):
    store, s, metrics = services
    snap = s.propose(seed=137)
    meta = s.metadata(snap); meta['stage'] = 1
    r = store.create(source(meta))
    from trajectory_remix.graph.bulk import BulkService
    bulk = BulkService(s.jobs)
    frozen = s.editor()
    assert frozen['configuration']['sampler_sha256'] == 'e00a66f5be1131f83f390ff8784edc6d2207202262cea5a1a51b833b582b175a'
    frozen = frozen['configuration']
    job = bulk.start({}, 'metrics', frozen)
    bulk.advance(job, {'metrics': metrics.calculate})
    assert s.jobs.inspect(job)['state'] == 'completed'
    report = metrics.population({}, 'accepted')
    assert report['total'] == 1 and report['proposal_coverage']['included_records'] == 1
    assert report['unavailable']['observed_context_tokens'] == 1
    assert report['unavailable']['raw_event_fragments'] == 0
    assert store.read(r.source.id).source.metadata['targets'] == meta['targets']
    assert len(bulk.history(job)) == 1
    again = bulk.start({}, 'metrics', frozen); bulk.advance(again, {'metrics': metrics.calculate})
    assert bulk.history(again) == []


def test_families_auto_missing_and_balancing():
    settings = ProjectSettings(split_ratios={'train': .5, 'development': .25, 'test': .25})
    rows = [source({'family_id': 'siblings', 'split': 'Train'}), source({'family_id': 'siblings', 'split': 'Auto'}),
            source({'split': 'Auto'}), source()]
    splits, warnings = resolve_splits(rows, settings)
    assert splits[rows[0].id] == splits[rows[1].id] == 'train'
    assert set(splits.values()) == {'train', 'development', 'test'}
    assert len(warnings) == 1
    assert rows[3].id not in resolve_splits(rows, settings, exclude_missing=True)[0]
    assert rows[2].id in resolve_splits(rows, settings, exclude_missing=True)[0]
    rows[1].metadata['split'] = 'Test'
    with pytest.raises(ValueError, match='Conflicting explicit family'):
        resolve_splits(rows, settings)


def test_shared_rest_editor_and_metrics(services, tmp_path, monkeypatch):
    store, _, _ = services
    from trajectory_remix.graph.compiler import GraphCompiler
    monkeypatch.setattr(GraphCompiler, '__init__', lambda *a, **kw: pytest.fail('compiled'))
    with TestClient(create_studio_app(store.path, artifact_root=tmp_path / 'exports')) as c:
        c.headers['X-Remix-Token'] = c.app.state.csrf
        editor = c.get('/api/v1/sampler').json()
        r = c.post('/api/v1/sampler/proposals', json={'seed': 137})
        assert r.status_code == 201
        assert c.get('/api/v1/sampler/proposals/' + r.json()['id']).json() == r.json()
        assert c.post('/api/v1/sampler/proposals', json={'overrides': {'domain': 'bad'}}).status_code == 422
        edited = c.put('/api/v1/sampler', json={'expected_revision': 1, 'matrix': editor['configuration']['matrix']})
        assert edited.status_code == 200
        assert c.put('/api/v1/sampler', json={'expected_revision': 1, 'matrix': editor['configuration']['matrix']}).status_code == 409
        assert c.post('/api/v1/metrics/population', json={}).json()['total'] == 0
        src = source(); store.create(src)
        assert c.get('/api/v1/metrics/trajectories/' + src.id).json()['values']['raw_event_fragments']['status'] == 'unavailable'
        assert c.post('/api/v1/metrics/calculate-missing', json={}).status_code == 202


@pytest.mark.asyncio
async def test_metric_worker_shutdown_completion_race():
    import asyncio
    from trajectory_remix.graph.job_api import JobWorker
    from trajectory_remix.graph.jobs import OwnershipConflict
    task = asyncio.get_running_loop().create_future()
    class CompletingJobs:
        state = 'running'
        def inspect(self, job):
            return {'state': self.state}
        def control(self, job, action):
            self.state = 'completed'
            task.set_result(None)
            raise OwnershipConflict('Job is terminal')
    worker = JobWorker(CompletingJobs(), None, None)
    worker.tasks['metrics'] = task
    await worker.close()
