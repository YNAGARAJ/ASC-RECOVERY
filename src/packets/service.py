"""Orchestrates packet generation: build prompt -> draft -> reject if the
LLM wrote a raw currency figure instead of using a placeholder -> reject
if the required labeled figure lines aren't verbatim (F-15,
docs/audit/REGISTER.md -- catches a token placed against the wrong
label, which plain set-membership validation can't) -> substitute
placeholders -> validate the final text against the finding record's
known amounts -> retry on failure, up to a small cap.

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
from packets.prompt import PromptInput, build_prompt, render_final_text, required_figure_lines
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
    # The procedure code is the one bare-digit string this system knows
    # for certain will legitimately appear in the draft -- F-14
    # (docs/audit/REGISTER.md) added bare-integer detection to
    # extract_currency_figures, so without this it would flag the
    # procedure code itself as a hallucinated currency figure. Diagnosis
    # codes (Phase 9) join it for the same reason -- an ICD-10 code like
    # "E11.9" contains a decimal point of its own, which
    # extract_currency_figures' masking-based exclude handles correctly
    # even though the code as a whole isn't what the regex would match
    # (see that module's own docstring on why masking, not fragment
    # filtering, is what actually closes this).
    exclude = frozenset({data.procedure_code, *data.diagnosis_codes})
    rejections: list[RejectedAttempt] = []

    for attempt in range(1, max_attempts + 1):
        raw_draft = drafter.draft(built.text)

        raw_figures = extract_currency_figures(raw_draft, exclude=exclude)
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

        missing_lines = [line for line in required_figure_lines() if line not in raw_draft]
        if missing_lines:
            rejections.append(
                RejectedAttempt(
                    raw_draft=raw_draft,
                    reason=(
                        "draft is missing or mislabeled the required figure line(s): "
                        f"{missing_lines}"
                    ),
                )
            )
            continue

        final_text = render_final_text(raw_draft, built.placeholders)
        validation = validate_currency(final_text, allowed_amounts, exclude=exclude)
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
