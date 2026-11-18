"""Tag every failed benchmark trial with a MAST group and failure mode (bonus B2).

MAST (Cemri et al., arXiv 2503.13657, NeurIPS 2025 D&B) groups multi-agent failures
into three categories: FC1 specification / system design, FC2 inter-agent
misalignment, FC3 task verification. The rules below map the evidence a LogiX trial
leaves behind (benchmark row, trace events, public claim and world data) to the
modes that can occur in this lab. Rules run in order; the first match wins and its
reason is stored with the tag.

Run from src/ after the bonus benchmark (traces/ must hold that run's traces):

    python -m bonus.mast_tag --benchmark reports/bonus_benchmark.json --labels bonus/hand_labels.json --safety reports/bonus_safety.json --out reports/bonus_mast.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lab8.config_loader import repo_path
from lab8.db import load_json, load_jsonl
from lab8.mock_llm import parse_delay_days

GROUPS = {
    "FC1": "Specification / system design",
    "FC2": "Inter-agent misalignment",
    "FC3": "Task verification",
    "infra": "Not a MAST failure (crash, invalid result or broken trace)",
    "unknown": "No rule matched",
}
MODES = {
    "FM-1.1": ("FC1", "Disobey task specification"),
    "FM-1.5": ("FC1", "Unaware of termination conditions"),
    "FM-2.4": ("FC2", "Information withholding"),
    "FM-3.2": ("FC3", "No or incomplete verification"),
    "FM-3.3": ("FC3", "Incorrect verification"),
}
INFRA_REASONS = (
    "exception", "invalid_result", "wrong_claim_id",
    "missing_trace", "stale_trace", "invalid_trace", "trace_run_id_mismatch",
)


# --- evidence ---------------------------------------------------------------------


def trace_evidence(body: dict[str, Any] | None) -> dict[str, Any]:
    """Pull the facts the rules need out of one trace (any of the shapes' formats)."""
    ev: dict[str, Any] = {"quote": None, "host": None, "decision": None, "order_fields": None, "model_override": False}
    for event in (body or {}).get("events") or []:
        kind, payload = event.get("kind"), event.get("payload") or {}
        if kind == "quote":
            ev["quote"] = payload
        elif kind == "react_step" and (payload.get("observed") or {}).get("tool") == "compute_compensation":
            ev["quote"] = payload["observed"]
        elif kind in ("host_result", "host"):
            ev["host"] = payload
        elif kind == "decision":
            ev["decision"] = payload
        elif kind == "investigator_return" and "order_fields" in payload:
            ev["order_fields"] = list(payload["order_fields"])
        elif kind == "model_override":
            ev["model_override"] = True
    return ev


def current_clause_ids(world: dict[str, Any]) -> set[str]:
    as_of = str(world["as_of"])
    return {
        clause["clause_id"]
        for policy in world["policies"]
        if policy.get("is_current") and str(policy.get("effective_date", "")) <= as_of
        for clause in policy.get("clauses", [])
    }


# --- rules ------------------------------------------------------------------------


def _tag(mode: str | None, group: str, rule: str, why: str) -> dict[str, Any]:
    return {"group": group, "mode": mode, "mode_name": MODES[mode][1] if mode else None, "rule": rule, "why": why}


def tag_trial(
    row: dict[str, Any],
    ev: dict[str, Any],
    claim: dict[str, Any] | None,
    order: dict[str, Any] | None,
    current_clauses: set[str],
) -> dict[str, Any]:
    reason = str(row.get("reason") or "")
    if reason.split(":", 1)[0] in INFRA_REASONS:
        return _tag(None, "infra", "R0", f"trial failed before grading content: {reason}")

    host = ev.get("host") or {}
    if host.get("ok") is False and row.get("status") == "resolved":
        return _tag("FM-3.2", "FC3", "R1", f"host refused ({host.get('reason')}) but the shape reported resolved")

    decision = ev.get("decision") or {}
    if decision.get("reason") == "step_cap":
        return _tag("FM-1.5", "FC1", "R2", "loop stopped at its step budget before the plan was finished")

    quote = ev.get("quote") or {}
    stale = sorted(set(quote.get("clause_ids") or []) - current_clauses)
    if stale:
        return _tag("FM-1.1", "FC1", "R3", f"quote used clauses not in force on as_of: {', '.join(stale)}")

    fields = ev.get("order_fields")
    if fields is not None and "delay_days" not in fields:
        return _tag("FM-2.4", "FC2", "R4", "order view returned by the investigator lacks delay_days")

    expected = (claim or {}).get("expected") or {}
    ticket_delay = parse_delay_days((claim or {}).get("ticket") or "")
    order_delay = (order or {}).get("delay_days")
    paid_more = int(quote.get("amount_vnd") or 0) > int(expected.get("proposal_amount") or 0)
    if ev.get("model_override") or (
        ticket_delay is not None and order_delay is not None and ticket_delay != int(order_delay) and paid_more
    ):
        return _tag(
            "FM-3.3", "FC3", "R5",
            f"ticket says {ticket_delay} days, order row says {order_delay}; the quote followed the ticket",
        )
    return _tag(None, "unknown", "R6", f"no rule matched (reason: {reason})")


# --- agreement --------------------------------------------------------------------


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    if not a or len(a) != len(b):
        return None
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected >= 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def agreement(tags: dict[str, dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [(lab, tags.get(lab["run_id"])) for lab in labels]
    missing = [lab["run_id"] for lab, tag in rows if tag is None]
    pairs = [(lab, tag) for lab, tag in rows if tag is not None]
    out: dict[str, Any] = {"labeled": len(labels), "compared": len(pairs), "missing_from_tags": missing}
    for key in ("group", "mode"):
        human = [str(lab.get(key)) for lab, _ in pairs]
        tagger = [str(tag.get(key)) for _, tag in pairs]
        same = sum(1 for h, t in zip(human, tagger) if h == t)
        out[key] = {
            "agreement": (same / len(pairs)) if pairs else None,
            "cohen_kappa": cohen_kappa(human, tagger),
            "disagreements": [
                {"run_id": lab["run_id"], "human": lab.get(key), "tagger": tag.get(key)}
                for lab, tag in pairs if str(lab.get(key)) != str(tag.get(key))
            ],
        }
    return out


# --- main -------------------------------------------------------------------------


def run(
    benchmark: dict[str, Any],
    *,
    traces_dir: Path,
    claims: list[dict[str, Any]],
    world: dict[str, Any],
    labels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_claim = {c["claim_id"]: c for c in claims}
    by_order = {o["order_id"]: o for o in world["orders"]}
    clauses = current_clause_ids(world)
    tags: dict[str, dict[str, Any]] = {}
    for row in benchmark["trials"]:
        if row.get("passed") is not False:
            continue
        path = traces_dir / f"{row['run_id']}.json"
        body = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        claim = by_claim.get(row["claim_id"])
        order = by_order.get((claim or {}).get("order_id"))
        tag = tag_trial(row, trace_evidence(body), claim, order, clauses)
        tags[row["run_id"]] = {"shape": row["shape"], "claim_id": row["claim_id"], "seed": row["seed"],
                               "benchmark_reason": row.get("reason"), "trace_found": body is not None, **tag}

    table: dict[str, Counter] = defaultdict(Counter)
    modes: dict[str, Counter] = defaultdict(Counter)
    for tag in tags.values():
        table[tag["shape"]][tag["group"]] += 1
        if tag["mode"]:
            modes[tag["shape"]][tag["mode"]] += 1

    metrics = {
        shape: {k: stats.get(k) for k in ("trials", "successes", "pass_rate", "pass_5", "seed_variance", "cost_per_success_vnd")}
        for shape, stats in benchmark["by_shape"].items()
    }
    return {
        "taxonomy": "MAST (arXiv 2503.13657): FC1 specification/system design, FC2 inter-agent misalignment, FC3 task verification",
        "groups": GROUPS,
        "modes": {k: {"group": g, "name": n} for k, (g, n) in MODES.items()},
        "controlled_variables": benchmark.get("controlled_variables"),
        "metrics_by_shape": metrics,
        "failed_trials": len(tags),
        "counts_by_shape_and_group": {s: dict(c) for s, c in sorted(table.items())},
        "counts_by_shape_and_mode": {s: dict(c) for s, c in sorted(modes.items())},
        "agreement_with_hand_labels": agreement(tags, labels) if labels else None,
        "tags": tags,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tag failed trials with MAST groups")
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--traces", default="traces")
    parser.add_argument("--cases", default=str(repo_path("data", "public_claims.jsonl")))
    parser.add_argument("--world", default=str(repo_path("data", "public_world_state.json")))
    parser.add_argument("--labels", default=None)
    parser.add_argument("--safety", default=None, help="optional bonus.safety output to embed")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    benchmark = json.loads(Path(args.benchmark).read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))["labels"] if args.labels else None
    report = run(benchmark, traces_dir=Path(args.traces), claims=load_jsonl(args.cases),
                 world=load_json(args.world), labels=labels)
    if args.safety:
        report["safety_counts"] = json.loads(Path(args.safety).read_text(encoding="utf-8"))["by_shape"]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'shape':<22} {'fail':>5}  " + "  ".join(f"{g:>7}" for g in ("FC1", "FC2", "FC3", "infra", "unknown")))
    for shape, stats in report["metrics_by_shape"].items():
        counts = report["counts_by_shape_and_group"].get(shape, {})
        fails = (stats["trials"] or 0) - (stats["successes"] or 0)
        print(f"{shape:<22} {fails:>5}  " + "  ".join(f"{counts.get(g, 0):>7}" for g in ("FC1", "FC2", "FC3", "infra", "unknown")))
    agree = report["agreement_with_hand_labels"]
    if agree:
        for key in ("group", "mode"):
            a = agree[key]
            print(f"hand labels ({agree['compared']}) vs tagger, {key}: agreement {a['agreement']:.3f}, kappa {a['cohen_kappa']:.3f}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
