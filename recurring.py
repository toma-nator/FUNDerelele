"""Recurring / scheduled transactions.

A small rule engine that materializes concrete Transaction rows from
RecurringRule templates up to today. The amount/sign logic (compute_amounts) is
shared with the manual Add-Transaction route so the two never diverge.
"""
import calendar
from datetime import date, timedelta

from models import db, Transaction, RecurringRule

FREQUENCIES = ('weekly', 'monthly', 'quarterly', 'yearly')


def compute_amounts(txn_type, qty, price, amount_in, fees, rate):
    """Return (amount_native, amount_cad, net_cad) for a transaction, matching
    the manual Add-Transaction convention. `rate` is FX→CAD (1.0 for CAD)."""
    if txn_type in ('Buy', 'Sell', 'Reinvest'):
        amount_native = qty * price          # share trade — value from qty × price
    elif txn_type == 'Split':
        amount_native = 0.0                  # only adds shares
    else:
        amount_native = amount_in            # income/cash types — a single total
    amount_cad = amount_native * rate

    if txn_type == 'Sell':
        net_cad = amount_cad - fees
    elif txn_type in ('Dividend', 'Interest', 'ReturnOfCapital', 'Deposit'):
        net_cad = amount_cad - fees          # cash in
    elif txn_type == 'Split':
        net_cad = 0.0
    else:  # Buy, Reinvest, WithholdingTax, Fee — cash out
        net_cad = -(amount_cad + fees)
    return amount_native, amount_cad, net_cad


def _add_months(d, n):
    """Step `d` forward n months, clamping the day to the target month's length
    (Jan 31 + 1mo → Feb 28/29)."""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def next_occurrence(d, freq):
    if freq == 'weekly':
        return d + timedelta(days=7)
    if freq == 'monthly':
        return _add_months(d, 1)
    if freq == 'quarterly':
        return _add_months(d, 3)
    if freq == 'yearly':
        return _add_months(d, 12)
    raise ValueError(f'Unknown frequency: {freq}')


def generate_due(as_of=None):
    """Materialize all due occurrences of active rules up to `as_of` (today).

    Idempotent: each rule's next_date advances as rows are created, so re-running
    never duplicates. Returns the number of transactions created.
    """
    as_of = as_of or date.today()
    rules = RecurringRule.query.filter_by(active=True).all()
    if not rules:
        return 0
    try:
        from price_service import get_fx_rate
        fx = get_fx_rate()
    except Exception:
        fx = 1.365

    created, tickers = 0, set()
    for rule in rules:
        while (rule.active and rule.next_date and rule.next_date <= as_of
               and (not rule.end_date or rule.next_date <= rule.end_date)):
            rate = fx if rule.currency == 'USD' else 1.0
            an, ac, nc = compute_amounts(rule.type, rule.qty or 0, rule.price or 0,
                                         rule.amount or 0, rule.fees or 0, rate)
            tkr = rule.ticker or 'CASH'
            db.session.add(Transaction(
                date=rule.next_date, ticker=tkr, account=rule.account, type=rule.type,
                qty=rule.qty or 0, price=rule.price or 0, currency=rule.currency,
                amount_native=an, amount_cad=ac, fees_cad=rule.fees or 0, net_cad=nc,
                notes=rule.notes or '', subtype=rule.subtype or '', recurring_id=rule.id))
            created += 1
            if tkr != 'CASH':
                tickers.add(tkr)
            rule.last_run = rule.next_date
            rule.next_date = next_occurrence(rule.next_date, rule.frequency)
            if rule.count_remaining is not None:
                rule.count_remaining -= 1
                if rule.count_remaining <= 0:
                    rule.active = False
        if rule.end_date and rule.next_date and rule.next_date > rule.end_date:
            rule.active = False

    db.session.commit()
    if tickers:
        try:
            from price_service import refresh_prices
            refresh_prices(list(tickers))
        except Exception:
            pass
    return created
