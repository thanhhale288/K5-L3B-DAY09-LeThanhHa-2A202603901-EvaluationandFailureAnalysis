"""Variants of shape single with one fault each. Same code path as `lab8.shapes.single`.

* `single_trusts_ticket` (FM-3.3 Incorrect verification): on seed % 5 == 0, when the
  delay written in the ticket disagrees with the order row, the order handed to
  `compute_compensation` takes the ticket's number. This is the swarm's
  REFERENCE_WEAKNESS moved into one agent; it is also the research-card ablation.
* `single_low_cap` (FM-1.5 Unaware of termination conditions): the step budget is
  set to 2, below the 3 steps the plan needs, so the loop stops before it is done.

Each wrapper swaps module globals of `lab8.shapes.single` (`SHAPE`, `_lock_args`,
`MAX_STEPS`) for the duration of one call and restores them in `finally`. The
graded file carries no switch for these faults.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from lab8.db import connect
from lab8.mock_llm import parse_delay_days
from lab8.schemas import RunResult
from lab8.shapes import single
from lab8.tools import get_claim_view


@contextmanager
def _patched(**values: Any) -> Iterator[None]:
    saved = {name: getattr(single, name) for name in values}
    for name, value in values.items():
        setattr(single, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(single, name, value)


def _ticket_delay(store_path: str, claim_id: str) -> int | None:
    conn = connect(store_path)
    try:
        claim = get_claim_view(conn, claim_id)
    finally:
        conn.close()
    return parse_delay_days(claim["ticket"]) if claim.get("found") else None


def single_trusts_ticket(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    ticket_delay = _ticket_delay(store_path, claim_id)
    real_lock = single._lock_args

    def trusting_lock(name, args, *, claim, collected) -> dict[str, Any]:
        locked = real_lock(name, args, claim=claim, collected=collected)
        if name == "compute_compensation" and ticket_delay is not None and seed % 5 == 0:
            if ticket_delay != int(locked["order"]["delay_days"]):
                locked["order"]["delay_days"] = ticket_delay  # the injected fault
        return locked

    with _patched(SHAPE="single_trusts_ticket", _lock_args=trusting_lock):
        return single.run_claim(claim_id, seed=seed, store_path=store_path, crash_at=crash_at)


def single_low_cap(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    with _patched(SHAPE="single_low_cap", MAX_STEPS=2):
        return single.run_claim(claim_id, seed=seed, store_path=store_path, crash_at=crash_at)
