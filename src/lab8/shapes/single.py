"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.

How this shape splits the work
------------------------------
* Code reads the claim (`get_claim_view`) before the loop. Unknown claim and a
  missing order_id end there, without spending ReAct steps.
* Inside the loop the "model" only names the next read tool. Every step costs
  one MockLLM call (`complete`). The tool choice comes from `_policy`, a
  scripted stand-in for the model: MockLLM returns canned text and cannot pick
  tools itself.
* `_lock_args` rewrites the arguments of every tool call. `principal_id`,
  `order_id` and `claim_id` always come from the claim row, and
  `compute_compensation` always receives the order that `lookup_order` returned.
  Whatever the policy writes into `args` cannot point a tool at another order
  or feed it a made-up order.
* The loop's `final` is only a "ready" signal. After the loop, code derives the
  status from the tool results it collected and is the only caller of
  `durability.issue_refund`, with the full quote and `crash_at`.
* The ticket text never reaches the model prompt, the tool args or the trace.
  It is customer data, not instructions, and nothing in the decision needs it.
"""

from __future__ import annotations

from typing import Any

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.mock_llm import complete
from lab8.runtime import ToolRejected, run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "single"
MAX_STEPS = 8


def _policy(collected: dict[str, Any]) -> dict[str, Any]:
    """Pick the next action from what the agent has read so far.

    Order: lookup_order -> compute_compensation -> final. Stops early when the
    order is not visible. Returns {"tool": name, "args": {...}} or {"final": {...}}.
    """
    order_view = collected.get("lookup_order")
    if order_view is None:
        return {"tool": "lookup_order", "args": {}}
    if not order_view.get("visible"):
        return {"final": {"ready": False}}
    if "compute_compensation" not in collected:
        return {"tool": "compute_compensation", "args": {}}
    return {"final": {"ready": True}}


def _lock_args(
    name: str | None,
    args: dict[str, Any] | None,
    *,
    claim: dict[str, Any],
    collected: dict[str, Any],
) -> dict[str, Any]:
    """Pin every identifier to this claim. Model-written ids and orders are dropped."""
    locked = dict(args or {})
    if name in ("lookup_order", "lookup_refund_history"):
        locked["order_id"] = claim["order_id"]
        locked["principal_id"] = claim["principal_id"]
    elif name == "get_claim_view":
        locked["claim_id"] = claim["claim_id"]
    elif name == "compute_compensation":
        order_view = collected.get("lookup_order") or {}
        if not order_view.get("visible"):
            raise ToolRejected("compute_compensation needs a visible order from lookup_order first")
        locked = {"order": dict(order_view["order"])}
    return locked


def _observation_summary(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Trace-safe summary of a tool result. No order row, no ticket, no clause text."""
    if name == "lookup_order":
        return {"tool": name, "visible": bool(result.get("visible")), "reason": result.get("reason")}
    if name == "compute_compensation":
        return {
            "tool": name,
            "amount_vnd": int(result.get("amount_vnd") or 0),
            "reason": result.get("reason"),
            "clause_ids": list(result.get("clause_ids") or []),
        }
    if name == "lookup_refund_history":
        return {"tool": name, "visible": bool(result.get("visible")), "refunded_vnd": result.get("refunded_vnd")}
    return {"tool": name}


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
        # The model only drafts the reply, from the decision alone.
        complete(budget, seed=seed, purpose="draft", prompt=f"status={status};proposal={proposal}")
        trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
        return finish(
            conn, shape=SHAPE, claim_id=claim_id, seed=seed,
            status=status, proposal_amount=proposal, budget=budget,
            trace=trace, error_reason=reason,
        )

    try:
        budget.add_tool(tool_cost())
        claim = get_claim_view(conn, claim_id)
        trace.add("claim_lookup", {"found": claim["found"]})
        if not claim["found"]:
            return done("failed", reason="unknown_claim")
        if not claim.get("order_id"):
            return done("needs_info", reason="missing_order_id")

        collected: dict[str, Any] = {}
        step_no = 0

        def decide(seen: dict[str, Any]) -> dict[str, Any]:
            nonlocal step_no
            # One trace event per ReAct step: what the agent just observed, then what it picks.
            event: dict[str, Any] = {"step": step_no + 1}
            last_tool = seen.get("last_tool")
            if last_tool:
                collected[last_tool] = seen["last_result"]
                event["observed"] = _observation_summary(last_tool, seen["last_result"])
            step_no += 1
            # One model call per ReAct step. The prompt holds tool names only.
            complete(
                budget, seed=seed, purpose="react_step",
                prompt=f"step={step_no};seen={','.join(sorted(collected)) or 'none'}",
            )
            action = _policy(collected)
            if "final" in action:
                trace.add("react_step", {**event, "action": "final"})
                return action
            name = action.get("tool")
            trace.add("react_step", {**event, "action": "tool", "tool": name})
            return {
                "tool": name,
                "args": _lock_args(name, action.get("args"), claim=claim, collected=collected),
            }

        try:
            loop = run_react(
                conn, budget,
                observation={"claim": {"claim_id": claim_id}},
                decide=decide, max_steps=MAX_STEPS, worker=True,
            )
        except ToolRejected as exc:
            trace.add("tool_rejected", {"detail": str(exc)})
            return done("failed", reason="tool_rejected")
        if loop["stopped"] == "step_cap":
            return done("failed", reason="step_cap")

        # Code, not the model's final, decides what happens next.
        order_view = collected.get("lookup_order")
        if order_view is None:
            return done("failed", reason="incomplete_investigation")
        if not order_view.get("visible"):
            return done("escalated", reason=order_view.get("reason"))
        quote = collected.get("compute_compensation")
        if quote is None:
            return done("failed", reason="incomplete_investigation")
        proposal = int(quote["amount_vnd"])
        if proposal <= 0:
            return done("denied", reason=quote.get("reason"))

        # Full quote; the host checks approval and the per-order cap.
        # InjectedCrash is not caught: the crash suite needs it to reach the caller.
        budget.add_tool(tool_cost())
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        trace.add("host_result", {"ok": outcome["ok"], "reason": outcome["reason"]})
        if not outcome["ok"]:
            return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
        return done("resolved", proposal)
    finally:
        conn.close()
