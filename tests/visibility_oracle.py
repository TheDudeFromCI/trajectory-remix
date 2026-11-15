"""Historical phase semantics retained only as a compiler regression oracle."""


def visible_to(query: dict, key: dict, windows: list[dict]) -> bool:
    """Exact causal attention predicate for a target query, before target commit.

    Loss masks DO NOT change this predicate. NOOP is a decision, never a KV key.
    """
    if not key["durable"]:
        return False
    removed = key.get("removed_at")
    if removed and (
        query["tick"] > removed["tick"]
        or query["tick"] == removed["tick"]
        and (
            query["phase"] == "D"
            or query["phase"] == "B"
            and query["input_rank"] >= removed["input_rank"]
        )
    ):
        return False
    life = next(w for w in windows if w["id"] == key["window"])
    if life["open"] > query["tick"] or (
        life["close"] is not None and life["close"] < query["tick"]
    ):
        return False
    if key["tick"] < query["tick"]:
        return True
    if key["tick"] > query["tick"]:
        return False
    if query["phase"] == "D":
        return key["phase"] in ("A", "B")
    if query["phase"] == "B":
        return key["phase"] == "A" or (
            key["phase"] == "B" and key["input_rank"] < query["input_rank"]
        )
    return False
