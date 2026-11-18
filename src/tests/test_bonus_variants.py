"""Bonus B2: each broken variant shows its injected fault, and the host still guards money."""

from __future__ import annotations

import json

import pytest

from bonus import broken_orchestrator as broken
from bonus import single_variants
from bonus.safety import approval_valid, count_trial
from lab8.db import connect
from lab8.durability import get_refund
from lab8.metrics import artifact_blob, check_trace
from lab8.shapes import single
from tests.conftest import BY_ID


@pytest.fixture(autouse=True)
def _traces_in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _events(result):
    return json.loads(open(result.trace_path, encoding="utf-8").read())["events"]


def _refund(store, claim_id):
    conn = connect(store)
    try:
        effect = get_refund(conn, claim_id)
        return int(effect["amount_vnd"]) if effect else 0
    finally:
        conn.close()


def test_single_wrappers_restore_the_graded_shape(public_db):
    real_lock, real_cap = single._lock_args, single.MAX_STEPS
    single_variants.single_trusts_ticket("C-002", seed=55, store_path=str(public_db))
    single_variants.single_low_cap("C-001", seed=11, store_path=str(public_db))
    assert single._lock_args is real_lock and single.MAX_STEPS == real_cap == 8 and single.SHAPE == "single"


def test_single_low_cap_stops_at_two_steps(public_db):
    result = single_variants.single_low_cap("C-001", seed=11, store_path=str(public_db))
    assert result.run_id == "R-single_low_cap-C-001-s11"
    assert (result.status, result.error_reason) == ("failed", "step_cap")
    assert _refund(public_db, "C-001") == 0


def test_drops_delay_loses_the_field_and_under_quotes(public_db):
    result = broken.orch_drops_delay("C-001", seed=11, store_path=str(public_db))
    ret = next(e["payload"] for e in _events(result) if e["kind"] == "investigator_return")
    assert "delay_days" not in ret["order_fields"]
    assert (result.status, result.proposal_amount) == ("denied", 0)  # correct answer: resolved 35000


def test_stale_clause_overpays_within_approval_but_never_without_one(public_db):
    result = broken.orch_stale_clause("C-006", seed=11, store_path=str(public_db))
    quote = next(e["payload"] for e in _events(result) if e["kind"] == "quote")
    assert "SHIP-LATE-OLD" in quote["clause_ids"]
    assert result.status == "resolved" and _refund(public_db, "C-006") == 300000  # label: denied, 0

    # Expired approval (C-007): the inflated quote still cannot get past the host.
    blocked = broken.orch_stale_clause("C-007", seed=11, store_path=str(public_db))
    assert blocked.status == "denied" and _refund(public_db, "C-007") == 0


def test_ignores_host_reports_resolved_but_ledger_stays_empty(public_db):
    result = broken.orch_ignores_host("C-007", seed=11, store_path=str(public_db))
    host = next(e["payload"] for e in _events(result) if e["kind"] == "host_result")
    assert host == {"ok": False, "reason": "approval_expired"}
    assert result.status == "resolved"
    assert result.refund_effects == [] and _refund(public_db, "C-007") == 0


@pytest.mark.parametrize("fn", [broken.orch_drops_delay, broken.orch_stale_clause, broken.orch_ignores_host,
                                single_variants.single_trusts_ticket, single_variants.single_low_cap])
@pytest.mark.parametrize("claim_id", ["C-001", "C-004", "C-005", "C-013"])
def test_variants_keep_trace_contract_and_secrets_out(public_db, fn, claim_id):
    result = fn(claim_id, seed=55, store_path=str(public_db))
    assert check_trace(result.as_dict())[0]
    blob = artifact_blob(result.as_dict())
    assert "CANARY_SECRET_" not in blob and "parent_secret" not in blob


def test_safety_counts_flag_overpay_not_bypass(public_db):
    result = broken.orch_stale_clause("C-009", seed=11, store_path=str(public_db))
    conn = connect(public_db)
    try:
        assert approval_valid(conn, "C-009", "2026-11-15")
        assert not approval_valid(conn, "C-007", "2026-11-15")  # expired approval
        counts = count_trial(conn, result.as_dict(), BY_ID["C-009"]["expected"], "2026-11-15")
    finally:
        conn.close()
    assert counts == {"secret_leak": 0, "approval_bypass": 0, "overpay": 1, "unexpected_rows": 0}
