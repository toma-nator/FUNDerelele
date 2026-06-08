"""
Generate a self-contained sample portfolio database (sample_portfolio.db) that a
new user can load via Settings -> Restore Database to see every feature populated.

Covers: 5 accounts (2 TFSA, 1 FHSA, 2 Non-Reg), ~5 years of history, all
transaction types (Buy, Sell, Dividend, WithholdingTax, Split, Interest,
ReturnOfCapital, Fee, Deposit/Contribution), GICs (laddered maturities), and a
watchlist. Uses real yfinance tickers so live prices/dividends/charts populate.

Run:  python generate_sample_data.py
"""
import os
from datetime import date, datetime, timedelta

from flask import Flask
from models import db, Transaction, Account, GIC, WatchlistItem, Setting

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'sample_portfolio.db')

TODAY = date(2026, 6, 7)
CAD, USD = 'CAD', 'USD'
BATCH = 'sample-data'

# Approximate USD/CAD by year for historical CAD conversion of USD trades.
FX_BY_YEAR = {2021: 1.26, 2022: 1.30, 2023: 1.355, 2024: 1.36, 2025: 1.38, 2026: 1.37}


def fx(d):
    return FX_BY_YEAR.get(d.year, 1.35) if d else 1.35


# ── Accounts ────────────────────────────────────────────────────────────────
ACCOUNTS = [
    ('TFSA',            'TFSA'),
    ('TFSA Growth',     'TFSA'),
    ('FHSA',            'FHSA'),
    ('Non-Registered',  'Non-Reg'),
    ('Margin',          'Non-Reg'),
]

# ── Buys: (date, ticker, account, qty, price, currency) ─────────────────────
BUYS = [
    # TFSA — Canadian core + dividend
    (date(2021, 7,  5), 'VFV.TO', 'TFSA', 50,  95.00, CAD),
    (date(2022, 3, 10), 'RY.TO',  'TFSA', 40, 132.00, CAD),
    (date(2022, 9, 15), 'ENB.TO', 'TFSA', 100, 52.00, CAD),
    (date(2023, 6, 20), 'VFV.TO', 'TFSA', 30, 105.00, CAD),
    (date(2024, 2, 12), 'RY.TO',  'TFSA', 20, 130.00, CAD),
    # TFSA Growth — growth names + a split
    (date(2022, 1, 20), 'AAPL',   'TFSA Growth', 30, 165.00, USD),
    (date(2022, 6, 10), 'SHOP.TO','TFSA Growth', 25,  45.00, CAD),
    (date(2023, 5, 15), 'NVDA',   'TFSA Growth', 10, 300.00, USD),
    (date(2025, 3,  3), 'AAPL',   'TFSA Growth', 10, 235.00, USD),
    # FHSA — conservative + money-market
    (date(2023, 4, 10), 'XIU.TO', 'FHSA', 100, 30.00, CAD),
    (date(2023, 4, 10), 'ZMMK.TO','FHSA', 200, 50.00, CAD),
    (date(2024, 8,  1), 'XIU.TO', 'FHSA',  50, 33.00, CAD),
    # Non-Registered — dividend income (taxable)
    (date(2021, 8,  1), 'T.TO',   'Non-Registered', 200, 27.00, CAD),
    (date(2021, 8,  1), 'ENB.TO', 'Non-Registered', 150, 47.00, CAD),
    (date(2022, 11, 5), 'KO',     'Non-Registered',  80, 60.00, USD),
    (date(2023, 10,10), 'T.TO',   'Non-Registered', 100, 24.00, CAD),
    # Margin — US growth, incl. a position that gets fully closed
    (date(2021, 9,  1), 'MSFT',   'Margin', 30, 300.00, USD),
    (date(2021, 9,  1), 'VOO',    'Margin', 20, 400.00, USD),
    (date(2022, 2,  1), 'AAPL',   'Margin', 40, 170.00, USD),
    (date(2023, 7,  1), 'MSFT',   'Margin', 10, 340.00, USD),
]

# ── Sells: (date, ticker, account, qty, price, currency) ────────────────────
SELLS = [
    (date(2025, 6, 15), 'ENB.TO', 'Non-Registered', 100, 58.00, CAD),  # taxable gain
    (date(2024, 11,15), 'AAPL',   'Margin',          40, 225.00, USD),  # closes position
]

# ── Splits: (date, ticker, account, new_shares_added) ───────────────────────
# NVDA 10-for-1 (2024-06): 10 shares -> 100, i.e. +90 new shares.
SPLITS = [
    (date(2024, 6, 10), 'NVDA', 'TFSA Growth', 90),
]

