"""The Assistant instructions are a project prompt; generation versions ignore them."""

from test_assistant import service

from trajectory_remix.graph import draft_assembly, endpoint_prompts, prompt_defaults
from trajectory_remix.graph.models import GenerationPrompts


def test_default_matches_packaged_text():
    assert GenerationPrompts().assistant == prompt_defaults.assistant()


def test_message_opens_with_the_instructions_and_keeps_the_reply_contract():
    comments = [dict(id="c1", target=dict(kind="trajectory", id="t"), body="Fix it", replies=[])]
    text = endpoint_prompts.assistant_user("Be terse.", comments, "t")
    assert text.startswith("Be terse.\n\n| ID | Target |")
    assert text.endswith(endpoint_prompts.ASSIST_AFTER)


def test_editing_them_leaves_the_generation_prompt_version_alone(tmp_path):
    gen, _ = service(tmp_path)
    settings = gen.store.settings().settings.model_copy(deep=True)
    before = draft_assembly.prompts(settings)["version"]
    settings.prompts.assistant = "Be terse."
    assert draft_assembly.prompts(settings)["version"] == before
