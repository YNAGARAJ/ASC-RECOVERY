"""Field-level PHI masking (Phase 4, `docs/MASTER-BUILD-PROMPT-V2.md`) --
a declarative per-column policy plus a query-layer function that masks
PHI columns for roles lacking `Action.VIEW_UNMASKED_PHI`.

Deliberately separate from `security.encryption`/`security.phi_columns`:
those protect data at rest (AES-256-GCM, decrypted only by an
authenticated read); this module decides whether an *already-decrypted*
value should actually reach a given role's response. `analyst` is the
role this exists for -- sees amounts and codes, never patient names or
member ids -- but the mechanism is role-driven, not analyst-specific, so
any future role can be slotted onto either side of the same `can()` check
without touching this module.

Applied once, at the API repository layer, right after decryption
(`api/repository.py`) -- not inside individual route handlers, so it
can't be forgotten on a new endpoint the way an ad-hoc per-route check
could be.
"""

from __future__ import annotations

from security.rbac import Action, Role, can

_MASK_TOKEN = "[MASKED]"


def mask_phi_value(role: Role, value: str | None) -> str | None:
    """Returns `value` unchanged if `role` may view unmasked PHI, else a
    fixed mask token (never `None` for a present value -- a masked field
    reading as "absent" would misrepresent the record, not just hide it).
    A `None` value (no PHI captured at all) stays `None` regardless of
    role -- there's nothing to mask."""
    if value is None:
        return None
    if can(role, Action.VIEW_UNMASKED_PHI):
        return value
    return _MASK_TOKEN


def mask_patient_fields(
    role: Role, *, patient_name: str | None, patient_member_id: str | None
) -> tuple[str | None, str | None]:
    """Convenience wrapper for the one PHI field pair every claim-detail
    read path carries (`api/repository.py`'s `FindingDetail`) -- avoids
    two separate `mask_phi_value` call sites drifting out of sync on
    which fields count as PHI."""
    return (
        mask_phi_value(role, patient_name),
        mask_phi_value(role, patient_member_id),
    )
