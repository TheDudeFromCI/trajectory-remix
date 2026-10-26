from pydantic_ai.usage import RequestUsage

from trajectory_remix.generation_stats import RequestStats


def test_rates_use_reported_counts_and_separate_live_and_average(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(
        "trajectory_remix.generation_stats.time.monotonic", lambda: clock[0]
    )
    stats = RequestStats("model", 1000)
    assert "completion_tokens" not in stats.sample()
    clock[0] = 11
    stats.first_token()
    clock[0] = 13
    stats.sample(RequestUsage(input_tokens=400, output_tokens=50))
    clock[0] = 15
    live = stats.sample(
        RequestUsage(
            input_tokens=400,
            output_tokens=150,
            cache_read_tokens=32,
            details={"reasoning_tokens": 40},
        ),
        baseline=200,
    )
    assert live["current_tokens_per_second"] == 50
    assert live["context_used_tokens"] == 550
    assert live["first_prompt_tokens"] == 200
    final = stats.sample(final=True, baseline=200)
    assert final["average_tokens_per_second"] == 37.5
    assert "current_tokens_per_second" not in final
    assert final["time_to_first_token_seconds"] == 1
    assert final["reasoning_tokens"] == 40


def test_end_only_usage_does_not_invent_live_rate():
    stats = RequestStats("local")
    final = stats.sample(RequestUsage(input_tokens=10, output_tokens=20), final=True)
    assert final["total_tokens"] == 30
    assert "current_tokens_per_second" not in final
    assert "time_to_first_token_seconds" not in final


def test_stream_estimates_enable_context_meter_then_yield_to_provider(compiler):
    stats = RequestStats("local", 1000, compiler.tokenizer)
    stats.estimate("System prompt and request", "Generated output", "reasoning")
    estimate = stats.sample()
    assert estimate["token_source"] == "estimated"
    assert estimate["prompt_tokens"] > 0
    assert estimate["completion_tokens"] > 0
    assert estimate["context_used_tokens"] == estimate["total_tokens"]
    reported = stats.sample(RequestUsage(input_tokens=100, output_tokens=20))
    assert reported["token_source"] == "provider"
    assert reported["total_tokens"] == 120
