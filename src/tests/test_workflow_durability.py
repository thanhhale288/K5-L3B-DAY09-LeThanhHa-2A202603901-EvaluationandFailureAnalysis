import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.shapes.workflow import run_claim


def test_workflow_retry_keeps_one_refund_and_audit(public_db):
    first = run_claim("C-011", seed=11, store_path=str(public_db))
    second = run_claim("C-011", seed=11, store_path=str(public_db))

    conn = connect(public_db)
    try:
        effect = get_refund(conn, "C-011")
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = ?",
            ("C-011",),
        ).fetchone()["n"]
        audits = committed_audit_count(conn, "C-011")
    finally:
        conn.close()

    assert first.status == "resolved"
    assert second.status == "resolved"
    assert effect is not None
    assert count == 1
    assert audits == 1


def test_workflow_crash_resume_keeps_one_refund_and_audit(public_db):
    with pytest.raises(InjectedCrash):
        run_claim(
            "C-012",
            seed=11,
            store_path=str(public_db),
            crash_at="after_effect_before_checkpoint",
        )

    result = run_claim("C-012", seed=11, store_path=str(public_db))
    conn = connect(public_db)
    try:
        effect = get_refund(conn, "C-012")
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = ?",
            ("C-012",),
        ).fetchone()["n"]
        audits = committed_audit_count(conn, "C-012")
    finally:
        conn.close()

    assert result.status == "resolved"
    assert effect is not None
    assert count == 1
    assert audits == 1
