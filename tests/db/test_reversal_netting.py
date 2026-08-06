"""F-01 (docs/audit/REGISTER.md, the audit's one CRITICAL): a reversal claim
reporting *fewer* service lines than the original claim it reverses must
still net every original finding to exactly zero, with each reversing entry
attached to the *original* finding's own service line -- never silently
dropped, and never mis-attached to whatever line happens to share the same
index on the reversal claim.

Before the fix, `ingestion.apply._apply_claim` mapped a reversing finding's
`line_index` against the *reversal* claim's own service-line dict. A
reversal with fewer lines than the original meant some reversing findings
had no matching index at all and were silently dropped -- overstating what
the system reports as still recoverable. This test builds an original claim
with two service lines and a reversal claim with only one, so a regression
back to that behavior shows up as `finding_count != 0` and/or
`sum(shortfall) != 0` below, not as a false pass.

Requires a live Postgres -- see tests/ingestion/conftest.py. Never executed
in this environment; written now, verified the next time this branch's CI
runs against a real Postgres 16.
"""

from __future__ import annotations

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from db.access import access_session
from db.models import Finding as FindingModel
from db.models import ServiceLine as ServiceLineModel
from domain.money import Money
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from tests.domain.fixtures_x835 import (
    ELEMENT_SEP,
    SUB_ELEMENT_SEP,
    assemble,
    envelope_head,
    envelope_tail,
    plb_segment,
    seg,
)
from tests.ingestion.conftest import make_test_encryptor, seed_org_with_contract

_PAYER_CTRL = "PAYERCTRL-REVTEST-01"


def _original_claim_two_lines() -> str:
    """One claim, two service lines (99213 charge/paid 300.00, 99214
    charge/paid 200.00, no CAS so allowed == charge for each), both priced
    far below a $50/$60 fee schedule so each line produces a real finding."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIMORIG01",
            "1",
            "500.00",
            "500.00",
            "0.00",
            "12",
            _PAYER_CTRL,
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT ONE",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000001",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "82",
            "2",
            "TEST RENDERING PROVIDER",
            "",
            "",
            "",
            "",
            "XX",
            "1999999999",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "300.00", "300.00", "", "1"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99214", "200.00", "200.00", "", "1"),
        plb_segment(),
        *envelope_tail(segment_count="17"),
    ]
    return assemble(segments)


def _reversal_claim_one_line() -> str:
    """The same claim reversed (CLP02=22), reporting only ONE service line
    -- deliberately fewer than the two the original claim had, and the
    shape that triggered F-01's silent-drop / wrong-line-attachment bug."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIMREV01",
            "22",
            "300.00",
            "-300.00",
            "0.00",
            "12",
            _PAYER_CTRL,
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT ONE",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000001",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "82",
            "2",
            "TEST RENDERING PROVIDER",
            "",
            "",
            "",
            "",
            "XX",
            "1999999999",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "300.00", "-300.00", "", "1"),
        plb_segment(),
        *envelope_tail(segment_count="17"),
    ]
    return assemble(segments)


def test_reversal_with_fewer_lines_than_original_nets_every_finding_to_zero(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(
        owner_engine,
        "Reversal netting tenant",
        fee_schedule={"99213": Money("50.00"), "99214": Money("60.00")},
    )
    scanner = EicarAwareScanner()

    with access_session(app_session_factory, user_id) as session:
        original_outcome = ingest_file(
            session,
            facility_id,
            content=_original_claim_two_lines().encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=scanner,
            encryptor=make_test_encryptor(),
        )
    assert isinstance(original_outcome, IngestionOutcome)
    assert original_outcome.status == "ingested"
    assert original_outcome.findings_created == 2

    with access_session(app_session_factory, user_id) as session:
        original_service_line_ids = set(
            session.execute(
                select(ServiceLineModel.id).where(ServiceLineModel.facility_id == facility_id)
            )
            .scalars()
            .all()
        )
        assert len(original_service_line_ids) == 2

    with access_session(app_session_factory, user_id) as session:
        reversal_outcome = ingest_file(
            session,
            facility_id,
            content=_reversal_claim_one_line().encode("utf-8"),
            source="upload",
            uploaded_by="tester",
            scanner=scanner,
            encryptor=make_test_encryptor(),
        )
    assert isinstance(reversal_outcome, IngestionOutcome)
    assert reversal_outcome.status == "ingested"
    # Both original findings must be reversed, even though the reversal
    # claim itself reported only one service line -- this is the exact
    # count that silently dropped to 1 before the F-01 fix.
    assert reversal_outcome.findings_created == 2

    with access_session(app_session_factory, user_id) as session:
        all_findings = (
            session.execute(select(FindingModel).where(FindingModel.facility_id == facility_id))
            .scalars()
            .all()
        )
        total_finding_count = session.execute(
            select(func.count())
            .select_from(FindingModel)
            .where(FindingModel.facility_id == facility_id)
        ).scalar_one()

    assert total_finding_count == 4  # 2 original + 2 reversing
    net_shortfall = sum((f.shortfall for f in all_findings), Money.zero().as_decimal())
    assert net_shortfall == Money.zero().as_decimal()

    # Every reversing finding's service_line_id must be one of the
    # ORIGINAL claim's two service lines -- never the reversal claim's own
    # single line, and never dropped for lack of a matching index.
    reversing = [f for f in all_findings if f.shortfall < 0]
    assert len(reversing) == 2
    for finding in reversing:
        assert finding.service_line_id in original_service_line_ids
