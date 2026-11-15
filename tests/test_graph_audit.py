"""Small integration-review regressions; no model or workload execution."""
import pytest
from fastapi.testclient import TestClient

from trajectory_remix.graph.export import ExportOptions
from trajectory_remix.graph.storage import GraphStore
from trajectory_remix.graph.studio_api import create_studio_app


@pytest.mark.parametrize('options', [
    {'compiled': 'false'}, {'compiled': 0},
    {'include_stale': 'false'}, {'exclude_missing_split': 'false'},
    {'minimum_stage': True}, {'minimum_stage': 1.0},
    {'seed': True}, {'seed': '137'},
])
def test_export_options_reject_ambiguous_types(options):
    with pytest.raises(ValueError):
        ExportOptions(**options)


def test_bad_rest_export_options_never_reserve_job(tmp_path):
    store = GraphStore.initialize(tmp_path / 'p.sqlite')
    app = create_studio_app(store.path, artifact_root=tmp_path / 'exports')
    with TestClient(app) as client:
        client.headers['X-Remix-Token'] = app.state.csrf
        for options in ({'compiled': 'false'}, {'compiled': False, 'exclude_missing_split': 'false'},
                        {'compiled': False, 'minimum_stage': True}):
            response = client.post('/api/v1/exports', json={'options': options})
            assert response.status_code == 422, response.text
        assert client.get('/api/v1/jobs').json() == []
    assert ExportOptions(compiled=False, include_stale=False, exclude_missing_split=False,
                         minimum_stage=0, seed=137).minimum_stage == 0


def test_fresh_projects_include_editable_chat_for_default_flow(tmp_path):
    from trajectory_remix.graph.flow import default_plan
    from trajectory_remix.graph.models import ProjectSettings
    from trajectory_remix.graph.sampling import SamplerService
    from trajectory_remix.graph.jobs import JobService
    store = GraphStore.initialize(tmp_path / 'fresh.sqlite')
    settings = store.settings().settings
    assert settings.tools['chat'].input_schema['required'] == ['content']
    sampler = SamplerService(JobService(store))
    plan = default_plan(sampler.propose(seed=137)['proposal'], settings)
    assert plan['tasks'][-1]['envelope']['tool'] == 'chat'
    # Authors can deliberately remove or replace the default catalog.
    assert ProjectSettings(tools={}).tools == {}
