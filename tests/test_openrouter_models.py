"""The OpenRouter model checker reads model metadata correctly. No network."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "openrouter_models", Path(__file__).parents[1] / "scripts" / "openrouter_models.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

PRICES = {"prompt": "0.0000001", "completion": "0.0000003", "input_cache_read": "0.00000001"}
FULL = ["tools", "tool_choice"]


def model(**fields):
    return dict(id="vendor/model", context_length=131072, pricing=PRICES, **fields)


def test_plain_instruct_model_is_ready_without_reasoning_settings():
    result = checker.assess(model(supported_parameters=FULL))
    assert result["ready"] and result["problems"] == [] and result["cache_discount"]
    assert result["extra_body"] == {"provider": {"require_parameters": True}}


def test_reasoning_is_switched_off_the_way_each_model_accepts():
    optional = model(
        supported_parameters=[*FULL, "reasoning"],
        reasoning={"mandatory": False, "supported_efforts": ["high", "none"]},
    )
    assert checker.assess(optional)["extra_body"]["reasoning"] == {"effort": "none"}
    toggle = model(supported_parameters=[*FULL, "reasoning"], reasoning={"mandatory": False})
    assert checker.assess(toggle)["extra_body"]["reasoning"] == {"enabled": False}
    mandatory = model(
        supported_parameters=[*FULL, "reasoning"],
        reasoning={"mandatory": True, "supported_efforts": ["high", "medium", "low"]},
    )
    result = checker.assess(mandatory)
    assert result["extra_body"]["reasoning"] == {"effort": "low", "exclude": True}
    assert result["ready"] and any("cannot be turned off" in p for p in result["problems"])


def test_models_without_native_tools_are_not_ready():
    result = checker.assess(model(supported_parameters=["response_format"]))
    assert not result["ready"] and "tools" in result["problems"][0]
    assert not checker.assess(model(supported_parameters=[]))["ready"]


def test_json_output_support_is_not_required():
    # Drafts are plain text, so models without response_format are still ready.
    assert checker.assess(model(supported_parameters=["tools"]))["ready"]


def test_cost_grows_with_episode_length_and_falls_with_a_cache_discount():
    cached = checker.assess(model(supported_parameters=FULL))["cost_per_1k"]
    assert cached[20] < cached[40] < cached[80]
    no_discount = dict(PRICES, input_cache_read="0.0000001")
    plain = checker.assess(dict(model(supported_parameters=FULL), pricing=no_discount))
    assert plain["cost_per_1k"][40] > cached[40] and not plain["cache_discount"]
    # The estimate is linear in price: 10x the prices is 10x the cost.
    tenfold = {k: str(float(v) * 10) for k, v in PRICES.items()}
    scaled = checker.assess(dict(model(supported_parameters=FULL), pricing=tenfold))
    assert abs(scaled["cost_per_1k"][40] - 10 * cached[40]) < 1e-6
