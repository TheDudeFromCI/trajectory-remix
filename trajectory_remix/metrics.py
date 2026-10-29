"""Source metrics from compiler records, with bounded, disposable SQLite caches.

Source, export, and actual trainer exposure are deliberately separate populations.
Semantic labels are observations supplied by reviewers, never inferred from tags.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections import Counter, defaultdict
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import yaml

from .compiler import CompileError

DOMAINS = ("software", "scheduling", "organization", "research", "writing", "general_assistance")
OUTCOMES = ("completed", "partial", "blocked", "clarified", "cancelled", "declined")
REQUIRED = ("primary_domain", "terminal_outcome", "scenario_family_id", "template_family_id", "split", "is_original", "provenance")
POPULATIONS = {
    "all": "1",
    "accepted": "json_extract(m.properties,'$.validation_level')=3",
    "accepted_train_originals": "json_extract(m.properties,'$.validation_level')=3 AND json_extract(m.properties,'$.split')='train' AND json_type(m.properties,'$.is_original')='true'",
    "development": "json_extract(m.properties,'$.split')='development'",
}
COUNT_EDGES = [0, 1, 2, 4, 8, 16, 32, 64, 256, 1024, 4096, 16384, 65536]
REGISTRY = {
    "duration_ticks": "Last execution tick minus first execution tick plus one; ticks are not seconds.",
    "token_rows": "All compiler token records, including headers, boundaries and idle decisions.",
    "generated_content_tokens": "Phase D tokens excluding BOS, EOS and NOOP.",
    "external_content_tokens": "Phase A/B tokens excluding BOS, EOS and NOOP; includes compiler headers.",
    "bos_tokens": "BOS records, whether supervised or masked.",
    "eos_tokens": "EOS records, whether supervised or masked.",
    "noop_tokens": "Explicit and implicit NOOP records.",
    "supervised_tokens": "Records with positive loss weight.",
    "masked_tokens": "Records with zero loss weight.",
    "loss_weight_sum": "Raw compiler target weights; before any trainer normalization.",
    "noop_loss_weight_fraction": "NOOP raw target weight / all raw target weight; null if denominator is zero.",
    "eos_loss_weight_fraction": "EOS raw target weight / all raw target weight; null if denominator is zero.",
    "generated_external_ratio": "Model content / external content, excluding controls; null if no external content.",
    "generated_content_fraction": "Model content / all content, excluding controls; null if no content.",
    "window_instances": "Compiler window lifetimes, including implicit tool/task windows and empty windows.",
    "mean_window_length": "Per-source mean durable window length; aggregate means are macro averages, separate from pooled window lengths.",
    "peak_open_windows": "Peak windows open immediately before phase D decisions (EOS closes after that decision).",
    "peak_visible_tokens": "Peak durable tokens visible before phase D decisions, respecting same-tick phases, replacement and closure.",
    "peak_generators": "Peak distinct windows producing same-tick phase D content, excluding boundary and NOOP tokens.",
    "peak_external_streams": "Peak distinct windows receiving same-tick phase B content, excluding boundary and NOOP tokens.",
    "tool_invocations": "Executed visible and invisible tool calls; repeated calls count separately.",
    "tool_failures": "Tool outcomes with ok=false; a failure is not an incorrect demonstration.",
    "warnings": "Compiler diagnostic warnings; not an admission verdict.",
}


@lru_cache(maxsize=1)
def launch_config():
    return yaml.safe_load(Path(__file__).with_name("coverage_launch.yaml").read_text())


@lru_cache(maxsize=1)
def semantic_selectors():
    selectors = set()
    for rule in launch_config()["coverage_rules"]:
        selector = rule["selector"]
        for value in selector.get("all", []) if isinstance(selector, dict) else [selector]:
            if value.startswith(("capability.", "information.", "interaction.", "tool_condition.", "user.")):
                selectors.add(value)
    return sorted(selectors)


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def cache_key(compiler):
    # Definition and compiler changes invalidate persisted values across restarts.
    return hashlib.sha256(json.dumps([
        implementation_hash(), compiler.fingerprint, compiler.ids,
        compiler.max_window_slots, compiler.slot_policy, compiler.tools.fingerprint,
    ], sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def implementation_hash():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    # Context/transport/UI changes do not change compiled numeric records.
    # Hash execution dependencies and the actual calculation definitions.
    for path in sorted([*(root / name for name in ("compiler.py", "sandbox_replay.py", "dataset.py", "storage.py", "coverage_launch.yaml")), *root.glob("tools/*.py")]):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    for function in (measure, ratio, execution_hash):
        digest.update(inspect.getsource(function).encode())
    digest.update(json.dumps(REGISTRY, sort_keys=True).encode())
    digest.update(b"numeric-cache-v2")
    return digest.hexdigest()


def measure(plan, compiler):
    rows, windows, calls = plan["rows"], plan["windows"], plan["tool_calls"]
    tokens, weights, tools = Counter(), Counter(), Counter()
    generators, streams, changes = defaultdict(set), defaultdict(set), Counter()
    lengths, categories, idle_runs = Counter(), Counter(), []
    last_idle, runs = {}, Counter()
    lives = {w["id"]: w for w in windows}
    visible_changes, decision_ticks = Counter(), set()
    streaming = {e["path"] for e in plan["events"] if e["delivery"] == "stream"}
    first = min([r["tick"] for r in rows] + [c["start"] for c in calls])
    duration = plan["max_tick"] - first + 1
    for row in rows:
        kind = next((name for name, tid in compiler.ids.items() if tid == row["token_id"]), "content")
        tokens[kind] += 1
        tokens["supervised" if row["loss_mask"] else "masked"] += 1
        weights[kind] += row["loss_weight"]
        if kind == "content":
            tokens["generated" if row["phase"] == "D" else "external"] += 1
            if row["phase"] == "D" or row["phase"] == "B" and row["event"] in streaming:
                (generators if row["phase"] == "D" else streams)[row["tick"]].add(row["window"])
        if row["durable"]:
            lengths[row["window"]] += 1
            # This is visible_to's phase-D frontier expressed as intervals.
            # D tokens commit after all same-tick D decisions; EOS closes after
            # its own decision. A phase-B replacement removes old keys first.
            start = row["tick"] + (row["phase"] == "D")
            close = lives[row["window"]]["close"]
            end = min(close + 1 if close is not None else plan["max_tick"] + 1,
                      row.get("removed_at", {}).get("tick", plan["max_tick"] + 1))
            if start < end:
                visible_changes[start] += 1
                visible_changes[end] -= 1
        wid = row["window"]
        if row["phase"] == "D":
            decision_ticks.add(row["tick"])
            if kind == "noop":
                if last_idle.get(wid) != row["tick"] - 1 and runs[wid]:
                    idle_runs.append(runs.pop(wid))
                runs[wid] += 1
                last_idle[wid] = row["tick"]
            elif runs[wid]:
                idle_runs.append(runs.pop(wid))
    idle_runs.extend(n for n in runs.values() if n)
    for window in windows:
        changes[window["open"]] += 1
        if window["close"] is not None:
            changes[window["close"] + 1] -= 1
        categories[window["type"]] += 1
    opened = peak = 0
    for tick in sorted(changes):
        opened += changes[tick]
        peak = max(peak, opened)
    visible = peak_visible = 0
    for tick in sorted(set(visible_changes) | decision_ticks):
        visible += visible_changes[tick]
        if tick in decision_ticks:
            peak_visible = max(peak_visible, visible)
    for call in calls:
        tools[call["tool"]] += 1
    total_weight = sum(weights.values())
    concurrency = {}
    for name, activity in (("model", generators), ("external", streams)):
        counts = Counter(min(len(value), 3) for value in activity.values())
        counts[0] += duration - len(activity)
        concurrency[name] = [{"generators": str(n) if n < 3 else "3+", "ticks": counts[n],
                              "fraction": ratio(counts[n], duration)} for n in range(4)]
    return {
        "duration_ticks": duration, "token_rows": len(rows),
        "generated_content_tokens": tokens["generated"], "external_content_tokens": tokens["external"],
        **{name + "_tokens": tokens[name] for name in ("bos", "eos", "noop", "supervised", "masked")},
        "loss_weight_sum": total_weight,
        "noop_loss_weight_fraction": ratio(weights["noop"], total_weight),
        "eos_loss_weight_fraction": ratio(weights["eos"], total_weight),
        "generated_external_ratio": ratio(tokens["generated"], tokens["external"]),
        "generated_content_fraction": ratio(tokens["generated"], tokens["content"]),
        "window_instances": len(windows), "peak_open_windows": peak,
        "mean_window_length": ratio(sum(lengths.values()), len(windows)),
        "peak_visible_tokens": peak_visible,
        "peak_generators": max(map(len, generators.values()), default=0),
        "peak_external_streams": max(map(len, streams.values()), default=0),
        "tool_invocations": len(calls), "tool_failures": sum(not c["outcome"]["ok"] for c in calls),
        "warnings": len(plan["diagnostics"]), "tools": tools, "window_categories": categories,
        "window_lengths": [lengths[w["id"]] for w in windows],
        "idle_runs": idle_runs, "concurrency": concurrency,
        "tool_latency_ticks": [c["end"] - c["start"] for c in calls],
        "raw_loss_weights": weights,
        "event_ids": sorted({e["path"] for e in plan["events"]} | {c["path"] for c in calls}),
    }


def pending(store, compiler):
    with store.connect() as db:
        return db.execute("""SELECT count(*) FROM samples s LEFT JOIN trajectory_metrics c
            ON c.trajectory_uuid=s.uuid WHERE c.source_hash IS NOT s.content_hash OR c.cache_key IS NOT ?""",
                          (cache_key(compiler),)).fetchone()[0]


def execution_hash(source):
    """Coverage annotations and review bookkeeping do not change execution."""
    value = dict(source)
    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        metadata = {k: v for k, v in metadata.items() if k not in
                    {*REQUIRED, "observed_labels", "validation_level", "evaluations", "approval"}}
        if metadata:
            value["metadata"] = metadata
        else:
            value.pop("metadata", None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refresh(store, compiler, limit=25, *, cancel=None, progress=None):
    """Compile one source at a time. Never hold a project write lock to compile."""
    key, processed, reused, stored, position = cache_key(compiler), 0, 0, 0, -1
    revision = store.info()[0]
    while processed < limit and not (cancel and cancel.is_set()):
        # Short read transactions release WAL snapshots between samples. A long
        # compilation never holds a reader open across the whole project.
        with store.connect() as snapshot:
            snapshot.execute("BEGIN")
            row = snapshot.execute("""
            SELECT s.uuid,s.content_hash,s.position,s.sample_id,c.cache_key,c.metrics,c.error FROM samples s
            LEFT JOIN trajectory_metrics c ON c.trajectory_uuid=s.uuid
            WHERE s.position>? AND (c.source_hash IS NOT s.content_hash OR c.cache_key IS NOT ?)
            ORDER BY s.position LIMIT 1
            """, (position, key)).fetchone()
            if row is None:
                break
            ident, digest, position, sample_id, old_key, old_metrics, old_error = row
            raw = snapshot.execute("SELECT source FROM samples WHERE uuid=?", (ident,)).fetchone()[0]
        if progress:
            progress(processed, sample_id, ident)
        source = json.loads(raw)
        signature = execution_hash(source)
        previous = json.loads(old_metrics) if old_metrics and old_key == key else {}
        try:
            if previous.get("execution_hash") == signature:
                metrics, error = previous, old_error
                reused += 1
            else:
                metrics = measure(compiler.compile(source, record_dependencies=False), compiler)
                normalized = {k: v for k, v in source.items() if k not in ("id", "uuid", "metadata")}
                metrics["normalized_replay_hash"] = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                error = None
        except CompileError as problem:
            metrics, error = {}, json.dumps(problem.diagnostic())
        metrics["execution_hash"] = signature
        with store.connect() as db:
            changed = db.execute("""
                    INSERT INTO trajectory_metrics
                    SELECT uuid,content_hash,?,?,? FROM samples WHERE uuid=? AND content_hash=?
                    ON CONFLICT(trajectory_uuid) DO UPDATE SET source_hash=excluded.source_hash,
                        cache_key=excluded.cache_key,metrics=excluded.metrics,error=excluded.error
                """, (key, json.dumps(metrics, allow_nan=False), error, ident, digest)).rowcount
            stored += changed
        processed += 1
        if progress:
            progress(processed, None, None)
    return {"revision": revision, "processed": processed, "stored": stored,
            "reused": reused, "cache_key": key, "remaining": pending(store, compiler)}


def label_status(metadata, selector, metrics=None):
    if not isinstance(metadata, dict):
        return None
    labels = metadata.get("observed_labels")
    label = labels.get(selector) if isinstance(labels, dict) else None
    if not isinstance(label, dict):
        return None
    if (label.get("review_status") != "reviewed" or not isinstance(label.get("annotator"), str)
            or not label["annotator"].strip() or not isinstance(label.get("version"), str)
            or not label["version"].strip() or not isinstance(label.get("justification"), str)
            or not label["justification"].strip() or type(label.get("confidence")) not in (int, float)
            or not 0 <= label["confidence"] <= 1 or not isinstance(label.get("evidence"), list)
            or not label["evidence"] or any(not isinstance(v, str) or not v.strip() for v in label["evidence"])):
        return None
    if metrics is not None and any(e not in metrics.get("event_ids", ()) for e in label["evidence"]):
        return None
    fields = ("present", "required", "demonstrated_correctly") if selector.startswith("capability.") else ("value",)
    if any(type(label.get(field)) is not bool for field in fields):
        return None
    return int(all(label[field] for field in fields))


def annotation_issues(metadata):
    if not isinstance(metadata, dict):
        metadata = {}
    issues = []
    for key in REQUIRED:
        value = metadata.get(key)
        valid = isinstance(value, str) and bool(value.strip())
        if key == "primary_domain":
            valid = value in DOMAINS
        elif key == "terminal_outcome":
            valid = value in OUTCOMES
        elif key == "split":
            valid = value in ("train", "development", "test")
        elif key == "is_original":
            valid = type(value) is bool
        elif key == "provenance":
            valid = isinstance(value, dict) and all(
                isinstance(value.get(k), str) and value[k].strip() for k in ("author", "source", "version")
            )
        if not valid:
            issues.append(key)
    return issues


@contextmanager
def snapshot(store, compiler, population):
    if population not in POPULATIONS:
        raise ValueError("Unknown metrics population")
    with store.connect() as db:
        # JSON metadata only, never replay. Cache decoding within this bounded
        # connection avoids reparsing it for every coverage rule.
        @lru_cache(maxsize=32)
        def metadata(raw):
            value = json.loads(raw) if raw else {}
            return value if isinstance(value, dict) else {}
        db.create_function("metric_label", 3, lambda raw, compiled, selector: label_status(metadata(raw), selector, metadata(compiled)) if compiled else None)
        db.create_function("metric_issues", 1, lambda raw: json.dumps(annotation_issues(metadata(raw))))
        db.execute("BEGIN")
        revision = db.execute("SELECT revision FROM project").fetchone()[0]
        # A temporary VIEW cannot bind parameters. A one-row temporary table
        # keeps the opaque cache key out of generated SQL.
        db.execute("CREATE TEMP TABLE metric_key(value TEXT)")
        db.execute("INSERT INTO metric_key VALUES(?)", (cache_key(compiler),))
        db.execute(f"""CREATE TEMP VIEW metric_population AS
            SELECT s.uuid,s.sample_id,s.position,s.content_hash,m.properties,
                c.metrics,c.error,c.trajectory_uuid AS cached,
                m.event_count,m.window_count,m.tags,
                metric_issues(m.properties) AS annotation_issues
            FROM samples s JOIN trajectory_metadata m ON m.trajectory_uuid=s.uuid
            LEFT JOIN trajectory_metrics c ON c.trajectory_uuid=s.uuid
                AND c.source_hash=s.content_hash AND c.cache_key=(SELECT value FROM metric_key)
            WHERE {POPULATIONS[population]}
        """)
        yield db, revision


class Moments:
    def __init__(self):
        self.n, self.mean, self.m2 = 0, 0.0, 0.0

    def step(self, value):
        if value is not None:
            self.n += 1
            delta = value - self.mean
            self.mean += delta / self.n
            self.m2 += delta * (value - self.mean)

    def finalize(self):
        return math.sqrt(max(0, self.m2 / self.n)) if self.n else None


def numeric_projection(db):
    """Extract numeric values once, then reuse a disk-backed sorted index.

    Repeated percentile/bin queries must not repeatedly decode every large
    per-source JSON blob or sort the same values on every percentile request.
    """
    if db.execute("SELECT 1 FROM sqlite_temp_master WHERE name='metric_values'").fetchone():
        return
    db.execute("CREATE TEMP TABLE metric_values(metric TEXT NOT NULL,value REAL NOT NULL)")
    placeholders = ",".join("?" for _ in REGISTRY)
    db.execute(f"""INSERT INTO metric_values
        SELECT v.key,v.atom FROM metric_population p,json_each(p.metrics) v
        WHERE p.error IS NULL AND v.key IN ({placeholders}) AND v.type IN ('integer','real')""", list(REGISTRY))
    for name in ("window_lengths", "idle_runs", "tool_latency_ticks"):
        db.execute(f"""INSERT INTO metric_values SELECT ?,v.atom FROM metric_population p,
            json_each(p.metrics,'$.{name}') v WHERE p.error IS NULL AND v.type IN ('integer','real')""", (name,))
    db.execute("CREATE INDEX metric_value_lookup ON metric_values(metric,value)")


def distribution(db, metric, edges=None):
    if metric not in REGISTRY and metric not in ("window_lengths", "idle_runs", "tool_latency_ticks"):
        raise ValueError("Unknown numeric metric")
    if edges is None:
        edges = [0, 0.1, 0.25, 0.5, 0.75, 1] if "fraction" in metric else COUNT_EDGES
    if (not 1 <= len(edges) <= 64 or any(type(v) not in (int, float) or not math.isfinite(v) for v in edges)
            or any(a >= b for a, b in zip(edges, edges[1:]))):
        raise ValueError("Histogram edges must be 1–64 finite, strictly increasing numbers")
    numeric_projection(db)
    # Metric is allowlisted above; no caller-controlled SQL identifiers or paths.
    if metric in ("window_lengths", "idle_runs", "tool_latency_ticks"):
        population = "pooled_windows" if metric == "window_lengths" else "pooled_runs" if metric == "idle_runs" else "pooled_calls"
    else:
        population = "source_samples"
    source = f"SELECT value AS x FROM metric_values WHERE metric='{metric}'"
    db.create_aggregate("metric_stddev", 1, Moments)
    count, low, high, mean, stddev, total = db.execute(
        f"SELECT count(x),min(x),max(x),avg(x),metric_stddev(x),sum(x) FROM ({source})"
    ).fetchone()
    percentiles = {}
    for p in (50, 90, 95, 99):
        if not count:
            percentiles[f"p{p}"] = None
            continue
        at = (count - 1) * p / 100
        values = [r[0] for r in db.execute(f"SELECT x FROM ({source}) WHERE x IS NOT NULL ORDER BY x LIMIT 2 OFFSET ?", (int(at),))]
        percentiles[f"p{p}"] = values[0] + (values[-1] - values[0]) * (at - int(at))
    boundaries = [None, *edges, None]
    case = "CASE " + " ".join(f"WHEN x<? THEN {i}" for i in range(len(edges))) + f" ELSE {len(edges)} END"
    bin_counts = dict(db.execute(f"SELECT {case} AS bucket,count(*) FROM ({source}) WHERE x IS NOT NULL GROUP BY bucket", edges))
    bins = []
    for i, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        n = bin_counts.get(i, 0)
        bins.append({"lower": left, "upper": right, "count": n, "fraction": ratio(n, count)})
    eligible = db.execute("SELECT count(*) FROM metric_population").fetchone()[0]
    return {"metric": metric, "unit": population, "count": count,
            "missing": max(0, eligible - count) if population == "source_samples" else None,
            "mean": mean, "stddev": stddev, "min": low, "max": high, "sum": total,
            **percentiles, "bins": bins}


def selector_sql(selector):
    if isinstance(selector, dict):
        values = [selector_sql(item) for item in selector["all"]]
        return "(" + " AND ".join(v[0] for v in values) + ")", [p for v in values for p in v[1]]
    if selector.startswith(("domain.", "outcome.")):
        field = "primary_domain" if selector.startswith("domain.") else "terminal_outcome"
        vocabulary = DOMAINS if field == "primary_domain" else OUTCOMES
        choices = ",".join("?" for _ in vocabulary)
        return f"CASE WHEN json_extract(properties,'$.{field}') IN ({choices}) THEN json_extract(properties,'$.{field}')=? END", [*vocabulary, selector.split(".", 1)[1]]
    if selector == "uses_tools":
        return "json_extract(metrics,'$.tool_invocations')>0", []
    if selector.startswith("tool."):
        families = {"fileio": "file_*", "time": "time_*", "tasks_notes": "note_*"}
        return "EXISTS(SELECT 1 FROM json_each(metrics,'$.tools') t WHERE t.key GLOB ? AND t.value>0)", [families[selector.split(".")[1]]]
    return "metric_label(properties,metrics,?)=1", [selector]


def coverage(db):
    config, results = launch_config(), []
    for rule in config["coverage_rules"]:
        measure_name = rule.get("measure", "source_sample_fraction")
        if measure_name == "exported_sample_fraction":
            results.append({**rule, "status": "unavailable", "reason": "Requires an export manifest and configured trainer context budget"})
            continue
        condition, args = selector_sql(rule["selector"])
        within, within_args = selector_sql(rule["within"]) if "within" in rule else ("1", [])
        denominator = db.execute(f"SELECT count(*) FROM metric_population WHERE {within}", within_args).fetchone()[0]
        numerator = db.execute(f"SELECT count(*) FROM metric_population WHERE ({within}) AND ({condition})", within_args + args).fetchone()[0]
        unknown = db.execute(f"SELECT count(*) FROM metric_population WHERE ({within}) AND ({condition}) IS NULL", within_args + args).fetchone()[0]
        families = db.execute(f"SELECT count(DISTINCT CASE WHEN json_type(properties,'$.scenario_family_id')='text' THEN nullif(trim(json_extract(properties,'$.scenario_family_id')),'') END) FROM metric_population WHERE ({within}) AND ({condition})", within_args + args).fetchone()[0]
        value = ratio(numerator, denominator)
        if measure_name == "distinct_scenario_family_count":
            active = denominator >= config["rule_defaults"]["count_minima_active_at_originals"]
            status = "pilot" if not active else "gap" if families < rule["minimum"] else "in_range"
            shortfall = max(0, rule["minimum"] - families)
        else:
            lower, upper = rule["target_range"]
            shortfall = max(0, math.ceil(lower * denominator - numerator))
            status = ("insufficient_support" if denominator < 200 else "unknown_labels" if unknown / denominator > 0.05
                      else "gap" if value < lower else "above_range" if value > upper else "in_range")
        results.append({**rule, "numerator": numerator, "denominator": denominator, "fraction": value,
                        "unknown": unknown, "unknown_fraction": ratio(unknown, denominator),
                        "distinct_families": families, "shortfall": shortfall, "status": status})
    return results


def overview(store, compiler, population="all"):
    with snapshot(store, compiler, population) as (db, revision):
        total, cached, invalid = db.execute("SELECT count(*),count(cached),count(error) FROM metric_population").fetchone()
        metadata_issues = db.execute("SELECT count(*) FROM metric_population WHERE json_array_length(annotation_issues)>0").fetchone()[0]
        selectors = semantic_selectors()
        unknown_samples = db.execute("SELECT count(*) FROM metric_population WHERE " + " OR ".join("metric_label(properties,metrics,?) IS NULL" for _ in selectors), selectors).fetchone()[0]
        missing_fields = [{"field": key, "samples": n} for key, n in db.execute("SELECT v.value,count(*) FROM metric_population p,json_each(p.annotation_issues) v GROUP BY v.value ORDER BY 2 DESC,1")]
        duplicate_extras = db.execute("SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM metric_population WHERE json_extract(metrics,'$.normalized_replay_hash') IS NOT NULL GROUP BY json_extract(metrics,'$.normalized_replay_hash') HAVING count(*)>1)").fetchone()[0]
        split_conflicts = []
        for field in ("scenario_family_id", "template_family_id"):
            split_conflicts.extend({"field": field, "family": family, "splits": splits} for family, splits in db.execute(f"""
                SELECT json_extract(properties,'$.{field}'),group_concat(DISTINCT json_extract(properties,'$.split'))
                FROM trajectory_metadata WHERE json_type(properties,'$.{field}')='text'
                    AND trim(json_extract(properties,'$.{field}'))!=''
                    AND json_extract(properties,'$.split') IN ('train','development','test')
                GROUP BY 1 HAVING count(DISTINCT json_extract(properties,'$.split'))>1 LIMIT 50
            """))
        domain = dict(db.execute("SELECT json_extract(properties,'$.primary_domain'),count(*) FROM metric_population GROUP BY 1"))
        outcome = dict(db.execute("SELECT json_extract(properties,'$.terminal_outcome'),count(*) FROM metric_population GROUP BY 1"))
        tools = [{"tool": name, "samples": n, "invocations": invocations} for name, n, invocations in db.execute("SELECT t.key,count(*),sum(t.value) FROM metric_population p,json_each(p.metrics,'$.tools') t GROUP BY t.key ORDER BY 2 DESC,1 LIMIT 100")]
        categories = [{"category": name, "instances": n} for name, n in db.execute("SELECT t.key,sum(t.value) FROM metric_population p,json_each(p.metrics,'$.window_categories') t GROUP BY t.key ORDER BY 2 DESC,1 LIMIT 100")]
        concurrency = {kind: [{"generators": str(i) if i < 3 else "3+", "ticks": n,
                               "fraction": ratio(n, denominator)} for i, n, denominator in db.execute(f"""
            SELECT CAST(t.key AS INTEGER),sum(json_extract(t.value,'$.ticks')),
                (SELECT sum(json_extract(metrics,'$.duration_ticks')) FROM metric_population)
            FROM metric_population p,json_each(p.metrics,'$.concurrency.{kind}') t GROUP BY t.key ORDER BY 1
        """)] for kind in ("model", "external")}
        loss = dict(db.execute("SELECT t.key,sum(t.value) FROM metric_population p,json_each(p.metrics,'$.raw_loss_weights') t GROUP BY t.key"))
        total_loss = sum(loss.values())
        families, templates = db.execute("SELECT count(DISTINCT CASE WHEN json_type(properties,'$.scenario_family_id')='text' THEN nullif(trim(json_extract(properties,'$.scenario_family_id')),'') END),count(DISTINCT CASE WHEN json_type(properties,'$.template_family_id')='text' THEN nullif(trim(json_extract(properties,'$.template_family_id')),'') END) FROM metric_population").fetchone()
        distributions = {name: distribution(db, name) for name in REGISTRY}
        distributions.update({name: distribution(db, name) for name in ("window_lengths", "idle_runs", "tool_latency_ticks")})
        return {
            "revision": revision, "population": population, "cache_key": cache_key(compiler),
            "summary": {"samples": total, "cached": cached, "pending": total - cached,
                        "compile_errors": invalid, "valid_compiled": cached - invalid,
                        "samples_missing_metadata": metadata_issues,
                        "samples_with_unknown_labels": unknown_samples,
                        "exact_replay_duplicate_extras": duplicate_extras,
                        "scenario_families": families, "template_families": templates},
            "missing_fields": missing_fields,
            "family_split_conflicts": split_conflicts,
            "family_split_conflicts_scope": "Project-wide family IDs and split names; no final-test replay or results are read",
            "registry": REGISTRY,
            "domains": [{"value": name, "count": domain.get(name, 0)} for name in DOMAINS],
            "outcomes": [{"value": name, "count": outcome.get(name, 0)} for name in OUTCOMES],
            "unknown_domains": total - sum(domain.get(name, 0) for name in DOMAINS),
            "unknown_outcomes": total - sum(outcome.get(name, 0) for name in OUTCOMES),
            "distributions": distributions, "tools": tools, "window_categories": categories,
            "concurrency": concurrency,
            "raw_loss_allocation": [{"target": k, "weight": v, "fraction": ratio(v, total_loss)} for k, v in loss.items()],
            "coverage": coverage(db) if population == "accepted_train_originals" else [],
            "unavailable": [
                {"metric": "trainer_exposure_and_measured_cost", "reason": "Requires trainer exposure, normalization, hardware and optimizer logs"},
                {"metric": "export_chunk_overhead_and_remix_coverage", "reason": "Requires accepted export manifests; source counts are not export counts"},
                {"metric": "reaction_latency_dependency_and_closure_quality", "reason": "Requires reviewed evidence/response/dependency/last-needed annotations and exact decision-frontier checks"},
                {"metric": "near_duplicates_and_semantic_validity", "reason": "Requires reviewed similarity clusters and independent semantic judgments; a replay hash is only an exact-structure candidate"},
                {"metric": "generation_cost", "reason": "Historical samples do not consistently retain per-request counters, retries, costs and wall times"},
            ],
            "metadata_contract": {"required": list(REQUIRED), "domains": DOMAINS, "outcomes": OUTCOMES,
                                  "semantic_selectors": semantic_selectors(),
                                  "annotation_context_tool": "get_metric_annotation_context",
                                  "instruction": "Request sample-specific context before annotation; provenance, split and lineage may require owner-supplied history or policy."},
        }


def query_samples(store, compiler, population="all", issue=None, metric=None, minimum=None, maximum=None, offset=0, limit=50):
    if issue not in (None, "missing_metadata", "unknown_labels", "compile_error", "pending"):
        raise ValueError("Unknown metrics issue")
    clauses, args = [], []
    if issue == "missing_metadata":
        clauses.append("json_array_length(annotation_issues)>0")
    elif issue == "unknown_labels":
        clauses.append("(" + " OR ".join("metric_label(properties,metrics,?) IS NULL" for _ in semantic_selectors()) + ")")
        args.extend(semantic_selectors())
    elif issue == "compile_error":
        clauses.append("error IS NOT NULL")
    elif issue == "pending":
        clauses.append("cached IS NULL")
    if metric is not None:
        if metric not in REGISTRY:
            raise ValueError("Unknown numeric metric")
        for bound, operator in ((minimum, ">="), (maximum, "<=")):
            if bound is not None:
                if not math.isfinite(bound):
                    raise ValueError("Metric bounds must be finite")
                clauses.append(f"json_extract(metrics,'$.{metric}') {operator} ?")
                args.append(bound)
        clauses.append(f"json_extract(metrics,'$.{metric}') IS NOT NULL")
    elif minimum is not None or maximum is not None:
        raise ValueError("Metric is required when specifying bounds")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("minimum must not exceed maximum")
    with snapshot(store, compiler, population) as (db, revision):
        condition = " AND ".join(clauses) or "1"
        total = db.execute(f"SELECT count(*) FROM metric_population WHERE {condition}", args).fetchone()[0]
        rows = db.execute(f"""SELECT uuid,sample_id,content_hash,metrics,error,annotation_issues,properties,
            (SELECT count(*) FROM samples s WHERE s.position<p.position) AS sample_index
            FROM metric_population p WHERE {condition} ORDER BY position LIMIT ? OFFSET ?""", [*args, limit, offset])
        samples = []
        for row in rows:
            compiled = json.loads(row[3]) if row[3] else None
            metadata = json.loads(row[6]) if row[6] else {}
            samples.append({"uuid": row[0], "id": row[1], "content_hash": row[2],
                            "metrics": {k: compiled.get(k) for k in REGISTRY} if compiled else None,
                            "error": json.loads(row[4]) if row[4] else None,
                            "missing_metadata": json.loads(row[5]),
                            "unknown_labels": [s for s in semantic_selectors() if compiled is None or label_status(metadata, s, compiled) is None],
                            "index": row[7]})
            if issue in ("missing_metadata", "unknown_labels"):
                samples[-1]["annotation_context"] = {"name": "get_metric_annotation_context", "arguments": {"sample_id": row[1]}}
        return {"revision": revision, "population": population, "total": total, "offset": offset, "limit": limit, "samples": samples}