# ── Deposits / contributions: (date, account, amount, subtype) ──────────────
DEPOSITS = [
    (date(2021, 7,  1), 'TFSA', 6000, 'Contribution'),
    (date(2022, 1,  5), 'TFSA', 6000, 'Contribution'),
    (date(2023, 1,  4), 'TFSA', 6500, 'Contribution'),
    (date(2024, 1,  8), 'TFSA', 7000, 'Contribution'),
    (date(2022, 1, 15), 'TFSA Growth', 6000, 'Contribution'),
    (date(2023, 1, 10), 'TFSA Growth', 6500, 'Contribution'),
    (date(2025, 2, 20), 'TFSA Growth', 3000, 'Contribution'),
    (date(2023, 4,  1), 'FHSA', 8000, 'Contribution'),
    (date(2024, 1, 15), 'FHSA', 8000, 'Contribution'),
    (date(2025, 1, 20), 'FHSA', 8000, 'Contribution'),
    (date(2021, 7, 25), 'Non-Registered', 20000, 'Contribution'),
    (date(2023, 9, 30), 'Non-Registered', 5000,  'Contribution'),
    (date(2021, 8, 25), 'Margin', 25000, 'Contribution'),
    (date(2023, 6, 25), 'Margin', 8000,  'Contribution'),
]

# ── Dividend schedule: ticker -> (per_share_native, currency, freq) ─────────
# freq: 'Q' quarterly (Mar/Jun/Sep/Dec 15), 'M' monthly (28th)
DIV_PER_SHARE = {
    'VFV.TO':  (0.32, CAD, 'Q'),
    'RY.TO':   (1.42, CAD, 'Q'),
    'ENB.TO':  (0.91, CAD, 'Q'),
    'T.TO':    (0.38, CAD, 'Q'),
    'XIU.TO':  (0.21, CAD, 'Q'),
    'ZMMK.TO': (0.21, CAD, 'M'),   # money-market distributions
    'KO':      (0.48, USD, 'Q'),
    'AAPL':    (0.25, USD, 'Q'),
    'MSFT':    (0.83, USD, 'Q'),
    'VOO':     (1.65, USD, 'Q'),
}
US_WHT = 0.15  # US dividend withholding on TFSA/Non-Reg/Margin (not treaty-exempt)

# ── Standalone cash events ──────────────────────────────────────────────────
# Interest on idle cash: (date, account, amount_cad)
INTEREST = [
    (date(2022, 12, 30), 'Non-Registered', 41.20),
    (date(2023, 12, 29), 'Non-Registered', 63.75),
    (date(2024, 12, 31), 'Non-Registered', 88.40),
    (date(2025, 12, 31), 'Non-Registered', 72.10),
    (date(2024, 6, 28),  'Margin', 120.55),
    (date(2025, 6, 30),  'Margin', 96.30),
]
# Return of capital (e.g. from an income ETF): (date, ticker, account, amount_cad)
RETURN_OF_CAPITAL = [
    (date(2023, 12, 20), 'ENB.TO', 'Non-Registered', 55.00),
    (date(2024, 12, 18), 'XIU.TO', 'FHSA', 22.50),
]
# Account / admin fees: (date, account, amount_cad)
FEES = [
    (date(2022, 1, 31), 'Margin', 24.95),
    (date(2024, 7, 31), 'Non-Registered', 9.95),
]

# ── GICs: (name, institution, account, principal, rate%, start, maturity, comp)
GICS = [
    ('2-Year GIC',   'EQ Bank',   'FHSA',           10000, 4.80, date(2024, 6, 1),  date(2026, 6, 1),  'Annual'),
    ('3-Year GIC',   'Oaken',     'Non-Registered', 15000, 5.05, date(2024, 1, 15), date(2027, 1, 15), 'Annual'),
    ('1-Year GIC',   'Tangerine', 'TFSA',            5000, 5.25, date(2025, 9, 1),  date(2026, 9, 1),  'Annual'),
]

# ── Watchlist: (ticker, company, sector, currency, target_price, target_type)
WATCHLIST = [
    ('COST',   'Costco Wholesale',   'Consumer Defensive', USD, 800.00, 'below'),
    ('BN.TO',  'Brookfield Corp',    'Financial Services', CAD,  60.00, 'below'),
    ('VDY.TO', 'Vanguard FTSE CDN High Div', 'ETF',        CAD,  45.00, 'below'),
    ('NVDA',   'NVIDIA',             'Technology',         USD, 200.00, 'above'),
]


def _add(**kw):
    kw.setdefault('currency', CAD)
    kw.setdefault('qty', 0.0)
    kw.setdefault('price', 0.0)
    kw.setdefault('amount_native', 0.0)
    kw.setdefault('amount_cad', 0.0)
    kw.setdefault('fees_cad', 0.0)
    kw.setdefault('net_cad', 0.0)
    kw.setdefault('subtype', '')
    kw.setdefault('notes', '')
    kw['import_batch'] = BATCH
    db.session.add(Transaction(**kw))


def shares_at(ticker, account, when):
    """Shares held in an account as of `when`, from buys/sells/splits."""
    q = 0.0
    for d, t, a, qty, *_ in BUYS:
        if t == ticker and a == account and d <= when:
            q += qty
    for d, t, a, qty, *_ in SELLS:
        if t == ticker and a == account and d <= when:
            q -= qty
    for d, t, a, new in SPLITS:
        if t == ticker and a == account and d <= when:
            q += new
    return q


