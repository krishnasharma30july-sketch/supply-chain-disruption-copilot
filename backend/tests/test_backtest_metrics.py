from app.evals.backtest import _brier, _fit_platt, _platt, _prf, _reliability


def test_brier_bounds():
    assert _brier([1.0, 0.0], [True, False]) == 0.0
    assert _brier([0.5, 0.5], [True, False]) == 0.25
    assert _brier([], []) == 0.0


def test_reliability_bins_cover_edges():
    rows = _reliability([0.0, 0.1, 0.5, 1.0], [False, False, True, True])
    assert [r["bin"] for r in rows] == ["0.0-0.2", "0.4-0.6", "0.8-1.0"]
    assert rows[0]["n"] == 2 and rows[0]["observed"] == 0.0
    assert rows[-1]["predicted"] == 1.0 and rows[-1]["observed"] == 1.0


def test_prf():
    m = _prf([True, True, False], [True, False, True])
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["predicted_late"] == 2


def test_platt_shrinks_overconfident_probabilities():
    # Says 95% but only 30% happen; says 10% and 5% happen.
    probs = [0.95] * 100 + [0.10] * 100
    actual = [i < 30 for i in range(100)] + [i < 5 for i in range(100)]
    ab = _fit_platt(probs, actual)
    cal = _platt([0.95, 0.10], ab)
    assert abs(cal[0] - 0.30) < 0.02 and abs(cal[1] - 0.05) < 0.02
    assert _brier(_platt(probs, ab), actual) < _brier(probs, actual)
