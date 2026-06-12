"""Unit tests for matured-GIC tracking under the symmetric cash model (Model 2).

Covers the per-account GIC cash adjustment (active = −principal, matured =
+interest), its effect on the per-currency cash pools and account all-time gain,
and the matured-interest total surfaced on the GIC tab.

Runs under pytest *or* standalone: `python tests/test_gics.py` executes every
test_* function and prints a summary (handy until pytest is installed).
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from models import db, Account, GIC, Transaction
import calculations as calc

# Far-apart dates so "active" / "matured" hold regardless of the real today.
ACTIVE_START, ACTIVE_MAT = date(2099, 1, 1), date(2099, 12, 31)   # always future
MAT_START, MAT_MAT = date(2023, 1, 1), date(2024, 1, 1)          # always past, 365d


def _app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app


def _seed_gic(account='GICs', principal=5000.0, rate=5.0,
              start=MAT_START, maturity=MAT_MAT, compounding='Simple'):
    g = GIC(account=account, principal=principal, rate=rate,
            start_date=start, maturity_date=maturity, compounding=compounding)
    db.session.add(g)
    db.session.commit()
    return g


def _deposit(account='GICs', amount=5000.0, ccy='CAD'):
    t = Transaction(date=date(2023, 1, 1), ticker='CASH', account=account,
                    type='Deposit', qty=0, price=0, currency=ccy,
                    amount_native=amount, amount_cad=amount, net_cad=amount)
    db.session.add(t)
    db.session.commit()
    return t


def _run(fn):
    """Run a test body inside a fresh in-memory app/DB context."""
    app = _app()
    with app.app_context():
        db.create_all()
        try:
            fn()
        finally:
            db.session.remove()
            db.drop_all()


# ── cash adjustment ──────────────────────────────────────────────────────────
def test_active_gic_locks_principal_out_of_cash():
    def body():
        _seed_gic(start=ACTIVE_START, maturity=ACTIVE_MAT)
        adj = calc.get_gic_cash_adjustment_by_account()
        assert round(adj['GICs'], 2) == -5000.0   # principal locked away
    _run(body)


def test_matured_gic_returns_interest_to_cash():
    def body():
        _seed_gic()  # matured, simple 5% over 365d → $250 interest
        adj = calc.get_gic_cash_adjustment_by_account()
        assert round(adj['GICs'], 2) == 250.0     # only the interest layers on
    _run(body)


def test_cash_pool_zeroes_while_active():
    def body():
        _deposit()                                  # $5,000 cash funds the GIC
        _seed_gic(start=ACTIVE_START, maturity=ACTIVE_MAT)
        pools = calc.get_cash_by_account_currency()['GICs']
        assert round(pools.get('CAD', 0.0), 2) == 0.0
    _run(body)


def test_cash_pool_holds_principal_plus_interest_after_maturity():
    def body():
        _deposit()
        _seed_gic()                                 # matures → +$250 interest
        pools = calc.get_cash_by_account_currency()['GICs']
        assert round(pools['CAD'], 2) == 5250.0     # principal back + interest
    _run(body)


def test_account_all_time_gain_is_only_interest():
    def body():
        db.session.add(Account(name='GICs', type='Non-Reg'))
        db.session.commit()
        _deposit()
        _seed_gic()                                 # matured, $250 interest
        summary = {a['name']: a for a in calc.get_account_summary()}
        acct = summary['GICs']
        assert round(acct['net_contributions'], 2) == 5000.0   # not 10,000
        assert round(acct['all_time_gain'], 2) == 250.0
    _run(body)


# ── matured-interest total (GIC tab) ─────────────────────────────────────────
def test_matured_interest_total_sums_matured_only():
    def body():
        _seed_gic(principal=5000.0)                              # matured → $250
        _seed_gic(principal=10000.0, start=ACTIVE_START,
                  maturity=ACTIVE_MAT)                           # active → excluded
        stats = calc.get_gic_stats(show_matured=True)
        assert stats['matured_count'] == 1
        assert round(stats['matured_interest_total'], 2) == 250.0
    _run(body)


def test_matured_interest_total_zero_when_none_matured():
    def body():
        _seed_gic(start=ACTIVE_START, maturity=ACTIVE_MAT)
        stats = calc.get_gic_stats()
        assert stats['matured_count'] == 0
        assert stats['matured_interest_total'] == 0.0
    _run(body)


# ── tax tab: GIC interest income (Part B) ────────────────────────────────────
def test_gic_interest_income_taxed_when_non_registered():
    def body():
        db.session.add(Account(name='GICs', type='Non-Reg'))
        db.session.commit()
        _seed_gic(account='GICs')                   # matured 2024, $250 interest
        tax = calc.get_tax_summary(year=2024, inclusion_rate=0.5, marginal_rate=0.25)
        assert round(tax['gic_interest_total'], 2) == 250.0
        assert round(tax['gic_interest_tax'], 2) == 62.5   # interest fully taxable
        assert len(tax['gic_interest_rows']) == 1
    _run(body)


def test_gic_interest_income_sheltered_when_registered():
    def body():
        db.session.add(Account(name='RDSP', type='RDSP'))
        db.session.commit()
        _seed_gic(account='RDSP')                    # matured in a registered acct
        tax = calc.get_tax_summary(year=2024, inclusion_rate=0.5, marginal_rate=0.25)
        assert tax['gic_interest_total'] == 0.0
        assert tax['gic_interest_rows'] == []
    _run(body)


def test_gic_interest_income_excluded_from_other_years():
    def body():
        db.session.add(Account(name='GICs', type='Non-Reg'))
        db.session.commit()
        _seed_gic(account='GICs')                    # matured 2024
        tax = calc.get_tax_summary(year=2023, inclusion_rate=0.5, marginal_rate=0.25)
        assert tax['gic_interest_total'] == 0.0
    _run(body)


# ── standalone runner ────────────────────────────────────────────────────────
if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f'  PASS  {t.__name__}')
        except Exception as e:
            print(f'  FAIL  {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
    sys.exit(0 if passed == len(tests) else 1)
