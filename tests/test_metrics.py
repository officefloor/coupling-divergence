"""Hand-worked self-tests for the comparison-matrix math in coupling_divergence.metrics:
the threshold-free AUC/AP, the skew-adjusted-boxplot medcouple, the signal weighting, the
outlier algorithms, and the concentration measures. Every expected value is derived by hand.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coupling_divergence import metrics


def test_auc_perfect_separation():
    vals = {"a": 10, "b": 9, "c": 2, "d": 1}
    auc, ap, ng, n = metrics.auc_ap(vals, {"a", "b"}, set(vals))
    assert auc == 1.0 and ap == 1.0 and (ng, n) == (2, 4), (auc, ap, ng, n)


def test_auc_reversed_is_zero():
    vals = {"a": 1, "b": 2, "c": 9, "d": 10}
    auc, _, _, _ = metrics.auc_ap(vals, {"a", "b"}, set(vals))
    assert auc == 0.0, auc


def test_auc_all_ties_is_half():
    vals = {k: 5 for k in "abcd"}
    auc, _, _, _ = metrics.auc_ap(vals, {"a", "b"}, set(vals))
    assert abs(auc - 0.5) < 1e-9, auc


def test_auc_no_gods_is_none():
    vals = {"a": 1, "b": 2}
    auc, ap, _, _ = metrics.auc_ap(vals, set(), set(vals))
    assert auc is None and ap is None


def test_medcouple_symmetric_near_zero():
    assert abs(metrics._medcouple([1, 2, 3, 4, 5, 6, 7, 8, 9])) <= 0.01


def test_medcouple_right_skew_positive():
    assert metrics._medcouple(sorted([1, 1, 1, 1, 2, 2, 3, 5, 20, 100])) > 0


def test_signal_value_weighting():
    r = {"gain": 3, "cc": 4, "cognitive": 5}
    assert metrics.signal_value("divergence", r) == 3
    assert metrics.signal_value("cc", r) == 4
    assert metrics.signal_value("div_x_cc", r) == 12
    assert metrics.signal_value("div_x_cognitive", r) == 15


def test_concentration_even_vs_piled():
    even, conc = metrics.entropy_norm([5, 5, 5, 5]), metrics.entropy_norm([100, 1, 1, 1])
    assert even > 0.99 and conc < 0.5, (even, conc)
    assert metrics.gini([5, 5, 5, 5]) < 1e-9 and metrics.gini([0, 0, 0, 10]) > 0.5


def test_mad_z_flags_the_extreme():
    vmap = {f"f{i}": 1 for i in range(20)}
    vmap["big"] = 100
    assert "big" in metrics.mad_z(vmap)


def test_all_algorithms_return_sets():
    vmap = {f"f{i}": i + 1 for i in range(50)}
    for name, algo in metrics.ALGORITHMS.items():
        assert isinstance(algo(vmap), set), name


def test_signal_registries():
    assert "div_x_cc" in metrics.METHOD_SIGNALS
    assert "div_x_cognitive" in metrics.CLASS_SIGNALS
    assert "nom" in metrics.CLASS_SIGNALS and "nom" not in metrics.METHOD_SIGNALS


if __name__ == "__main__":
    ok = True
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as e:
            ok = False
            print(f"  [FAIL] {fn.__name__}: {e}")
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
