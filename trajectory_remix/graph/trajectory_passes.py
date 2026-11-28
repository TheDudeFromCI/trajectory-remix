"""Draft and Assemble on one existing trajectory, from the Editor's Draft tab.

New draft samples a proposal around the trajectory's assigned metrics (matrix_fill), assigns
the sampled values to its unset metrics, then queues the standard Draft pass; the reply
replaces ``metadata.draft``. Assemble queues the standard Assembly pass on the trajectory as
it is: the native tools edit the existing graph in place rather than starting from empty.

Both are one-item manual jobs (like a selected-source review) that freeze the settings and
plan when queued. Neither pass is segmented, and neither starts an automatic review.
"""

from copy import deepcopy

from . import draft_assembly, matrix_fill

DRAFT_ACTION, ASSEMBLE_ACTION = "draft", "assemble"
# The checkpoint phase each action runs in.
PHASES = {DRAFT_ACTION: "draft", ASSEMBLE_ACTION: "assembly"}
MANUAL_PHASES = frozenset(PHASES.values())


def operation(action, settings):
    """(endpoint role, checkpoint phase) for a Draft-tab job."""
    stage = draft_assembly.DRAFT if action == DRAFT_ACTION else draft_assembly.ASSEMBLE
    return draft_assembly.role(settings, stage), PHASES[action]


def _plan(proposal, settings):
    plan = draft_assembly.plan(proposal, settings)
    plan.pop("scaling", None)  # One whole pass: the scaling experiment is for runs only.
    return plan


def prepare_draft(sampler, record, defs, seed=None):
    """The edited source and frozen proposal for New draft, plus the metric keys it filled."""
    snapshot = matrix_fill.sample(sampler, record.source, defs, seed)
    data = deepcopy(record.source.to_dict())
    filled = matrix_fill.fill(
        data, snapshot["proposal"], defs, record.intrinsic_revision, record.intrinsic_hash
    )
    matrix_fill.attach(data, snapshot)
    return data, snapshot, filled


def proposal_of(source):
    """The trajectory's frozen proposal, or an empty one for a hand-made trajectory."""
    proposal = source.metadata.get("proposal")
    if isinstance(proposal, dict) and isinstance(proposal.get("proposal"), dict):
        return proposal["proposal"]
    return {}


def require_role(settings, role):
    if role not in settings.endpoint_roles:
        raise ValueError("Assign the Draft endpoint role in Project Settings first")


def require_draft(source):
    draft = source.metadata.get("draft")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("Write or generate a draft before assembling")


def options(store, snapshot_settings, revision, proposal, action):
    return dict(
        settings=store.frozen(snapshot_settings),
        settings_revision=revision,
        driver=draft_assembly.DRIVER,
        plan=_plan(proposal, snapshot_settings),
        pass_action=action,
    )


def in_place_note(session, source):
    """Tell Assembly the trajectory already has a graph to edit, not an empty one."""
    if not source.events:
        return
    session["messages"][1]["content"] += (
        "\n\nThis trajectory already has "
        f"{len(source.events)} events in {len(source.windows)} windows. Read them with the"
        " view tools and edit the existing graph in place so it matches the draft: keep what"
        " already fits, change or remove what does not, and add what is missing."
    )


def finished_stage(source):
    """An Unfinished trajectory becomes Pending once assembled; other stages stay."""
    stage = source.metadata.get("stage", -3)
    return 0 if stage == -3 else stage


def delta(before, record):
    """An Undo step for a server-side edit, in the shape a trajectory save returns."""
    return dict(
        id=record.source.id,
        before=before,
        after=record.source.to_dict(),
        expected_revision=record.revision,
        expected_commit=record.commit_id,
    )


def _queue(gen, ident, expected_revision, options, action):
    """One-item manual job on a saved, unlocked trajectory (like a selected-source review)."""
    with gen.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        record = gen.store._read(db, ident)
        gen.store._check_revision(ident, expected_revision, record)
        job = gen.jobs._create(db, "bulk", dict(query={}, action=action, options=options), 1)
        db.execute(
            "INSERT INTO graph_bulk_items VALUES(?,0,?,?,'pending',NULL,NULL)",
            (job, ident, record.revision),
        )
        return job


def start_draft(gen, ident, expected_revision, seed=None):
    """Assign sampled values to unset metrics, then queue the Draft pass.

    Returns the job, the Undo step for the metric assignment and the metric keys filled."""
    snapshot = gen.store.settings()
    settings = snapshot.settings
    require_role(settings, "draft")
    record = gen.store.read(ident)
    gen.store._check_revision(ident, expected_revision, record)
    defs = gen.sampler.editor()["configuration"]["definitions"]
    data, proposal, filled = prepare_draft(gen.sampler, record, defs, seed)
    before = record.source.to_dict()
    record = gen.store.replace(ident, data, expected_revision)
    gen.sampler.keep(proposal)
    job = _queue(
        gen,
        ident,
        record.revision,
        options(gen.store, settings, snapshot.revision, proposal["proposal"], DRAFT_ACTION),
        DRAFT_ACTION,
    )
    return job, delta(before, record), filled


def start_assemble(gen, ident, expected_revision):
    """Queue the Assembly pass on the trajectory's draft and current graph."""
    snapshot = gen.store.settings()
    settings = snapshot.settings
    require_role(settings, "draft")
    record = gen.store.read(ident)
    gen.store._check_revision(ident, expected_revision, record)
    require_draft(record.source)
    plan_options = options(
        gen.store, settings, snapshot.revision, proposal_of(record.source), ASSEMBLE_ACTION
    )
    return _queue(gen, ident, expected_revision, plan_options, ASSEMBLE_ACTION)


def sample_unset(metrics, ident, expected_revision, seed=None):
    """Metrics tab: fill unset category and label metrics from a matrix sample."""
    record = metrics.store.read(ident)
    defs = metrics.sampler.editor()["configuration"]["definitions"]
    data, _, filled = prepare_draft(metrics.sampler, record, defs, seed)
    # Only the metrics change here; the proposal and targets are New draft's to replace.
    metadata = deepcopy(record.source.metadata)
    metadata["metrics"] = data["metadata"]["metrics"]
    data["metadata"] = metadata
    if not filled:
        view = metrics.view(record, defs)
        view["filled"] = []
        return view
    view = metrics._commit(ident, record.source.to_dict(), data, expected_revision, defs)
    view["filled"] = filled
    return view
