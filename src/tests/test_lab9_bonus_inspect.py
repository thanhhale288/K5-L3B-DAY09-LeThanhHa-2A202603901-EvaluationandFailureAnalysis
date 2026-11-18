"""Bonus: Inspect AI scorers must agree with the pydantic-evals graders, cell by cell.
Skipped when inspect-ai is not installed (it lives in requirements-bonus.txt, not in the gate's requirements)."""
import pytest

pytestmark = pytest.mark.student
pytest.importorskip("inspect_ai")


@pytest.fixture(scope="module")
def port():
    from lab9 import inspect_port, variants
    variants.register()
    return inspect_port


def _spec():
    from pathlib import Path
    from lab9.eval import SPEC, load_spec
    return load_spec(Path(SPEC))


def test_clean_shape_agrees(port):
    from pathlib import Path
    r = port.compare("fixed", _spec(), Path("reports/eval_fixed.json"))
    assert r["cells"] == 280 and r["mismatches"] == 0


@pytest.mark.parametrize("shape,metric", [("stress_leaky_trace", "secret_leak"),
                                          ("stress_sibling_refund", "approval_bypass"),
                                          ("stress_misreport", "reply_matches_ledger")])
def test_buggy_shape_agrees_and_scorer_fires(port, shape, metric):
    from lab8.shapes import SHAPES
    from lab9.variant_agent import make_shape
    SHAPES.setdefault(shape, make_shape(shape, frozenset(port.STRESS[shape])))
    r = port.compare(shape, _spec(), None)
    assert r["mismatches"] == 0
    # flag metrics fire when mean > 0; ok metrics fire when mean < 1
    mean = r["inspect_means"][metric]
    assert (mean > 0.0) if port.METRICS[metric] == "flag" else (mean < 1.0), f"{metric} never fired on {shape}"
