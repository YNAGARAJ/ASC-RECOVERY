"""In-memory `Repository` fake -- facility/org-partitioned storage
implementing the same `api.repository.Repository` Protocol
`PostgresRepository` does. This is what lets the full role x endpoint x
facility authorization matrix run as real, passing tests without a live
Postgres (the same trick `ingestion.plan`/`apply` used in Phase 5).

Access resolution here (`_accessible_facility_ids`/`_accessible_org_ids`)
mirrors `resolve_accessible_facility_ids`/`resolve_accessible_org_ids`
(`alembic/versions/0001_initial_schema.py`) closely enough to exercise
API-layer wiring (routes, auth, masking) -- it is not a substitute for
`tests/db/test_rls_tenant_isolation.py`'s proof that RLS itself, at the
database, is what actually blocks a cross-facility read (register finding
B-50 already documents this as an accepted gap in what the runnable
suite alone can prove).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from api.repository import (
    AcceptedInvitation,
    AccessEventSummary,
    ApiKeyAuthLookup,
    ApiKeyListItem,
    ApiKeySummary,
    AuditLogEntry,
    AuditLogFilters,
    ContractSummary,
    ContractVersionInput,
    FindingDetail,
    FindingFilters,
    FindingSummary,
    InvitationPreview,
    InvitationSummary,
    LoginCredentials,
    OrgMemberSummary,
    Page,
    PagedResult,
    RecordOutcomeInput,
    RecoveryPacketSummary,
    UserRecord,
)
from domain.deadlines import calculate_appeal_deadline
from domain.outcomes import Outcome, validate_outcome_recording
from domain.variance import RootCause
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome
from ingestion.virus_scan import VirusScanner
from security.mfa import generate_enrollment_secret, provisioning_uri, verify_code
from security.passwords import hash_password
from security.phi_masking import mask_patient_fields
from security.rbac import Role
from security.tokens import generate_api_key, generate_token, hash_token

_DEFAULT_TIMELY_FILING_DAYS = 90
_INVITATION_TTL = timedelta(days=7)
_API_KEY_TTL = timedelta(days=365)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Membership:
    id: uuid.UUID
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    scope: str
    facility_ids: frozenset[uuid.UUID]
    created_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _ApiKey:
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    key_hash: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _Invitation:
    id: uuid.UUID
    org_id: uuid.UUID
    subject: str
    role: str
    scope: str
    facility_ids: frozenset[uuid.UUID]
    token_hash: str
    status: str
    expires_at: datetime


@dataclass
class FakeRepository:
    users: dict[str, UserRecord] = field(default_factory=dict)
    login_credentials: dict[str, LoginCredentials] = field(default_factory=dict)
    # org_id -> parent_org_id (None for a root org)
    organizations: dict[uuid.UUID, uuid.UUID | None] = field(default_factory=dict)
    # facility_id -> org_id
    facilities: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    memberships: list[_Membership] = field(default_factory=list)
    invitations: dict[uuid.UUID, _Invitation] = field(default_factory=dict)
    api_keys: dict[uuid.UUID, _ApiKey] = field(default_factory=dict)
    findings: dict[uuid.UUID, tuple[uuid.UUID, FindingDetail]] = field(default_factory=dict)
    contracts: dict[uuid.UUID, tuple[uuid.UUID, ContractSummary]] = field(default_factory=dict)
    audit_entries: dict[uuid.UUID, tuple[uuid.UUID, AuditLogEntry]] = field(default_factory=dict)
    packets: dict[uuid.UUID, tuple[uuid.UUID, RecoveryPacketSummary]] = field(default_factory=dict)
    access_events: list[tuple[uuid.UUID, AccessEventSummary]] = field(default_factory=list)
    ingest_calls: list[tuple[uuid.UUID, bytes, str, str]] = field(default_factory=list)
    next_ingest_outcome: IngestionOutcome | DuplicateOutcome | None = None
    healthy: bool = True

    # --- seeding helpers, test-only -------------------------------------

    def seed_user(self, subject: str) -> uuid.UUID:
        user_id = uuid.uuid4()
        self.users[subject] = UserRecord(id=user_id, subject=subject)
        return user_id

    def seed_organization(
        self, *, parent_org_id: uuid.UUID | None = None, org_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        org_id = org_id or uuid.uuid4()
        self.organizations[org_id] = parent_org_id
        return org_id

    def seed_facility(
        self, org_id: uuid.UUID, *, facility_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        facility_id = facility_id or uuid.uuid4()
        self.facilities[facility_id] = org_id
        return facility_id

    def seed_membership(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        role: Role | str,
        scope: str = "ALL_FACILITIES",
        facility_ids: frozenset[uuid.UUID] = frozenset(),
    ) -> uuid.UUID:
        membership_id = uuid.uuid4()
        role_value = role.value if isinstance(role, Role) else role
        self.memberships.append(
            _Membership(
                id=membership_id,
                user_id=user_id,
                org_id=org_id,
                role=role_value,
                scope=scope,
                facility_ids=facility_ids,
                created_at=now(),
            )
        )
        return membership_id

    def seed_api_key(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        name: str = "test key",
        raw_key: str | None = None,
        expires_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> str:
        """Test-only: seeds an `ApiKey` row directly, bypassing
        `create_api_key`'s service-user/membership creation, so a test can
        pin the raw key or force an expired/revoked state precisely.
        Returns the raw key (never stored -- only its hash is)."""
        raw_key = raw_key or generate_api_key()
        api_key_id = uuid.uuid4()
        self.api_keys[api_key_id] = _ApiKey(
            id=api_key_id,
            org_id=org_id,
            user_id=user_id,
            name=name,
            key_hash=hash_token(raw_key),
            created_at=now(),
            expires_at=expires_at or (now() + _API_KEY_TTL),
            revoked_at=revoked_at,
        )
        return raw_key

    def seed_login_credentials(
        self, subject: str, *, password: str | None, mfa_secret: str | None
    ) -> None:
        """`password=None`/`mfa_secret=None` seeds an unprovisioned
        credential (matching a NULL DB column) -- tests use that to prove
        the login route rejects it the same as a wrong value."""
        user = self.users.get(subject)
        default_org_id = self._default_membership_org_id(user.id) if user is not None else None
        self.login_credentials[subject] = LoginCredentials(
            user_id=user.id if user is not None else uuid.uuid4(),
            subject=subject,
            password_hash=None if password is None else hash_password(password),
            mfa_secret=mfa_secret,
            default_org_id=default_org_id,
        )

    def seed_finding(self, facility_id: uuid.UUID, detail: FindingDetail) -> uuid.UUID:
        self.findings[detail.summary.id] = (facility_id, detail)
        return detail.summary.id

    def seed_contract(self, org_id: uuid.UUID, summary: ContractSummary) -> None:
        self.contracts[summary.id] = (org_id, summary)

    def seed_audit_entry(self, facility_id: uuid.UUID, entry: AuditLogEntry) -> None:
        self.audit_entries[entry.id] = (facility_id, entry)

    # --- access resolution, mirrors alembic/versions/0001_initial_schema.py --

    def _descendant_org_ids(self, root_org_id: uuid.UUID) -> set[uuid.UUID]:
        result = {root_org_id}
        changed = True
        while changed:
            changed = False
            for org_id, parent_id in self.organizations.items():
                if parent_id in result and org_id not in result:
                    result.add(org_id)
                    changed = True
        return result

    def _accessible_org_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        result: set[uuid.UUID] = set()
        for m in self.memberships:
            if m.user_id == user_id and m.revoked_at is None:
                result |= self._descendant_org_ids(m.org_id)
        return result

    def _accessible_facility_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        result: set[uuid.UUID] = set()
        for m in self.memberships:
            if m.user_id != user_id or m.revoked_at is not None:
                continue
            if m.scope == "SPECIFIC_FACILITIES":
                result |= m.facility_ids
                continue
            accessible_orgs = self._descendant_org_ids(m.org_id)
            result |= {
                facility_id
                for facility_id, org_id in self.facilities.items()
                if org_id in accessible_orgs
            }
        return result

    def _default_membership_org_id(self, user_id: uuid.UUID) -> uuid.UUID | None:
        for m in self.memberships:
            if m.user_id == user_id and m.revoked_at is None:
                return m.org_id
        return None

    def resolve_membership_role(self, user_id: uuid.UUID, org_id: uuid.UUID) -> Role | None:
        """Walks from `org_id` up through `organizations` looking for the
        nearest membership -- mirrors db.repository.resolve_membership_role.
        A revoked membership is skipped, same as the real function's `AND
        m.revoked_at IS NULL` -- this is what makes offboarding
        (`FakeRepository.revoke_membership`) take effect on the very next
        call, exercised by `tests/api/test_offboarding.py`."""
        current: uuid.UUID | None = org_id
        visited: set[uuid.UUID] = set()
        for _ in range(20):
            if current is None or current in visited:
                return None
            visited.add(current)
            for m in self.memberships:
                if m.user_id == user_id and m.org_id == current and m.revoked_at is None:
                    return Role(m.role)
            current = self.organizations.get(current)
        return None

    def resolve_default_facility_id(
        self, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> uuid.UUID | None:
        matches = [fid for fid, oid in self.facilities.items() if oid == org_id]
        return matches[0] if len(matches) == 1 else None

    # --- Repository protocol ---------------------------------------------

    def ping(self) -> bool:
        return self.healthy

    def get_user_by_subject(self, subject: str) -> UserRecord | None:
        return self.users.get(subject)

    def get_login_credentials(self, subject: str) -> LoginCredentials | None:
        return self.login_credentials.get(subject)

    def ingest_remittance(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        *,
        content: bytes,
        source: str,
        uploaded_by: str,
        scanner: VirusScanner,
    ) -> IngestionOutcome | DuplicateOutcome:
        self.ingest_calls.append((facility_id, content, source, uploaded_by))
        scan_result = scanner.scan(content)
        if not scan_result.clean:
            return IngestionOutcome(
                remittance_id=uuid.uuid4(),
                status="quarantined",
                claims_created=0,
                findings_created=0,
                reconciliation_mismatches=0,
                dollars_detected=Decimal("0"),
            )
        if self.next_ingest_outcome is not None:
            return self.next_ingest_outcome
        return IngestionOutcome(
            remittance_id=uuid.uuid4(),
            status="ingested",
            claims_created=1,
            findings_created=1,
            reconciliation_mismatches=0,
            dollars_detected=Decimal("50.00"),
        )

    def list_findings(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, *, filters: FindingFilters, page: Page
    ) -> PagedResult[FindingSummary]:
        accessible = self._accessible_facility_ids(user_id)
        items = [
            detail.summary
            for fid, detail in self.findings.values()
            if fid == facility_id and fid in accessible
        ]
        if filters.root_cause is not None:
            items = [i for i in items if i.root_cause == filters.root_cause]
        if filters.claim_id is not None:
            items = [i for i in items if i.claim_id == filters.claim_id]
        if filters.min_shortfall is not None:
            items = [i for i in items if float(i.shortfall) >= float(filters.min_shortfall)]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def get_finding_detail(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        actor: str,
        role: Role,
    ) -> FindingDetail | None:
        entry = self.findings.get(finding_id)
        if entry is None:
            return None
        fid, detail = entry
        if fid != facility_id or fid not in self._accessible_facility_ids(user_id):
            # Cross-facility lookup by known id must miss, not 403 -- same
            # IDOR-shaped guarantee tests/db/test_rls_tenant_isolation.py
            # proves at the DB layer.
            return None
        self._record_access(
            facility_id,
            actor=actor,
            action="finding_detail_view",
            claim_id=detail.summary.claim_id,
            purpose="finding_detail_view",
        )
        patient_name, patient_member_id = mask_patient_fields(
            role, patient_name=detail.patient_name, patient_member_id=detail.patient_member_id
        )
        return replace(detail, patient_name=patient_name, patient_member_id=patient_member_id)

    def _record_access(
        self, facility_id: uuid.UUID, *, actor: str, action: str, claim_id: uuid.UUID, purpose: str
    ) -> None:
        event = AccessEventSummary(
            occurred_at=now(),
            actor=actor,
            action=action,
            resource_type="claim",
            resource_id=str(claim_id),
            purpose=purpose,
        )
        self.access_events.append((facility_id, event))

    def list_contracts(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ContractSummary]:
        accessible = self._accessible_org_ids(user_id)
        items = [
            summary
            for oid, summary in self.contracts.values()
            if oid == org_id and oid in accessible
        ]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def list_org_members(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[OrgMemberSummary]:
        accessible = self._accessible_org_ids(user_id)
        items = []
        if org_id in accessible:
            for m in self.memberships:
                if m.org_id != org_id or m.revoked_at is not None:
                    continue
                member_user = next((u for u in self.users.values() if u.id == m.user_id), None)
                subject = member_user.subject if member_user is not None else ""
                items.append(
                    OrgMemberSummary(
                        membership_id=m.id,
                        user_id=m.user_id,
                        subject=subject,
                        role=Role(m.role),
                        scope=m.scope,
                        facility_ids=tuple(m.facility_ids),
                        created_at=m.created_at,
                    )
                )
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def revoke_membership(self, user_id: uuid.UUID, membership_id: uuid.UUID) -> bool:
        """Mirrors the real `org_authoring_update` RLS policy: the caller
        must have resolved access to the target membership's org, and the
        membership must exist and not already be revoked -- both
        indistinguishable as a `False`/404, same as the real path."""
        accessible = self._accessible_org_ids(user_id)
        for index, m in enumerate(self.memberships):
            if m.id != membership_id:
                continue
            if m.revoked_at is not None or m.org_id not in accessible:
                return False
            self.memberships[index] = replace(m, revoked_at=now())
            return True
        return False

    def create_invitation(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        subject: str,
        role: Role,
        scope: str,
        facility_ids: Sequence[uuid.UUID] = (),
    ) -> InvitationSummary:
        raw_token = generate_token()
        invitation_id = uuid.uuid4()
        expires_at = now() + _INVITATION_TTL
        self.invitations[invitation_id] = _Invitation(
            id=invitation_id,
            org_id=org_id,
            subject=subject,
            role=role.value,
            scope=scope,
            facility_ids=frozenset(facility_ids),
            token_hash=hash_token(raw_token),
            status="pending",
            expires_at=expires_at,
        )
        return InvitationSummary(
            id=invitation_id,
            token=raw_token,
            subject=subject,
            role=role,
            scope=scope,
            expires_at=expires_at,
        )

    def _find_invitation_by_token(self, token: str) -> _Invitation | None:
        token_hash = hash_token(token)
        return next(
            (inv for inv in self.invitations.values() if inv.token_hash == token_hash), None
        )

    def get_invitation_preview(self, token: str) -> InvitationPreview | None:
        invitation = self._find_invitation_by_token(token)
        if invitation is None:
            return None
        return InvitationPreview(
            subject=invitation.subject,
            role=Role(invitation.role),
            scope=invitation.scope,
            status=invitation.status,
            expires_at=invitation.expires_at,
        )

    def accept_invitation(self, token: str, *, password: str) -> AcceptedInvitation | None:
        invitation = self._find_invitation_by_token(token)
        if invitation is None or invitation.status != "pending" or invitation.expires_at < now():
            return None
        user = self.users.get(invitation.subject)
        user_id = user.id if user is not None else self.seed_user(invitation.subject)
        membership_id = self.seed_membership(
            user_id,
            invitation.org_id,
            role=invitation.role,
            scope=invitation.scope,
            facility_ids=invitation.facility_ids,
        )
        mfa_secret = generate_enrollment_secret()
        # Membership must exist before this call -- seed_login_credentials
        # computes default_org_id from it once and caches it, matching how
        # PostgresRepository.get_login_credentials resolves it fresh from
        # `memberships` on every call (a fake-only ordering quirk, not a
        # real behavioral difference).
        self.seed_login_credentials(invitation.subject, password=password, mfa_secret=mfa_secret)
        self.invitations[invitation.id] = replace(invitation, status="accepted")
        return AcceptedInvitation(
            user_id=user_id,
            membership_id=membership_id,
            mfa_secret=mfa_secret,
            mfa_provisioning_uri=provisioning_uri(mfa_secret, invitation.subject),
        )

    def verify_invitation_mfa(self, token: str, *, totp_code: str) -> bool | None:
        invitation = self._find_invitation_by_token(token)
        if invitation is None or invitation.status != "accepted":
            return None
        credentials = self.login_credentials.get(invitation.subject)
        if credentials is None or credentials.mfa_secret is None:
            return None
        return verify_code(credentials.mfa_secret, totp_code)

    def create_api_key(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        name: str,
        scope: str,
        facility_ids: Sequence[uuid.UUID] = (),
    ) -> ApiKeySummary:
        service_user_id = self.seed_user(f"api-key:{uuid.uuid4()}")
        self.seed_membership(
            service_user_id,
            org_id,
            role=Role.API_SERVICE,
            scope=scope,
            facility_ids=frozenset(facility_ids),
        )
        raw_key = generate_api_key()
        api_key_id = uuid.uuid4()
        expires_at = now() + _API_KEY_TTL
        self.api_keys[api_key_id] = _ApiKey(
            id=api_key_id,
            org_id=org_id,
            user_id=service_user_id,
            name=name,
            key_hash=hash_token(raw_key),
            created_at=now(),
            expires_at=expires_at,
        )
        return ApiKeySummary(
            id=api_key_id,
            key=raw_key,
            name=name,
            scope=scope,
            facility_ids=tuple(facility_ids),
            expires_at=expires_at,
        )

    def list_api_keys(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ApiKeyListItem]:
        accessible = self._accessible_org_ids(user_id)
        items = []
        if org_id in accessible:
            items = [
                ApiKeyListItem(
                    id=k.id,
                    name=k.name,
                    created_at=k.created_at,
                    expires_at=k.expires_at,
                    revoked_at=k.revoked_at,
                    last_used_at=k.last_used_at,
                )
                for k in self.api_keys.values()
                if k.org_id == org_id
            ]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def revoke_api_key(self, user_id: uuid.UUID, api_key_id: uuid.UUID) -> bool:
        accessible = self._accessible_org_ids(user_id)
        key = self.api_keys.get(api_key_id)
        if key is None or key.revoked_at is not None or key.org_id not in accessible:
            return False
        self.api_keys[api_key_id] = replace(key, revoked_at=now())
        return True

    def get_api_key_for_auth(self, raw_key: str) -> ApiKeyAuthLookup | None:
        key_hash = hash_token(raw_key)
        for k in self.api_keys.values():
            if k.key_hash == key_hash:
                return ApiKeyAuthLookup(
                    id=k.id,
                    org_id=k.org_id,
                    user_id=k.user_id,
                    name=k.name,
                    revoked_at=k.revoked_at,
                    expires_at=k.expires_at,
                )
        return None

    def touch_api_key_last_used(self, user_id: uuid.UUID, api_key_id: uuid.UUID) -> None:
        key = self.api_keys.get(api_key_id)
        if key is not None:
            self.api_keys[api_key_id] = replace(key, last_used_at=now())

    def create_contract(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, payer_id: str, name: str
    ) -> ContractSummary:
        summary = ContractSummary(id=uuid.uuid4(), payer_id=payer_id, name=name, created_at=now())
        self.contracts[summary.id] = (org_id, summary)
        return summary

    def create_contract_version(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        contract_id: uuid.UUID,
        data: ContractVersionInput,
    ) -> uuid.UUID:
        return uuid.uuid4()

    def list_audit_log(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, *, filters: AuditLogFilters, page: Page
    ) -> PagedResult[AuditLogEntry]:
        accessible = self._accessible_facility_ids(user_id)
        items = [
            entry
            for fid, entry in self.audit_entries.values()
            if fid == facility_id and fid in accessible
        ]
        total = len(items)
        page_items = items[page.offset : page.offset + page.limit]
        return PagedResult(items=page_items, total=total, limit=page.limit, offset=page.offset)

    def write_audit_log(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        phi_accessed: bool,
        request_id: str | None,
    ) -> None:
        entry = AuditLogEntry(
            id=uuid.uuid4(),
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=now(),
            source_ip=None,
            phi_accessed=phi_accessed,
            request_id=request_id,
        )
        self.audit_entries[entry.id] = (facility_id, entry)

    def generate_packet(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        generated_by: str,
    ) -> RecoveryPacketSummary | None:
        """Only proves facility-scoping/state-machine plumbing through the
        API layer -- the actual currency/PHI-safety logic this stands in
        for is proven directly against `packets.service` in
        tests/packets/, not re-tested redundantly here."""
        entry = self.findings.get(finding_id)
        if entry is None or entry[0] != facility_id or facility_id not in (
            self._accessible_facility_ids(user_id)
        ):
            return None
        _, detail = entry
        self._record_access(
            facility_id,
            actor=generated_by,
            action="packet_generation",
            claim_id=detail.summary.claim_id,
            purpose="packet_generation",
        )
        packet = RecoveryPacketSummary(
            id=uuid.uuid4(),
            finding_id=finding_id,
            status="draft",
            draft_text=f"Draft appeal letter for finding {finding_id}.",
            deadline=calculate_appeal_deadline(now().date(), _DEFAULT_TIMELY_FILING_DAYS),
            generated_by=generated_by,
            generated_at=now(),
            decided_by=None,
            decided_at=None,
        )
        self.packets[packet.id] = (facility_id, packet)
        return packet

    def list_packets(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> list[RecoveryPacketSummary]:
        accessible = self._accessible_facility_ids(user_id)
        entry = self.findings.get(finding_id)
        if entry is not None and entry[0] == facility_id and facility_id in accessible:
            self._record_access(
                facility_id,
                actor=actor,
                action="packet_list_view",
                claim_id=entry[1].summary.claim_id,
                purpose="packet_list_view",
            )
        return [
            packet
            for fid, packet in self.packets.values()
            if fid == facility_id and fid in accessible and packet.finding_id == finding_id
        ]

    def get_claim_access_history(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[AccessEventSummary, ...]:
        accessible = self._accessible_facility_ids(user_id)
        events = [
            event
            for fid, event in self.access_events
            if fid == facility_id and fid in accessible and event.resource_id == str(claim_id)
        ]
        return tuple(sorted(events, key=lambda event: event.occurred_at))

    def decide_packet(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        packet_id: uuid.UUID,
        *,
        approve: bool,
        decided_by: str,
    ) -> RecoveryPacketSummary | None:
        entry = self.packets.get(packet_id)
        if entry is None or entry[0] != facility_id or facility_id not in (
            self._accessible_facility_ids(user_id)
        ):
            return None
        _, packet = entry
        updated = replace(
            packet,
            status="approved" if approve else "rejected",
            decided_by=decided_by,
            decided_at=now(),
        )
        self.packets[packet_id] = (facility_id, updated)
        return updated

    def record_finding_outcome(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        data: RecordOutcomeInput,
        recorded_by: str,
    ) -> FindingSummary | None:
        entry = self.findings.get(finding_id)
        if entry is None or entry[0] != facility_id or facility_id not in (
            self._accessible_facility_ids(user_id)
        ):
            return None
        _, detail = entry
        existing_outcome = Outcome(detail.summary.outcome) if detail.summary.outcome else None
        validate_outcome_recording(RootCause[detail.summary.root_cause], existing_outcome)
        updated_summary = replace(
            detail.summary,
            outcome=data.outcome,
            amount_recovered=None if data.amount_recovered is None else str(data.amount_recovered),
            outcome_recorded_by=recorded_by,
            outcome_recorded_at=now(),
        )
        self.findings[finding_id] = (facility_id, replace(detail, summary=updated_summary))
        return updated_summary
