import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backtest


def test_fit_logistic_recovers_the_line():
    """Data sampled from a known line: the fit should find it again."""
    rng = random.Random(7)
    a, b = 0.05, -0.1
    xs = [rng.uniform(-40, 40) for _ in range(2000)]
    ys = [1.0 if rng.random() < backtest.sigmoid(a * x + b) else 0.0 for x in xs]
    fa, fb = backtest.fit_logistic(xs, ys)
    assert abs(fa - a) < 0.02 and abs(fb - b) < 0.2


def test_fit_logistic_constant_x_finds_the_rate():
    """No slope to find (all x equal): the intercept is still the observed log odds."""
    a, b = backtest.fit_logistic([0.0] * 5, [0.0, 1.0, 0.0, 1.0, 1.0])
    assert abs(b - math.log(3 / 2)) < 1e-3  # 3 wins of 5


def test_predict_clamps():
    assert backtest.predict(100, 25) == 0.75
    assert backtest.predict(-100, 25) == 0.25
    assert backtest.predict(10, 25) == 0.6


def test_log_loss_penalizes_confident_wrong_answers():
    ys = [1.0]
    assert backtest.log_loss([0.99], ys) < backtest.log_loss([0.6], ys)
    assert backtest.log_loss([0.01], ys) > backtest.log_loss([0.4], ys)


def test_calibration_buckets_everything_once():
    ps, ys = [0.35, 0.44, 0.5, 0.58, 0.7], [1.0, 0.0, 1.0, 1.0, 0.0]
    rows = backtest.calibration(ps, ys)
    assert [n for _, n, _ in rows] == [1, 1, 1, 1, 1]
    assert rows[0][2] == 100.0 and rows[4][2] == 0.0
