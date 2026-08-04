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

from typing import Protocol

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024


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
    ) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def draft(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )
