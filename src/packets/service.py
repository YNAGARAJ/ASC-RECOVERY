"""Orchestrates packet generation: build prompt -> draft -> reject if the
LLM wrote a raw currency figure instead of using a placeholder ->
substitute placeholders -> validate the final text against the finding
record's known amounts -> retry on failure, up to a small cap.

Never returns an unvalidated draft: `PacketDraftResult.success` is only
True when the final text passed `packets.currency.validate_currency`.
Every rejected attempt is recorded on `.rejections` so the caller can
audit/log it (CLAUDE.md rule 5, and the Phase 7 prompt's explicit "log
the rejection").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from packets.currency import extract_currency_figures, validate_currency
from packets.drafter import PacketDrafter
from packets.prompt import PromptInput, build_prompt, render_final_text
from packets.templates import PacketTemplate

DEFAULT_MAX_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class RejectedAttempt:
    raw_draft: str
    reason: str


@dataclass(frozen=True, slots=True)
class PacketDraftResult:
    success: bool
    final_text: str | None
    attempts: int
    rejections: tuple[RejectedAttempt, ...]


def _allowed_amounts(data: PromptInput) -> frozenset[Decimal]:
    return frozenset(
        Decimal(value) for value in (data.expected_allowed, data.actual_allowed, data.shortfall)
    )


def generate_packet_draft(
    data: PromptInput,
    template: PacketTemplate,
    drafter: PacketDrafter,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> PacketDraftResult:
    built = build_prompt(data, template)
    allowed_amounts = _allowed_amounts(data)
    rejections: list[RejectedAttempt] = []

    for attempt in range(1, max_attempts + 1):
        raw_draft = drafter.draft(built.text)

        raw_figures = extract_currency_figures(raw_draft)
        if raw_figures:
            rejections.append(
                RejectedAttempt(
                    raw_draft=raw_draft,
                    reason=(
                        "draft contains a raw currency figure "
                        f"({raw_figures[0]}) instead of a placeholder token"
                    ),
                )
            )
            continue

        final_text = render_final_text(raw_draft, built.placeholders)
        validation = validate_currency(final_text, allowed_amounts)
        if not validation.valid:
            rejections.append(
                RejectedAttempt(
                    raw_draft=raw_draft,
                    reason=(
                        "final text contains figure(s) not in the finding record: "
                        f"{validation.unmatched}"
                    ),
                )
            )
            continue

        return PacketDraftResult(
            success=True,
            final_text=final_text,
            attempts=attempt,
            rejections=tuple(rejections),
        )

    return PacketDraftResult(
        success=False,
        final_text=None,
        attempts=max_attempts,
        rejections=tuple(rejections),
    )