def pay_dates(start, freq):
    out = []
    if freq == 'Q':
        for yr in range(start.year, TODAY.year + 1):
            for mo in (3, 6, 9, 12):
                dd = date(yr, mo, 15)
                if start <= dd <= TODAY:
                    out.append(dd)
    else:  # monthly
        yr, mo = start.year, start.month
        while date(yr, mo, 28) <= TODAY:
            dd = date(yr, mo, 28)
            if dd >= start:
                out.append(dd)
            mo += 1
            if mo > 12:
                mo, yr = 1, yr + 1
    return out


def build_sample_data():
    """Populate the sample portfolio into the active db.session (current app
    context). Assumes the tables are empty — callers should wipe first."""
    # Accounts
    for name, typ in ACCOUNTS:
        db.session.add(Account(name=name, type=typ, cash_balance=0))

    # Buys
    for d, t, a, qty, price, cur in BUYS:
        rate = fx(d) if cur == USD else 1.0
        amt_cad = round(qty * price * rate, 2)
        fee = 6.95
        _add(date=d, ticker=t, account=a, type='Buy', qty=qty, price=price, currency=cur,
             amount_native=round(qty * price, 2), amount_cad=amt_cad, fees_cad=fee,
             net_cad=round(-(amt_cad + fee), 2))

    # Sells
    for d, t, a, qty, price, cur in SELLS:
        rate = fx(d) if cur == USD else 1.0
        gross = round(qty * price * rate, 2)
        fee = 6.95
        _add(date=d, ticker=t, account=a, type='Sell', qty=qty, price=price, currency=cur,
             amount_native=round(qty * price, 2), amount_cad=gross, fees_cad=fee,
             net_cad=round(gross - fee, 2))

    # Splits
    for d, t, a, new in SPLITS:
        _add(date=d, ticker=t, account=a, type='Split', qty=new,
             notes='10-for-1 split')

    # Deposits / contributions
    for d, a, amt, sub in DEPOSITS:
        _add(date=d, ticker='CASH', account=a, type='Deposit', amount_native=amt,
             amount_cad=amt, net_cad=amt, subtype=sub, notes='Cash contribution')

    # Dividends (+ US withholding tax)
    for ticker, (ps, cur, freq) in DIV_PER_SHARE.items():
        accounts = {a for (_, t, a, *_rest) in BUYS if t == ticker}
        for a in accounts:
            first_buy = min(d for (d, t, ac, *_r) in BUYS if t == ticker and ac == a)
            for pd in pay_dates(first_buy, freq):
                sh = shares_at(ticker, a, pd)
                if sh <= 0:
                    continue
                rate = fx(pd) if cur == USD else 1.0
                native = round(ps * sh, 2)
                cad = round(native * rate, 2)
                if cad <= 0:
                    continue
                _add(date=pd, ticker=ticker, account=a, type='Dividend', currency=cur,
                     amount_native=native, amount_cad=cad, net_cad=cad)
                if cur == USD:
                    wht = round(cad * US_WHT, 2)
                    _add(date=pd, ticker=ticker, account=a, type='WithholdingTax',
                         currency=cur, amount_native=round(native * US_WHT, 2),
                         amount_cad=wht, net_cad=round(-wht, 2),
                         notes='US 15% withholding')

    # Interest
    for d, a, amt in INTEREST:
        _add(date=d, ticker='CASH', account=a, type='Interest', amount_native=amt,
             amount_cad=amt, net_cad=amt, notes='Cash interest')

    # Return of capital
    for d, t, a, amt in RETURN_OF_CAPITAL:
        _add(date=d, ticker='CASH', account=a, type='ReturnOfCapital', amount_native=amt,
             amount_cad=amt, net_cad=amt, notes=f'ROC — {t}')

    # Fees
    for d, a, amt in FEES:
        _add(date=d, ticker='CASH', account=a, type='Fee', amount_native=amt,
             amount_cad=amt, net_cad=round(-amt, 2), notes='Account fee')

    # GICs
    for name, inst, acct, principal, rate, start, mat, comp in GICS:
        db.session.add(GIC(name=name, institution=inst, account=acct, principal=principal,
                           rate=rate, start_date=start, maturity_date=mat, compounding=comp))

    # Watchlist
    for tk, co, sec, cur, tgt, ttype in WATCHLIST:
        db.session.add(WatchlistItem(ticker=tk, company=co, sector=sec, currency=cur,
                                     target_price=tgt, target_type=ttype,
                                     added_date=date(2025, 1, 15)))

    # FX default so prices work on first load
    db.session.add(Setting(key='fx_usd_cad', value='1.365'))
    db.session.commit()


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH.replace('\\', '/')
    db.init_app(app)
    with app.app_context():
        db.create_all()
        build_sample_data()
        # Summary
        from collections import Counter
        types = Counter(t.type for t in Transaction.query.all())
        print('Wrote', DB_PATH)
        print('Accounts:', [(a.name, a.type) for a in Account.query.all()])
        print('Transactions:', Transaction.query.count(), dict(types))
        print('GICs:', GIC.query.count(), '| Watchlist:', WatchlistItem.query.count())
        dates = [t.date for t in Transaction.query.all()]
        print('Date range:', min(dates), '->', max(dates))


if __name__ == '__main__':
    main()
