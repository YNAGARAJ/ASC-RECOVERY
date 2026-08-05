"""LLM drafting port, same port/adapter shape as
`security.kms`/`ingestion.virus_scan`: a `Protocol`, a scripted/fake
adapter used by every test in this repo, and a real adapter no test
exercises (real cloud LLM calls are non-deterministic, cost money, and
need an API key this environment doesn't have -- same deferral as real
cloud KMS adapters and real antivirus engines elsewhere in this codebase).

The real adapter is a thin wrapper: it has no special knowledge of the
money/PHI safety rules -- those are enforced entirely by
`packets.prompt`/`packets.currency`/`packets.service` regardless of which
adapter drafted the text. A misbehaving or compromised LLM provider still
can't get an unvalidated dollar figure into a sent packet.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from observability.metrics import Instruments, noop_instruments, record_llm_usage

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024

# USD per million tokens, (input, output). Illustrative rates for
# cost-tracking purposes -- confirm against the provider's published
# pricing before this feeds a real budget decision; that reconciliation
# is Phase 9/11 deployment scope, not this phase's.
_PRICING_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
}
_DEFAULT_PRICING = (Decimal("3.00"), Decimal("15.00"))


def estimate_cost(model: str, *, input_tokens: int, output_tokens: int) -> Decimal:
    input_price, output_price = _PRICING_PER_MILLION_TOKENS.get(model, _DEFAULT_PRICING)
    cost = (
        Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price
    ) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.0001"))


class PacketDrafter(Protocol):
    def draft(self, prompt: str) -> str: ...


class ScriptedPacketDrafter:
    """Test/dev adapter: returns a pre-scripted response per call, in
    order. Never makes a network call. `responses` may include
    deliberately malformed drafts (e.g. containing a raw dollar figure,
    or a figure that doesn't match the finding record) to exercise
    `packets.service`'s reject-and-regenerate path."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts_received: list[str] = []

    def draft(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        if not self._responses:
            raise RuntimeError("ScriptedPacketDrafter has no more responses queued")
        return self._responses.pop(0)


class AnthropicPacketDrafter:
    """Real adapter -- not exercised by any test in this repo."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        instruments: Instruments | None = None,
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._instruments = instruments if instruments is not None else noop_instruments()

    def draft(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        cost = estimate_cost(
            self._model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        record_llm_usage(self._instruments, model=self._model, cost_usd=cost)
        return "".join(block.text for block in response.content if block.type == "text")
