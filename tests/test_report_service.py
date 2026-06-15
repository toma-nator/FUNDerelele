"""Unit tests for the report engine's pure math (report_service.py).

Runs under pytest *or* standalone: `python tests/test_report_service.py` executes
every test_* function and prints a summary. Only the network-free, DB-free helpers
are covered here (the orchestrator `build_report_data` needs the app/DB/yfinance).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_service import (
    _returns, _corr, _max_drawdown, _ret_1y, _spark, _corr_tag,
    _alloc_weights, _portfolio_risk, _portfolio_returns,
)


# ── series math ──────────────────────────────────────────────────────────────────
def test_returns():
    r = _returns([100, 110, 99])
    assert len(r) == 2 and abs(r[0] - 0.1) < 1e-9 and abs(r[1] - (-0.1)) < 1e-9
    assert _returns([]) == []
    assert _returns([100]) == []


def test_ret_1y_and_drawdown():
    assert abs(_ret_1y([100, 120]) - 0.20) < 1e-9
    assert _ret_1y([100]) is None
    # peak 120 -> trough 90 = -25%
    assert abs(_max_drawdown([100, 120, 90, 110]) - (-0.25)) < 1e-9
    assert _max_drawdown([100]) is None


def test_corr():
    a = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02]
    assert abs(_corr(a, a) - 1.0) < 1e-9            # perfectly correlated with itself
    assert abs(_corr(a, [-x for x in a]) + 1.0) < 1e-9   # perfectly anti-correlated
    assert _corr(a, a[:4]) is None                  # too short after alignment
    assert _corr([1.0] * 8, a) is None              # zero variance -> undefined


def test_corr_tag():
    assert _corr_tag(0.05) == 'strong diversifier'
    assert _corr_tag(0.5) == 'moderate'
    assert _corr_tag(0.9) == 'higher overlap'
    assert _corr_tag(None) is None


def test_spark_downsamples():
    assert _spark([1, 2, 3]) == [1, 2, 3]            # shorter than n -> unchanged
    s = _spark(list(range(100)), n=10)
    assert len(s) == 10
    assert s[0] == 0 and s[-1] == 99                 # endpoints preserved


# ── allocation aggregation (stubbed metadata, no DB) ──────────────────────────────
def _pos(value, ticker, sector=None, asset_type='Equity'):
    return {'value': value, 'ticker': ticker, 'm': {'sector': sector, 'asset_type': asset_type}}


def test_alloc_weights_sector_with_cash():
    positions = {
        'AAA': _pos(60, 'AAA', sector='Technology'),
        'BBB': _pos(20, 'BBB', sector='Financials'),
    }
    rows, tot = _alloc_weights(positions, 20, 'sector')   # +20 cash -> 100 total
    assert tot == 100
    assert abs(rows['Technology'] - 60) < 1e-6
    assert abs(rows['Financials'] - 20) < 1e-6
    assert abs(rows['Cash'] - 20) < 1e-6


def test_alloc_weights_empty():
    rows, tot = _alloc_weights({}, 0, 'sector')
    assert rows == {} and tot == 0.0


# ── portfolio risk (stubbed metadata) ─────────────────────────────────────────────
def test_portfolio_risk_value_weighted():
    # expense_ratio is already a percentage figure (yfinance convention), blended
    # only over holdings that report a fee.
    positions = {
        'AAA': {'value': 75, 'ticker': 'AAA', 'm': {'beta': 1.2, 'volatility': 0.20, 'expense_ratio': 0.20}},
        'BBB': {'value': 25, 'ticker': 'BBB', 'm': {'beta': 0.4, 'volatility': 0.08, 'expense_ratio': None}},
    }
    r = _portfolio_risk(positions, 0)
    assert abs(r['beta'] - (0.75 * 1.2 + 0.25 * 0.4)) < 1e-6          # 1.0
    assert abs(r['volatility'] - (0.75 * 0.20 + 0.25 * 0.08) * 100) < 1e-6  # 17.0%
    assert abs(r['mer'] - 0.20) < 1e-6                                 # only AAA carries a fee → 0.20%
    assert abs(r['top_weight'] - 75.0) < 1e-6


def test_portfolio_risk_cash_dilutes_and_tops():
    positions = {'AAA': {'value': 50, 'ticker': 'AAA', 'm': {'beta': 1.0, 'volatility': 0.1}}}
    r = _portfolio_risk(positions, 50)            # 50% cash
    assert abs(r['beta'] - 0.5) < 1e-6            # cash contributes 0 beta
    assert abs(r['top_weight'] - 50.0) < 1e-6     # cash weight counts toward "top"


def test_portfolio_returns_blended():
    rets = {'AAA': [0.10] * 8, 'BBB': [0.00] * 8}
    positions = {'AAA': {'value': 50, 'ticker': 'AAA', 'm': {}},
                 'BBB': {'value': 50, 'ticker': 'BBB', 'm': {}}}
    pr = _portfolio_returns(positions, rets)
    assert len(pr) == 8
    assert all(abs(x - 0.05) < 1e-9 for x in pr)  # 50/50 of 10% and 0%


# ── standalone runner (no pytest needed) ─────────────────────────────────────────
if __name__ == '__main__':
    tests = sorted((n, f) for n, f in globals().items() if n.startswith('test_') and callable(f))
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS  {name}')
            passed += 1
        except Exception as e:
            print(f'  FAIL  {name}: {type(e).__name__}: {e}')
            failed += 1
    print(f'\n{passed} passed, {failed} failed, {len(tests)} total')
    sys.exit(1 if failed else 0)
