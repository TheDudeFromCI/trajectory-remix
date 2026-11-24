"""Draft replies are plain text, wrapped into the trajectory in code."""

import json

import pytest

from trajectory_remix.graph import prompt_defaults
from trajectory_remix.graph.draft_reply import MIN_LENGTH, draft_text
from trajectory_remix.graph.models import Endpoint, GenerationPrompts

NOTES = "Working notes for the episode. " * 10


def test_plain_text_is_the_draft():
    assert draft_text({"text": "\n" + NOTES + "\n"}) == NOTES.strip()


def test_a_json_wrapper_from_habit_is_unwrapped():
    assert draft_text({"text": json.dumps({"draft": NOTES})}) == NOTES.strip()


def test_other_json_is_kept_as_notes():
    text = json.dumps({"notes": NOTES})
    assert draft_text({"text": text}) == text


def test_results_saved_before_plain_text_drafts_still_read():
    assert draft_text({"output": {"draft": NOTES}}) == NOTES.strip()


@pytest.mark.parametrize("result", [{"text": "too short"}, {"text": None}, {}])
def test_unusable_replies_explain_the_correction(result):
    with pytest.raises(ValueError, match="Draft"):
        draft_text(result)


def test_minimum_length_matches_the_prompt():
    assert MIN_LENGTH == 200


def test_saved_output_mode_is_dropped_from_endpoints():
    endpoint = Endpoint.model_validate(
        dict(url="http://127.0.0.1:8080/v1", model="local", output_mode="json_object")
    )
    assert "output_mode" not in endpoint.model_dump()


def test_the_shipped_json_draft_prompt_upgrades_to_plain_text():
    current = prompt_defaults.draft()
    deliverable = next(line for line in current.split("\n\n") if line.startswith("**Deliverable"))
    old = current.replace(
        deliverable,
        '**Deliverable:** one JSON object with a "draft" string of at least 200 characters.',
    )
    assert GenerationPrompts(draft=old).draft == current
    assert "one JSON object" not in current
