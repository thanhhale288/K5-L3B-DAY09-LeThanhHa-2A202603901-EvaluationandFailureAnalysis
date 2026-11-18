"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows, base_rows = pr.get("rows") or [], base.get("rows") or []
    # 1. non-finite numbers: `nan < min` is False, so a naive gate lets them through
    for k, v in (pr.get("metrics") or {}).items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v} (not finite)")
    # 2. crashed graders
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s), first: {pr['evaluator_failures'][0]}")
    graded = [m["id"] for m in spec["metrics"] if any(m["id"] in r for r in rows + base_rows)]
    bad_none = sorted({m for r in rows for m in graded if r.get(m) is None})
    if bad_none:
        n = sum(1 for r in rows if any(r.get(m) is None for m in bad_none))
        problems.append(f"{n} row(s) with a missing/None grade for {', '.join(bad_none)} (crashed grader dropped out of the mean)")
    # 3. row count, duplicates, and the exact (item, seed) grid
    man = pr.get("manifest", {})
    want_rows = man.get("items", 0) * len(man.get("seeds", []))
    if len(rows) != want_rows:
        problems.append(f"{len(rows)} rows but manifest says {man.get('items')} items x {len(man.get('seeds', []))} seeds = {want_rows}")
    keys = Counter((r.get("item_id"), r.get("seed")) for r in rows)
    dup = sorted(k for k, c in keys.items() if c > 1)
    if dup:
        problems.append(f"duplicated (item, seed) rows: {dup[:3]}")
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base_rows}
    missing, extra = sorted(base_keys - set(keys)), sorted(set(keys) - base_keys)
    if missing or extra:
        problems.append(f"(item, seed) set differs from the baseline: {len(missing)} missing {missing[:3]}, {len(extra)} extra {extra[:3]}")
    if sorted(man.get("seeds", [])) != sorted(spec.get("seeds", man.get("seeds", []))):
        problems.append(f"manifest seeds {man.get('seeds')} differ from the spec's {spec.get('seeds')}")
    # 4. reported metrics must be what the rows say
    ids = [m["id"] for m in spec["metrics"] if rows and m["id"] in rows[0] and all(r.get(m["id"]) is not None for r in rows)]
    if rows and ids and not bad_none:
        has_cost = all("cost" in r for r in rows)
        redo = aggregate([r if "cost" in r else {**r, "cost": 0.0} for r in rows], ids, k_values=spec.get("pass_k", []))
        for k, v in redo.items():
            if k == "cost_per_success" and not has_cost:
                continue
            got = (pr.get("metrics") or {}).get(k)
            if isinstance(v, (int, float)) and isinstance(got, (int, float)) and math.isfinite(got) and abs(v - got) > 1e-3:
                problems.append(f"metric {k} reported {got} but the rows give {v} (hand-edited metrics)")
    # 5. an item relabelled out of a slice hides its regression from the slice gate
    tags = lambda rs: {r.get("item_id"): tuple(sorted(r.get("slices") or [])) for r in rs}
    pt, bt = tags(rows), tags(base_rows)
    moved = sorted(i for i in pt if i in bt and pt[i] != bt[i])
    if moved:
        problems.append(f"slice tags differ from the baseline for {moved[:3]}")
    # 6. ratchet: same eval-set version => thresholds may only get stricter
    if base_spec is not None and spec.get("eval_set_version") == base_spec.get("eval_set_version"):
        problems += _ratchet(spec, base_spec)
    return problems


def _tighter(new: dict, old: dict, what: str) -> list[str]:
    out = []
    for key, sign in (("max", 1), ("min", -1), ("max_increase", 1)):
        if key in old:
            if key not in new:
                out.append(f"{what}: threshold {key} removed")
            elif (new[key] - old[key]) * sign > 1e-12:
                out.append(f"{what}: {key} loosened {old[key]} -> {new[key]}")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        out.append(f"{what}: no_significant_drop dropped")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    out = []
    cur = {m["id"]: m for m in spec["metrics"]}
    for old in base_spec["metrics"]:
        new = cur.get(old["id"])
        if new is None:
            out.append(f"ratchet: metric {old['id']} disappeared")
            continue
        out += [f"ratchet: {p}" for p in _tighter(new.get("threshold", {}), old.get("threshold", {}), old["id"])]
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"ratchet: {old['id']} is no longer blocking")
    cur_s = {x["name"]: x for x in spec.get("slices") or []}
    for old in base_spec.get("slices") or []:
        new = cur_s.get(old["name"])
        if new is None:
            out.append(f"ratchet: slice {old['name']} disappeared")
            continue
        out += [f"ratchet: slice {p}" for p in _tighter(new, old, old["name"])]
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"ratchet: slice {old['name']} is no longer blocking")
        if new.get("metric") != old.get("metric"):
            out.append(f"ratchet: slice {old['name']} metric changed {old.get('metric')} -> {new.get('metric')}")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        a, b = per_item(pr, metric, name), per_item(base, metric, name)
        verdict, note = "ok", ""
        if not a:
            verdict, note = "fail", "no rows in this slice"
        else:
            val = sum(a.values()) / len(a)
            note = f"n={len(a)} mean {val:.3f}"
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note += f" Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        sid = f"slice:{name}/{metric}"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = "ok" if verdict == "ok" else ("BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<34} {json.dumps({k: sl[k] for k in ('min', 'no_significant_drop') if k in sl}):<40} {note:<44} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
