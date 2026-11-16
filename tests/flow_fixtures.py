"""A small fixed Flow for interpreter-mechanics tests, independent of the packaged default."""

MINIMAL_FLOW = dict(
    version="adaptive-flow/1",
    start="choose",
    max_steps=600,
    max_events=128,
    actor_instruction="Write exactly this next action from the visible evidence only.",
    nodes=dict(
        choose=dict(kind="select", instruction="Choose the next event or finish.", next="finished"),
        finished=dict(kind="branch", path="finished", equals=True, then="done", otherwise="write"),
        write=dict(kind="event", instruction="Author the selected event.", next="choose"),
        done=dict(kind="end"),
    ),
)
