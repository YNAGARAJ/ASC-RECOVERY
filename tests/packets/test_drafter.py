"""ScriptedPacketDrafter's own behavior. AnthropicPacketDrafter is
deliberately never exercised here -- no real network call, no API key
available in this environment; same deferral as real cloud KMS adapters
elsewhere in this codebase."""

from __future__ import annotations

import pytest

from packets.drafter import ScriptedPacketDrafter


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
