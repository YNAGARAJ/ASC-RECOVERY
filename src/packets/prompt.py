"""Builds the prompt sent to the LLM, structurally excluding PHI.

Patient name, member id, payer claim control number, and date of service
never enter `BuiltPrompt.text` as real values -- like the dollar figures
(expected/actual allowed, shortfall), they live only in the
`placeholders` map, used for post-generation substitution
(`render_final_text`). Only procedure code, root cause, and evidence
text are ever real values in the prompt text itself.

F-13 (docs/audit/REGISTER.md): an earlier version of this module
embedded the claim control number and date of service as literal text
and called them "not PHI" in this docstring -- wrong. A claim/account
number is a HIPAA Safe Harbor identifier (#16 account numbers / #18 any
other unique identifying number); a date directly tied to an
individual's care, including date of service, is explicitly identifier
#3. Both are placeholder-only now, same as patient name/member id
always were -- and the real `AnthropicPacketDrafter` (main.py) must not
be enabled against a live tenant before a BAA exists with the LLM
vendor, a compliance precondition this module's data minimization
doesn't substitute for (see docs/compliance/README.md's own checklist;
that gate is process, not something code can enforce).
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
CLAIM_REFERENCE_TOKEN = "{{CLAIM_REFERENCE}}"  # nosec B105
DATE_OF_SERVICE_TOKEN = "{{DATE_OF_SERVICE}}"  # nosec B105
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
        CLAIM_REFERENCE_TOKEN: data.payer_claim_control_number,
        DATE_OF_SERVICE_TOKEN: data.date_of_service.isoformat(),
        EXPECTED_ALLOWED_TOKEN: data.expected_allowed,
        ACTUAL_ALLOWED_TOKEN: data.actual_allowed,
        SHORTFALL_TOKEN: data.shortfall,
    }
    text = (
        f"Draft the body of a professional insurance appeal letter, in the "
        f"style of a letter opening with {template.salutation!r} and closing "
        f"with {template.closing!r}.\n\n"
        f"Claim reference: {CLAIM_REFERENCE_TOKEN}\n"
        f"Procedure code: {data.procedure_code}\n"
        f"Date of service: {DATE_OF_SERVICE_TOKEN}\n"
        f"Reason for underpayment: {data.root_cause}\n"
        f"Supporting evidence: {data.evidence}\n\n"
        "IMPORTANT -- do not write any dollar amount as digits, and do not "
        "compute, adjust, or restate any figure yourself. Use exactly these "
        f"placeholder tokens wherever a dollar figure belongs: "
        f"{EXPECTED_ALLOWED_TOKEN} for the contracted allowed amount, "
        f"{ACTUAL_ALLOWED_TOKEN} for the amount actually paid, and "
        f"{SHORTFALL_TOKEN} for the shortfall. Also use "
        f"{CLAIM_REFERENCE_TOKEN} and {DATE_OF_SERVICE_TOKEN} exactly as "
        "shown above wherever the claim reference or date of service "
        "belongs -- never invent or restate either yourself. Refer to the "
        f"patient only as {PATIENT_TOKEN} and, if a member id is needed, "
        f"only as {MEMBER_ID_TOKEN} -- never write or invent a patient "
        "name or member id yourself."
    )
    return BuiltPrompt(text=text, placeholders=placeholders)


def render_final_text(draft: str, placeholders: dict[str, str]) -> str:
    result = draft
    for token, value in placeholders.items():
        result = result.replace(token, value)
    return result
