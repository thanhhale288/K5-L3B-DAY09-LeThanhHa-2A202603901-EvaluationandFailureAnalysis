"""Orchestrator variants with one injected fault each (bonus B2).

The flow mirrors `lab8.shapes.orchestrator`: claim view -> handoff -> read-only
investigators -> quote -> host. Each variant breaks exactly one step:

* `orch_drops_delay` (FM-2.4 Information withholding): the investigator's order
  view loses `delay_days` on the way back; the orchestrator fills the gap with 0.
* `orch_stale_clause` (FM-1.1 Disobey task specification): the quote is computed
  over every policy row, treating superseded ones as in force.
* `orch_ignores_host` (FM-3.2 No or incomplete verification): the orchestrator
  reports `resolved` without checking whether the host accepted the refund.

The host still guards money: approval, per-order cap and idempotency are checked
in `durability.issue_refund` for every variant. Traces record only field names,
clause ids, amounts and host verdicts, never ticket text or order rows.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.compensation import quote as compensation_quote
from lab8.config_loader import as_of
from lab8.db import connect
from lab8.durability import issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import _policy_dict, compute_compensation, get_claim_view, tool_cost
from lab8.trace import Trace

FAULTS = ("drops_delay", "stale_clause", "ignores_host")


def _quote_all_policies(conn: sqlite3.Connection, order: dict[str, Any]) -> dict[str, Any]:
    """The stale-clause fault: every policy row counts as current."""
    policies = [dict(_policy_dict(row), is_current=True) for row in conn.execute("SELECT * FROM policies")]
    result = compensation_quote(order, policies, as_of=as_of())
    return {"amount_vnd": result.amount_vnd, "reason": result.reason, "clause_ids": result.clause_ids}


def _run(claim_id: str, *, seed: int, store_path: str, crash_at: str | None, fault: str) -> RunResult:
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault}")
    shape = f"orch_{fault}"
    conn = connect(store_path)
    budget = Budget()
    trace = Trace(make_run_id(shape, claim_id, seed))

    def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
        complete(budget, seed=seed, purpose="draft", prompt=f"status={status};proposal={proposal}")
        trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
        return finish(
            conn, shape=shape, claim_id=claim_id, seed=seed,
            status=status, proposal_amount=proposal, budget=budget,
            trace=trace, error_reason=reason,
        )

    try:
        budget.add_tool(tool_cost())
        claim = get_claim_view(conn, claim_id)
        if not claim["found"]:
            return done("failed", reason="unknown_claim")
        if not claim.get("order_id"):
            return done("needs_info", reason="missing_order_id")

        complete(budget, seed=seed, purpose="plan", prompt="Investigate owner order and current policy using read-only tools.")
        handoff = make_handoff(
            request_id=f"REQ-{claim_id}", claim_id=claim_id,
            task="check_applicable_policy", facts={},
            allowed_tools=["lookup_order", "lookup_policy"],
        )
        trace.add("handoff", safe_dump(handoff))
        investigated = run_readonly_investigators(conn, budget, handoff, principal_id=claim["principal_id"])
        order_view = investigated["tools"].get("lookup_order", {})
        if not order_view.get("visible"):
            trace.add("investigator_return", {"order_visible": False})
            return done("escalated", reason=order_view.get("reason"))

        order = dict(order_view["order"])
        if fault == "drops_delay":
            order.pop("delay_days", None)  # the injected fault: lost between agents
        trace.add("investigator_return", {"order_visible": True, "order_fields": sorted(order)})

        budget.add_tool(tool_cost())
        if fault == "stale_clause":
            quote = _quote_all_policies(conn, order)
        else:
            order.setdefault("delay_days", 0)
            quote = compute_compensation(conn, order)
        proposal = int(quote["amount_vnd"])
        trace.add("quote", {"amount_vnd": proposal, "reason": quote.get("reason"), "clause_ids": list(quote.get("clause_ids") or [])})
        if proposal <= 0:
            return done("denied", reason=quote.get("reason"))

        budget.add_tool(tool_cost())
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        trace.add("host_result", {"ok": outcome["ok"], "reason": outcome["reason"]})
        if fault == "ignores_host":
            # The injected fault: the host verdict is not checked.
            return done("failed" if outcome["conflict"] else "resolved", proposal, outcome["reason"])
        if not outcome["ok"]:
            return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
        return done("resolved", proposal)
    finally:
        conn.close()


def orch_drops_delay(claim_id: str, *, seed: int, store_path: str, crash_at: str | None = None) -> RunResult:
    return _run(claim_id, seed=seed, store_path=store_path, crash_at=crash_at, fault="drops_delay")


def orch_stale_clause(claim_id: str, *, seed: int, store_path: str, crash_at: str | None = None) -> RunResult:
    return _run(claim_id, seed=seed, store_path=store_path, crash_at=crash_at, fault="stale_clause")


def orch_ignores_host(claim_id: str, *, seed: int, store_path: str, crash_at: str | None = None) -> RunResult:
    return _run(claim_id, seed=seed, store_path=store_path, crash_at=crash_at, fault="ignores_host")
