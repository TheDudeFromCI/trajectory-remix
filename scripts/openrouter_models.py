"""Check OpenRouter models for Trajectory Remix generation and estimate their cost.

Reads OpenRouter's public model list (no API key, no generation requests) and reports,
for each model: whether it supports the structured output Remix sends, how its
reasoning must be configured, the extra request fields to put in the endpoint's
settings, and a rough Generator cost per 1,000 trajectories (no reviews or repairs).

    python scripts/openrouter_models.py mistralai/mistral-small-3.2-24b-instruct
    python scripts/openrouter_models.py --max-input-price 0.15

The cost estimate is a planning aid built from measured prompt sizes and stated
assumptions (see ASSUMPTIONS). Replace it with the Usage tab of a real pilot job.
"""

import argparse
import json
import sys
import urllib.request

MODELS_URL = "https://openrouter.ai/api/v1/models"
ASSUMPTIONS = dict(
    bytes_per_token=3.5,
    selector_fixed_bytes=16050,  # Selector instruction, schema, ~6 KB tools, proposal, step.
    source_bytes_per_event=600,  # Compact envelope plus average content.
    writer_fixed_bytes=2000,
    writer_visible_share=0.4,  # Average share of history in a writer's ancestor view.
    selection_output_tokens=400,
    event_output_tokens=180,
    overhead=1.10,  # Phase, verify and echo steps, about 5% format retries.
    selector_cached_share=0.75,  # With sticky sessions and a cache discount.
)


def tokens(events, a=ASSUMPTIONS):
    """Estimated (selector input, writer input, output) tokens for one trajectory."""
    fixed, step = a["selector_fixed_bytes"], a["source_bytes_per_event"]
    selector = sum(fixed + step * k for k in range(events)) / a["bytes_per_token"]
    visible = a["writer_visible_share"] * step
    writer = sum(a["writer_fixed_bytes"] + visible * k for k in range(events))
    writer /= a["bytes_per_token"]
    output = events * (a["selection_output_tokens"] + a["event_output_tokens"])
    return tuple(value * a["overhead"] for value in (selector, writer, output))


def per_million(pricing, key):
    try:
        value = float(pricing.get(key) or 0) * 1e6
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def cost_per_thousand(model, events):
    pricing = model.get("pricing", {})
    price_in, price_out = per_million(pricing, "prompt"), per_million(pricing, "completion")
    cached_price = per_million(pricing, "input_cache_read")
    if price_in is None or price_out is None:
        return None
    selector, writer, output = tokens(events)
    discounted = cached_price is not None and 0 < cached_price < price_in
    cached = selector * ASSUMPTIONS["selector_cached_share"] if discounted else 0
    dollars = (selector + writer - cached) * price_in + cached * (cached_price or 0)
    dollars += output * price_out
    return dollars / 1e6 * 1000


def reasoning_settings(model):
    """The reasoning request field that turns reasoning off, or keeps it cheapest."""
    reasoning = model.get("reasoning")
    supported = set(model.get("supported_parameters") or [])
    if not reasoning and "reasoning" not in supported:
        return None, "none"
    reasoning = reasoning or {}
    efforts = reasoning.get("supported_efforts")
    if reasoning.get("mandatory"):
        lowest = (efforts or ["low"])[-1]
        return {"effort": lowest, "exclude": True}, f"mandatory (lowest effort: {lowest})"
    if efforts is None or "none" in efforts:
        if efforts is None and "supported_efforts" not in reasoning:
            return {"enabled": False}, "optional (disabled with enabled: false)"
        return {"effort": "none"}, "optional (disabled with effort: none)"
    return {"effort": efforts[-1], "exclude": True}, f"cannot be disabled; lowest {efforts[-1]}"


def assess(model):
    supported = set(model.get("supported_parameters") or [])
    reasoning, reasoning_note = reasoning_settings(model)
    extra = {"provider": {"require_parameters": True}}
    if reasoning is not None:
        extra["reasoning"] = reasoning
    problems = []
    if "response_format" not in supported:
        problems.append("no response_format: Remix requests would be ignored or rejected")
    if "structured_outputs" not in supported:
        problems.append(
            "no json_schema structured outputs: event requests need it with require_parameters"
        )
    if reasoning is not None and reasoning.get("exclude"):
        problems.append("reasoning cannot be turned off: expect extra output tokens and cost")
    pricing = model.get("pricing", {})
    cached = per_million(pricing, "input_cache_read")
    price_in = per_million(pricing, "prompt")
    return dict(
        id=model.get("id"),
        context_length=model.get("context_length"),
        input_price=price_in,
        output_price=per_million(pricing, "completion"),
        cache_discount=bool(cached is not None and price_in and 0 < cached < price_in),
        reasoning=reasoning_note,
        extra_body=extra,
        ready=not any(p.startswith("no ") for p in problems),
        problems=problems,
        cost_per_1k={n: cost_per_thousand(model, n) for n in (20, 40, 80)},
    )


def fetch():
    request = urllib.request.Request(MODELS_URL, headers={"User-Agent": "trajectory-remix"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["data"]


def report(result):
    money = " · ".join(
        f"{n} events ${cost:,.0f}" if cost is not None else f"{n} events ?"
        for n, cost in result["cost_per_1k"].items()
    )
    lines = [
        f"{result['id']}  [{'ready' if result['ready'] else 'NOT READY'}]",
        f"  price per M tokens: in ${result['input_price']:.3f}, out ${result['output_price']:.3f},"
        f" cache discount: {'yes' if result['cache_discount'] else 'no'};"
        f" context {result['context_length']}",
        f"  reasoning: {result['reasoning']}",
        f"  estimated Generator cost per 1,000 trajectories: {money}",
        f"  extra_body: {json.dumps(result['extra_body'])}",
    ]
    lines += [f"  ! {problem}" for problem in result["problems"]]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("models", nargs="*", help="OpenRouter model IDs to check")
    parser.add_argument("--max-input-price", type=float, help="List ready models up to this $/M")
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    args = parser.parse_args(argv)
    if not args.models and args.max_input_price is None:
        parser.error("name one or more models, or pass --max-input-price")
    data = {m["id"]: m for m in fetch()}
    if args.models:
        missing = [m for m in args.models if m not in data]
        if missing:
            print("Unknown model IDs: " + ", ".join(missing), file=sys.stderr)
        results = [assess(data[m]) for m in args.models if m in data]
    else:
        # Batch variants are priced for asynchronous batch jobs, not request-at-a-time use.
        results = [assess(m) for m in data.values() if not m["id"].endswith(":batch")]
        results = [
            r
            for r in results
            if r["ready"] and r["input_price"] and r["input_price"] <= args.max_input_price
        ]
        results.sort(key=lambda r: r["cost_per_1k"][40] or float("inf"))
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n\n".join(report(r) for r in results) or "No matching models.")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
