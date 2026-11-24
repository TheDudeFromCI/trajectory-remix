"""The Assistant prompt is a project setting; generation versions ignore it."""

from test_assistant import service

from trajectory_remix.graph import draft_assembly, prompt_defaults
from trajectory_remix.graph.models import GenerationPrompts


def test_default_matches_packaged_text():
    assert GenerationPrompts().assistant == prompt_defaults.assistant()


def test_assistant_session_uses_the_edited_prompt(tmp_path):
    from trajectory_remix.graph import assistant

    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings.model_copy(deep=True)
    settings.prompts.assistant = "Be terse."
    source = type("S", (), {"model_dump": lambda self, **k: {}, "intrinsic_hash": "h", "id": "x"})()
    from unittest.mock import patch

    with (
        patch.object(assistant, "diagnose", return_value=[]),
        patch.object(assistant, "causal_evidence", return_value={}),
    ):
        messages = assistant.payload(source, settings, [])["messages"]
    assert messages[0] == dict(role="system", content="Be terse.")


def test_editing_it_leaves_generation_prompt_version_alone(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings.model_copy(deep=True)
    before = draft_assembly.prompts(settings)["version"]
    settings.prompts.assistant = "Be terse."
    assert draft_assembly.prompts(settings)["version"] == before
