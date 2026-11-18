"""Bonus B2: the MAST tagger rules, agreement maths and an end-to-end run on real traces."""

from __future__ import annotations

import pytest

from bonus import mast_tag
from lab8.benchmark import run_benchmark
from lab8.app import register_extra_shapes
from lab8.shapes import SHAPES
from tests.conftest import BY_ID, CLAIMS, WORLD

CURRENT = mast_tag.current_clause_ids(WORLD)
ORDERS = {o["order_id"]: o for o in WORLD["orders"]}


def _tag(row, events, claim_id="C-001"):
    claim = BY_ID[claim_id]
    ev = mast_tag.trace_evidence({"events": events})
    return mast_tag.tag_trial(row, ev, claim, ORDERS.get(claim["order_id"]), CURRENT)


def test_current_clauses_exclude_superseded_and_future():
    assert "SHIP-LATE-03" in CURRENT and "DMG-04" in CURRENT
    assert "SHIP-LATE-OLD" not in CURRENT and "SHIP-LATE-FUTURE" not in CURRENT


@pytest.mark.parametrize("row,events,claim_id,mode,group", [
    ({"reason": "exception:KeyError:x", "status": "error"}, [], "C-001", None, "infra"),
    ({"reason": "status resolved != denied", "status": "resolved"},
     [{"kind": "host_result", "payload": {"ok": False, "reason": "approval_expired"}}], "C-007", "FM-3.2", "FC3"),
    ({"reason": "status failed != resolved", "status": "failed"},
     [{"kind": "decision", "payload": {"status": "failed", "reason": "step_cap"}}], "C-001", "FM-1.5", "FC1"),
    ({"reason": "proposal_mismatch", "status": "resolved"},
     [{"kind": "quote", "payload": {"amount_vnd": 250000, "clause_ids": ["SHIP-LATE-03", "SHIP-LATE-OLD"]}}], "C-001", "FM-1.1", "FC1"),
    ({"reason": "status denied != resolved", "status": "denied"},
     [{"kind": "investigator_return", "payload": {"order_visible": True, "order_fields": ["order_id", "status"]}}], "C-001", "FM-2.4", "FC2"),
    # Single-agent trace format: quote sits inside a react_step observation.
    ({"reason": "status resolved != denied", "status": "resolved"},
     [{"kind": "react_step", "payload": {"observed": {"tool": "compute_compensation", "amount_vnd": 20000, "clause_ids": ["SHIP-LATE-03"]}}}],
     "C-002", "FM-3.3", "FC3"),
    ({"reason": "status resolved != denied", "status": "resolved"},
     [{"kind": "model_override", "payload": {"delay_days": 5}}, {"kind": "quote", "payload": {"amount_vnd": 20000, "clause_ids": ["SHIP-LATE-03"]}}],
     "C-002", "FM-3.3", "FC3"),
    ({"reason": "refund_mismatch", "status": "resolved"}, [], "C-001", None, "unknown"),
])
def test_each_rule_maps_evidence_to_one_mode(row, events, claim_id, mode, group):
    tag = _tag(row, events, claim_id)
    assert (tag["mode"], tag["group"]) == (mode, group)
    assert tag["why"]


def test_stale_clause_wins_over_ticket_disagreement():
    """C-002 has a lying ticket too; the clause id is the stronger, earlier evidence."""
    tag = _tag({"reason": "status resolved != denied", "status": "resolved"},
               [{"kind": "quote", "payload": {"amount_vnd": 100000, "clause_ids": ["SHIP-LATE-OLD"]}}], "C-002")
    assert tag["mode"] == "FM-1.1"


def test_ticket_rule_needs_overpayment():
    """A ticket that disagrees with the order is not a failure mode by itself."""
    tag = _tag({"reason": "refund_mismatch", "status": "denied"},
               [{"kind": "quote", "payload": {"amount_vnd": 0, "clause_ids": []}}], "C-002")
    assert tag["group"] == "unknown"


def test_cohen_kappa_known_values():
    assert mast_tag.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert mast_tag.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    # 3/4 observed, chance 0.5 -> 0.5
    assert mast_tag.cohen_kappa(["a", "a", "b", "b"], ["a", "a", "b", "a"]) == pytest.approx(0.5)
    assert mast_tag.cohen_kappa([], []) is None


def test_agreement_reports_disagreements_and_missing():
    tags = {"R1": {"group": "FC1", "mode": "FM-1.1"}, "R2": {"group": "FC3", "mode": "FM-3.2"}}
    labels = [{"run_id": "R1", "group": "FC1", "mode": "FM-1.1"},
              {"run_id": "R2", "group": "FC3", "mode": "FM-3.3"},
              {"run_id": "R9", "group": "FC2", "mode": "FM-2.4"}]
    out = mast_tag.agreement(tags, labels)
    assert out["compared"] == 2 and out["missing_from_tags"] == ["R9"]
    assert out["group"]["agreement"] == 1.0
    assert out["mode"]["agreement"] == 0.5
    assert out["mode"]["disagreements"] == [{"run_id": "R2", "human": "FM-3.3", "tagger": "FM-3.2"}]


def test_end_to_end_tags_each_variant_with_its_injected_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    specs = [
        "single_trusts_ticket=bonus.single_variants:single_trusts_ticket",
        "orch_drops_delay=bonus.broken_orchestrator:orch_drops_delay",
        "orch_stale_clause=bonus.broken_orchestrator:orch_stale_clause",
        "orch_ignores_host=bonus.broken_orchestrator:orch_ignores_host",
    ]
    names = []
    try:
        names = register_extra_shapes(specs)
        claims = [c for c in CLAIMS if c["claim_id"] in ("C-001", "C-002", "C-007", "C-013")]
        bench = run_benchmark(world=WORLD, claims=claims, shapes=["workflow", *names], seeds=[11, 55])
    finally:
        for name in names:
            SHAPES.pop(name, None)
    report = mast_tag.run(bench, traces_dir=tmp_path / "traces", claims=CLAIMS, world=WORLD)
    modes = report["counts_by_shape_and_mode"]
    assert "workflow" not in report["counts_by_shape_and_group"]
    assert set(modes["single_trusts_ticket"]) == {"FM-3.3"}
    assert set(modes["orch_drops_delay"]) == {"FM-2.4"}
    assert set(modes["orch_stale_clause"]) == {"FM-1.1"}
    assert set(modes["orch_ignores_host"]) == {"FM-3.2"}
    assert all(tag["trace_found"] for tag in report["tags"].values())
