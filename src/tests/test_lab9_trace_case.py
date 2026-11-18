"""Regression test for docs/trace_case.md: C-002@11, FM-03 (ticket_trust).

First wrong step on seeded_regression is event 3 (`model_override delay_days=5`, DB says 2).
The test must FAIL on the buggy variant and PASS on `fixed`."""
import pytest

pytestmark = pytest.mark.student


def _trial(shape):
    from lab9.trace_case import investigate
    return investigate(shape, "C-002", 11)


def _ctx(case):
    from pydantic_evals.evaluators import EvaluatorContext
    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"], output=case["trial"],
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def test_fm03_seeded_regression_is_caught():
    from lab9 import graders
    case = _trial("seeded_regression")
    events = case["trial"]["events"]
    assert events[2]["kind"] == "model_override" and events[2]["payload"] == {"delay_days": 5}   # first wrong step
    assert graders.DbFactKept().evaluate(_ctx(case)).value is False
    assert graders.RefundAmountCorrect().evaluate(_ctx(case)).value is False
    assert graders.ProposalCorrect().evaluate(_ctx(case)).value is False


def test_fm03_fixed_passes():
    from lab9 import graders
    case = _trial("fixed")
    assert all(e["kind"] != "model_override" for e in case["trial"]["events"])
    for g in (graders.DbFactKept, graders.RefundAmountCorrect, graders.ProposalCorrect, graders.ReplyMatchesLedger):
        assert g().evaluate(_ctx(case)).value is True
