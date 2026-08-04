"""Builds the prompt sent to the LLM, structurally excluding PHI.

Patient name and member id never enter `BuiltPrompt.text` -- they live
only in the `placeholders` map, used for post-generation substitution
(`render_final_text`), the same mechanism the money figures use (see
`packets.currency`/`packets.service`). Everything else on `PromptInput`
(procedure code, dates, dollar amounts, root cause, evidence, payer claim
control number) is not PHI and is included as real values, so the LLM
has enough context to write coherent prose -- CLAUDE.md rule 1 and the
Phase 7 prompt's "minimum necessary" requirement are specifically about
names/member IDs, not about amounts or codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from packets.templates import PacketTemplate

# bandit flags these as B105 "possible hardcoded password" purely on the
# `_TOKEN` suffix in the variable name -- these are template placeholder
# markers substituted by render_final_text(), not secrets or credentials.
PATIENT_TOKEN = "{{PATIENT_REF}}"  # nosec B105
MEMBER_ID_TOKEN = "{{MEMBER_ID}}"  # nosec B105
EXPECTED_ALLOWED_TOKEN = "{{EXPECTED_ALLOWED}}"  # nosec B105
ACTUAL_ALLOWED_TOKEN = "{{ACTUAL_ALLOWED}}"  # nosec B105
SHORTFALL_TOKEN = "{{SHORTFALL}}"  # nosec B105


@dataclass(frozen=True, slots=True)
class PromptInput:
    payer_claim_control_number: str
    procedure_code: str
    date_of_service: date
    expected_allowed: str
    actual_allowed: str
    shortfall: str
    root_cause: str
    evidence: str
    patient_name: str | None
    patient_member_id: str | None


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    text: str
    placeholders: dict[str, str]


def build_prompt(data: PromptInput, template: PacketTemplate) -> BuiltPrompt:
    placeholders = {
        PATIENT_TOKEN: data.patient_name or "the patient",
        MEMBER_ID_TOKEN: data.patient_member_id or "",
        EXPECTED_ALLOWED_TOKEN: data.expected_allowed,
        ACTUAL_ALLOWED_TOKEN: data.actual_allowed,
        SHORTFALL_TOKEN: data.shortfall,
    }
    text = (
        f"Draft the body of a professional insurance appeal letter, in the "
        f"style of a letter opening with {template.salutation!r} and closing "
        f"with {template.closing!r}.\n\n"
        f"Claim reference: {data.payer_claim_control_number}\n"
        f"Procedure code: {data.procedure_code}\n"
        f"Date of service: {data.date_of_service.isoformat()}\n"
        f"Reason for underpayment: {data.root_cause}\n"
        f"Supporting evidence: {data.evidence}\n\n"
        "IMPORTANT -- do not write any dollar amount as digits, and do not "
        "compute, adjust, or restate any figure yourself. Use exactly these "
        f"placeholder tokens wherever a dollar figure belongs: "
        f"{EXPECTED_ALLOWED_TOKEN} for the contracted allowed amount, "
        f"{ACTUAL_ALLOWED_TOKEN} for the amount actually paid, and "
        f"{SHORTFALL_TOKEN} for the shortfall. Refer to the patient only as "
        f"{PATIENT_TOKEN} and, if a member id is needed, only as "
        f"{MEMBER_ID_TOKEN} -- never write or invent a patient name or "
        "member id yourself."
    )
    return BuiltPrompt(text=text, placeholders=placeholders)


def render_final_text(draft: str, placeholders: dict[str, str]) -> str:
    result = draft
    for token, value in placeholders.items():
        result = result.replace(token, value)
    return result
