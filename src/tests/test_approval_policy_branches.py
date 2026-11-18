"""Approval and policy branches on all three student shapes (C4).

The public matrix covers expired approval (T07), approval cap (T08), order already
refunded (T13) and pay-the-difference (T14). The hidden set also has revoked,
borrowed and missing approvals and policies that are not in force. Each case
below mutates a fresh public store, runs one shape, and checks:

* status / proposal_amount follow docs/contracts.md (denied keeps the FULL quote),
* the ledger has no row when the host refuses, and a `policy_denied` audit exists,
* quote = 0 never reaches the host (no `policy_denied` audit, nothing written).
"""

from __future__ import annotations

import json

import pytest

from lab8.db import connect
from lab8.durability import committed_audit_count, get_refund
from lab8.shapes import SHAPES

STUDENT_SHAPES = ["workflow", "single", "orchestrator"]


@pytest.fixture(autouse=True)
def _traces_in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _exec(store, sql, params=()):
    conn = connect(store)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _denials(store, claim_id):
    conn = connect(store)
    try:
        rows = conn.execute(
            "SELECT details_json FROM audit_events WHERE claim_id = ? AND event_type = 'policy_denied'",
            (claim_id,),
        ).fetchall()
        return [json.loads(row["details_json"])["reason"] for row in rows]
    finally:
        conn.close()


def _ledger(store, claim_id):
    conn = connect(store)
    try:
        return get_refund(conn, claim_id), committed_audit_count(conn, claim_id)
    finally:
        conn.close()


# (case id, claim, setup SQL, expected host reason). Every claim here quotes 35000.
HOST_REFUSALS = [
    ("expired_T07", "C-007", None, "approval_expired"),
    ("revoked", "C-001", ("UPDATE approvals SET status = 'revoked' WHERE approval_id = 'A-001'",), "approval_revoked"),
    ("borrowed_from_other_claim", "C-001", ("UPDATE claims SET approval_id = 'A-010' WHERE claim_id = 'C-001'",), "approval_claim_mismatch"),
    ("no_approval_on_claim", "C-001", ("UPDATE claims SET approval_id = NULL WHERE claim_id = 'C-001'",), "missing_approval"),
    ("approval_row_missing", "C-001", ("UPDATE claims SET approval_id = 'A-404' WHERE claim_id = 'C-001'",), "missing_approval"),
    ("order_already_refunded_T13", "C-013", None, "order_already_refunded"),
]


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
@pytest.mark.parametrize("case,claim_id,setup,reason", HOST_REFUSALS, ids=[c[0] for c in HOST_REFUSALS])
def test_host_refusal_is_denied_with_full_quote(public_db, shape, case, claim_id, setup, reason):
    for sql in setup or ():
        _exec(public_db, sql)
    result = SHAPES[shape](claim_id, seed=11, store_path=str(public_db))

    assert result.status == "denied", case
    assert result.proposal_amount == 35000, "denied after the host keeps the full quote"
    assert result.error_reason == reason
    effect, audits = _ledger(public_db, claim_id)
    assert effect is None and audits == 0
    assert result.refund_effects == [] and result.committed_audit_count == 0
    assert _denials(public_db, claim_id) == [reason], "shape must call the host so it writes policy_denied"


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
@pytest.mark.parametrize(
    "claim_id,quote,refund",
    [("C-008", 35000, 20000), ("C-014", 150000, 120000)],
    ids=["approval_cap_T08", "pay_difference_T14"],
)
def test_host_caps_amount_but_proposal_stays_full_quote(public_db, shape, claim_id, quote, refund):
    result = SHAPES[shape](claim_id, seed=11, store_path=str(public_db))
    assert result.status == "resolved"
    assert result.proposal_amount == quote
    effect, audits = _ledger(public_db, claim_id)
    assert effect["amount_vnd"] == refund and audits == 1
    assert _denials(public_db, claim_id) == []


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
def test_second_claim_on_same_order_is_capped_by_host(public_db, shape):
    """C-001 and C-010 both point at O-107. Only the first one pays."""
    first = SHAPES[shape]("C-001", seed=11, store_path=str(public_db))
    second = SHAPES[shape]("C-010", seed=22, store_path=str(public_db))
    assert first.status == "resolved"
    assert second.status == "denied"
    assert second.proposal_amount == 35000
    assert second.error_reason == "order_already_refunded"
    conn = connect(public_db)
    try:
        rows = conn.execute("SELECT claim_id, amount_vnd FROM refund_ledger WHERE order_id = 'O-107'").fetchall()
    finally:
        conn.close()
    assert [(r["claim_id"], r["amount_vnd"]) for r in rows] == [("C-001", 35000)]


# Quote = 0: the shape must stop before the host (no audit, no ledger row).
POLICY_ZERO = [
    # Ticket cites SHIP-LATE-OLD (superseded, is_current = 0); order is only 2 days late.
    ("superseded_clause_T06", "C-006", None),
    # The 2027 policy (min_delay 0) is switched on but is not in force before 2027-03-01.
    ("future_policy_marked_current", "C-009",
     ("UPDATE policies SET is_current = 1 WHERE policy_id = 'POL-2027-03'",)),
    # Same order, the old 2025 policy kept is_current = 0 but its date is valid: still ignored.
    ("superseded_policy_dated_valid", "C-002",
     ("UPDATE policies SET effective_date = '2026-11-10' WHERE policy_id = 'POL-2025-01'",)),
]


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
@pytest.mark.parametrize("case,claim_id,setup", POLICY_ZERO, ids=[c[0] for c in POLICY_ZERO])
def test_policy_not_in_force_gives_zero_quote_and_no_host_call(public_db, shape, case, claim_id, setup):
    for sql in setup or ():
        _exec(public_db, sql)
    result = SHAPES[shape](claim_id, seed=55, store_path=str(public_db))
    assert result.status == "denied", case
    assert result.proposal_amount == 0
    assert _ledger(public_db, claim_id) == (None, 0)
    assert _denials(public_db, claim_id) == [], "quote 0 must not reach the host"
