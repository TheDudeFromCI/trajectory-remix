from trajectory_remix.graph.models import ProjectSettings
from trajectory_remix.graph.size_labels import default_size_labels, nearest_size

LABELS = default_size_labels()


def test_defaults_are_ordered_and_binary():
    assert [x["name"] for x in LABELS][:3] == ["XXXS", "XXS", "XS"]
    assert LABELS[0]["total_tokens"] == 4096 and LABELS[-1]["total_tokens"] == 1024 * 1024
    assert LABELS[0]["agent_tokens"] == 256


def test_nearest_uses_log2_distance():
    assert nearest_size(0, LABELS) == "XXXS"
    assert nearest_size(4096, LABELS) == "XXXS"
    assert nearest_size(5000, LABELS) == "XXXS"
    assert nearest_size(6200, LABELS) == "XXS"  # log2 midpoint is 4096*sqrt(2) ~ 5793
    assert nearest_size(10**9, LABELS) == "XXXL"
    assert nearest_size(10, []) is None


def test_settings_default_and_unique_names():
    assert len(ProjectSettings().size_labels) == 9
    dup = [
        dict(name="A", agent_tokens=1, total_tokens=2),
        dict(name="A", agent_tokens=1, total_tokens=4),
    ]
    try:
        ProjectSettings(size_labels=dup)
    except ValueError:
        return
    raise AssertionError("duplicate names accepted")


def test_token_estimate_uses_three_and_a_half_chars_per_token():
    from trajectory_remix.graph.size_labels import estimate_tokens

    assert estimate_tokens(7) == 2 and estimate_tokens(8) == 3 and estimate_tokens(0) == 0
