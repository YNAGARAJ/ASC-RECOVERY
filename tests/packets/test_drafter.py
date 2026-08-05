"""ScriptedPacketDrafter's own behavior. AnthropicPacketDrafter is
deliberately never exercised here -- no real network call, no API key
available in this environment; same deferral as real cloud KMS adapters
elsewhere in this codebase."""

from __future__ import annotations

from decimal import Decimal

import pytest

from packets.drafter import ScriptedPacketDrafter, estimate_cost


def test_scripted_drafter_returns_responses_in_order() -> None:
    drafter = ScriptedPacketDrafter(["first draft", "second draft"])

    assert drafter.draft("prompt A") == "first draft"
    assert drafter.draft("prompt B") == "second draft"
    assert drafter.prompts_received == ["prompt A", "prompt B"]


def test_scripted_drafter_raises_once_responses_are_exhausted() -> None:
    drafter = ScriptedPacketDrafter(["only draft"])
    drafter.draft("prompt A")

    with pytest.raises(RuntimeError, match="no more responses"):
        drafter.draft("prompt B")


def test_estimate_cost_uses_known_model_pricing() -> None:
    # claude-sonnet-5: $3.00/M input, $15.00/M output
    cost = estimate_cost("claude-sonnet-5", input_tokens=1000, output_tokens=500)
    assert cost == Decimal("0.0105")  # (1000*3 + 500*15) / 1_000_000


def test_estimate_cost_falls_back_to_default_pricing_for_unknown_model() -> None:
    cost = estimate_cost("some-future-model", input_tokens=1_000_000, output_tokens=0)
    assert cost == Decimal("3.0000")
