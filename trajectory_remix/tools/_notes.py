"""Canonical prioritized task lists for the persistent notes context window."""

import json

PRIORITIES = ("critical", "very high", "high", "medium", "low", "very low")
STATUSES = ("not started", "doing", "paused", "blocked", "done")


def grouped_notes(notes):
    """Return every priority bucket, ordered from most to least urgent."""
    grouped = {priority: {} for priority in PRIORITIES}
    for name in sorted(notes):
        note = notes[name]
        grouped[note["priority"]][name] = {
            "description": note["description"],
            "status": note["status"],
            "blocked_by": list(note.get("blocked_by", [])),
        }
    return grouped


def notes_json(notes):
    """Serialize the same JSON used by note_list and the notes window."""
    return json.dumps(grouped_notes(notes), ensure_ascii=False, indent=2)
