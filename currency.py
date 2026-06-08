"""Currency Exchange (Norbert's gambit / broker FX conversion).

An exchange moves cash between an account's currency pools. It's recorded as two
net-zero CASH legs — cash *out* of the source currency and *in* to the target —
so it changes the per-currency cash split without touching net worth,
contributions, dividends, or the performance series (the legs cancel in CAD and
the type is not a Deposit).
"""
import time
from datetime import date

from models import db, Transaction


def add_exchange(account, txn_date, from_ccy, from_amt, to_ccy, to_amt, notes=''):
    """Record an exchange as two CASH legs. One side must be CAD; the CAD amount
    anchors both legs' CAD value so they net to zero. Returns (out_leg, in_leg)."""
    from_ccy = (from_ccy or 'CAD').upper()
    to_ccy = (to_ccy or 'CAD').upper()
    if from_ccy == to_ccy:
        raise ValueError('From and To currencies must differ.')
    if 'CAD' not in (from_ccy, to_ccy):
        raise ValueError('One side of an exchange must be CAD.')
    if from_amt <= 0 or to_amt <= 0:
        raise ValueError('Both amounts must be positive.')

    cad_equiv = from_amt if from_ccy == 'CAD' else to_amt
    batch = f'fx-{int(time.time())}'
    label = notes or f'Exchange {from_ccy}→{to_ccy}'

    out_leg = Transaction(
        date=txn_date, ticker='CASH', account=account, type='CurrencyExchange',
        qty=0, price=0, currency=from_ccy, amount_native=from_amt, amount_cad=cad_equiv,
        fees_cad=0, net_cad=-cad_equiv, notes=label, import_batch=batch)
    in_leg = Transaction(
        date=txn_date, ticker='CASH', account=account, type='CurrencyExchange',
        qty=0, price=0, currency=to_ccy, amount_native=to_amt, amount_cad=cad_equiv,
        fees_cad=0, net_cad=cad_equiv, notes=label, import_batch=batch)
    db.session.add(out_leg)
    db.session.add(in_leg)
    db.session.commit()
    return out_leg, in_leg


def reconcile_account_fx(account):
    """Zero an account's non-CAD cash pools that are an artifact of foreign buys
    funded by an (unrecorded) CAD→foreign conversion at purchase time.

    For each non-CAD currency, create a balancing exchange whose CAD value equals
    the CAD already recorded for that currency's activity — so the foreign pool
    goes to 0 and CAD cash returns to the true (historical) total, undoing the
    live-FX revaluation of the phantom balance. Idempotent: no-op when the pool is
    already ~0. Assumes no real foreign cash is held. Returns rules created."""
    from calculations import get_cash_by_account_currency
    pools = get_cash_by_account_currency().get(account, {})
    today = date.today()
    made = 0
    for ccy, native in list(pools.items()):
        if ccy == 'CAD' or abs(native) < 0.005:
            continue
        # CAD recorded for this currency's activity (so CAD cash lands on the
        # historical total once the foreign pool is zeroed).
        cad_equiv = -sum((t.net_cad or 0) for t in Transaction.query
                         .filter_by(account=account, currency=ccy).all())
        cad_equiv = round(abs(cad_equiv), 2)
        amt = round(abs(native), 2)
        if cad_equiv < 0.005 or amt < 0.005:
            continue
        if native < 0:   # short foreign cash → convert CAD into it
            add_exchange(account, today, 'CAD', cad_equiv, ccy, amt,
                         notes=f'FX reconcile: CAD→{ccy} (funding of {ccy} purchases)')
        else:            # excess foreign cash → convert it back to CAD
            add_exchange(account, today, ccy, amt, 'CAD', cad_equiv,
                         notes=f'FX reconcile: {ccy}→CAD')
        made += 1
    return made
