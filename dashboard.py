"""Dashboard data layer — hero strip, the KPI catalog, and the table/feed
widgets. Chart widgets are served by charts.py (reused, not duplicated).

Everything here is portfolio-wide; the per-account view lives in the Account
Highlights widget. Values are pre-formatted strings so the frontend can drop
them straight into tiles."""
from datetime import date
from calculations import (
    get_holdings, get_dashboard_stats, get_dividend_stats, get_account_summary,
    get_gic_stats, get_performance_series, get_performance_data, get_contribution_room,
    ROOM_TYPES,
)
from charts import _realized_by_year
from models import Transaction, Account


# ── formatting ──────────────────────────────────────────────────────────────
def _cad(v):
    return '—' if v is None else f'${v:,.2f}'


def _signed(v):
    if v is None:
        return '—'
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _pct(v):
    if v is None:
        return '—'
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


def _cls(v):
    if v is None or abs(v) < 1e-9:
        return 'text-dim'
    return 'text-green' if v > 0 else 'text-red'


# ── KPI catalog (id, label) — drives the "Add KPI" menu ─────────────────────
KPI_CATALOG = [
    ('total_value', 'Total Portfolio'), ('unrealized', 'Unrealized G/L'),
    ('day_change', "Today's Change"), ('total_return', 'Total Return'),
    ('div_all', 'Dividends · All-time'), ('div_ttm', 'Dividends · TTM'),
    ('div_ytd', 'Dividends · YTD'), ('fwd_income', 'Forward Income'),
    ('realized_ytd', 'Realized G/L · YTD'), ('realized_all', 'Realized G/L · All-time'),
    ('contributions', 'Net Contributions'), ('room', 'Contribution Room'),
    ('cash', 'Cash'), ('best', 'Best Performer'), ('worst', 'Worst Performer'),
    ('counts', 'Holdings · Accounts'), ('avg_div_mo', 'Avg Dividend / mo'),
    ('cagr', 'CAGR'),
]


def _personal_contributions():
    from calculations import get_gic_value_by_account
    total = 0.0
    for net, sub in (Transaction.query.filter_by(type='Deposit')
                     .with_entities(Transaction.net_cad, Transaction.subtype)):
        n = net or 0.0
        if sub not in ('RDSP Grant', 'RDSP Bond') and n > 0:
            total += n
    # GIC principal isn't booked as a cash deposit; count it as contributed capital
    # so the gain on net worth (which now includes GIC value) is only the interest.
    total += sum(v['principal'] for v in get_gic_value_by_account().values())
    return total


def _room_total():
    """Summed contribution room across registered types (deduped — room pools
    per type). Returns (total, any_found)."""
    seen, total = set(), 0.0
    for a in Account.query.all():
        t = (a.type or '').upper()
        if t in ROOM_TYPES and t not in seen:
            r = get_contribution_room(a.name, a.type)
            if r and r.get('ready') and r.get('total_remaining') is not None:
                total += r['total_remaining']
                seen.add(t)
    return total, bool(seen)


def price_alerts():
    """Watchlist items whose live price has crossed their target: below a buy
    target ('below') or above a sell target ('above'). Drives the hero alert."""
    from models import WatchlistItem, PriceCache
    out = []
    for it in WatchlistItem.query.order_by(WatchlistItem.ticker).all():
        if not it.target_price:
            continue
        pc = PriceCache.query.get(it.ticker)
        price = pc.price if pc else None
        if not price:
            continue
        tt = it.target_type or 'below'
        if tt == 'below' and price <= it.target_price:
            d, label = 'buy', 'below buy target'
        elif tt == 'above' and price >= it.target_price:
            d, label = 'sell', 'above sell target'
        else:
            continue
        out.append({'ticker': it.ticker, 'dir': d, 'label': label,
                    'price': _cad(price), 'target': _cad(it.target_price)})
    return out


def build_overview():
    """Hero + every KPI in one pass (shared computations)."""
    holdings = get_holdings()
    st = get_dashboard_stats(holdings)
    ds = get_dividend_stats('portfolio')
    contrib = _personal_contributions()
    realized = _realized_by_year(None)

    total = st['total_portfolio']
    all_time_gain = total - contrib
    overall_pct = (all_time_gain / contrib * 100) if contrib else 0.0
    yr = date.today().year

    hero = {
        'total': _cad(total),
        'gain': _signed(all_time_gain),
        'overall_pct': _pct(overall_pct), 'overall_cls': _cls(all_time_gain),
        'unrealized': _signed(st['total_unrealized']),
        'unrealized_pct': _pct(st['total_unrealized_pct']), 'unrealized_cls': _cls(st['total_unrealized']),
        'day': _signed(st['total_day_change']), 'day_cls': _cls(st['total_day_change']),
        'num_holdings': st['num_holdings'], 'cash': _cad(st['total_cash']),
    }

    room_total, has_room = _room_total()
    perf = [h for h in holdings if (h['book_value_cad'] or 0) > 1]
    best = max(perf, key=lambda h: h['unrealized_gl_pct']) if perf else None
    worst = min(perf, key=lambda h: h['unrealized_gl_pct']) if perf else None
    naccts = len({h['account'] for h in holdings})
    try:
        cagr = get_performance_data().get('cagr')
    except Exception:
        cagr = None

    K = {}

    def add(kid, label, value, sub, cls=''):
        K[kid] = {'label': label, 'value': value, 'sub': sub, 'cls': cls}

    add('total_value', 'Total Portfolio', _cad(total), f"{st['num_holdings']} holdings")
    add('unrealized', 'Unrealized G/L', _signed(st['total_unrealized']),
        _pct(st['total_unrealized_pct']), _cls(st['total_unrealized']))
    add('day_change', "Today's Change", _signed(st['total_day_change']), 'market hours', _cls(st['total_day_change']))
    add('total_return', 'Total Return', _pct(overall_pct), 'on contributions', _cls(overall_pct))
    add('div_all', 'Dividends · All-time', _cad(ds['all_time']), 'net', 'text-green')
    add('div_ttm', 'Dividends · TTM', _cad(ds['ttm']), 'last 12 mo', 'text-green')
    add('div_ytd', 'Dividends · YTD', _cad(ds['ytd']), str(yr), 'text-green')
    add('fwd_income', 'Forward Income', _cad(ds['forward_income']), f"{ds['forward_yield']:.2f}% yield", 'text-green')
    add('reinvested', 'Reinvested', _cad(st['total_reinvested']), 'DRIP — holdings not paid for', 'text-green')
    add('realized_ytd', 'Realized G/L · YTD', _signed(realized.get(yr, 0.0)), str(yr), _cls(realized.get(yr, 0.0)))
    add('realized_all', 'Realized G/L · All-time', _signed(sum(realized.values())), 'all-time', _cls(sum(realized.values())))
    add('contributions', 'Net Contributions', _cad(contrib), 'all-time')
    add('room', 'Contribution Room', _cad(room_total) if has_room else '—', 'registered')
    add('cash', 'Cash', _cad(st['total_cash']), f"{(st['total_cash'] / total * 100) if total else 0:.0f}% of port")
    add('best', 'Best Performer', best['ticker'] if best else '—',
        _pct(best['unrealized_gl_pct']) if best else '', _cls(best['unrealized_gl_pct']) if best else 'text-dim')
    add('worst', 'Worst Performer', worst['ticker'] if worst else '—',
        _pct(worst['unrealized_gl_pct']) if worst else '', _cls(worst['unrealized_gl_pct']) if worst else 'text-dim')
    add('counts', 'Holdings · Accounts', f"{st['num_holdings']} · {naccts}", 'positions · accounts')
    add('avg_div_mo', 'Avg Dividend / mo', _cad(ds['ttm_monthly_avg']), 'TTM', 'text-green')
    add('cagr', 'CAGR', _pct(cagr) if cagr is not None else '—', 'annualized', _cls(cagr) if cagr is not None else 'text-dim')

    return {'hero': hero, 'kpis': K}


def sparkline():
    s = get_performance_series('portfolio')
    return {'labels': s['labels'],
            'values': [round((s['market_value'][i] or 0) + (s['cash'][i] or 0), 2)
                       for i in range(len(s['labels']))]}


# ── table / feed widgets ────────────────────────────────────────────────────
def _account_perf(account):
    """(1-year return %, annualized TWR %) for an account, from its monthly value
    series. TWR links monthly sub-period returns (contributions treated as
    invested at period start), then annualizes over the elapsed months."""
    s = get_performance_series(account)
    n = len(s['labels'])
    if n < 2:
        return None, None
    vals = [(s['market_value'][i] or 0) + (s['cash'][i] or 0) for i in range(n)]
    flows = s['flows']

    k = min(12, n - 1)
    then, now = vals[n - 1 - k], vals[-1]
    y1 = ((now - then - sum(flows[n - k:])) / then * 100) if then > 0 else None

    link, periods = 1.0, 0
    for i in range(1, n):
        base = vals[i - 1] + (flows[i] or 0)
        if base > 0:
            link *= vals[i] / base
            periods += 1
    if periods == 0 or link <= 0:
        twr = None
    else:
        years = periods / 12.0
        twr = ((link ** (1 / years) - 1) * 100) if years > 0 else (link - 1) * 100
    return y1, twr


# Per-account columns the Account Highlights widget can show. (id, label)
ACCOUNT_COL_DEFS = [
    ('value', 'Value'), ('overall', 'Overall'), ('y1', '1Y'), ('twr', 'TWR'),
    ('unrealized_pct', 'Unrealized %'), ('div_ttm', 'Div TTM'), ('div_all', 'Div All'),
    ('realized', 'Realized'), ('cash', 'Cash'), ('cash_pct', 'Cash %'),
    ('book', 'Book Value'), ('holdings', '# Holdings'), ('day', 'Today'),
]
ACCOUNT_COLS = dict(ACCOUNT_COL_DEFS)
DEFAULT_ACCOUNT_COLS = ['value', 'overall', 'y1', 'twr', 'div_ttm', 'day']


def _cell(val, cls=''):
    return {'val': val, 'cls': cls}


def w_account_highlights(basis='contrib', cols_csv=''):
    cols = [c for c in cols_csv.split(',') if c in ACCOUNT_COLS] or DEFAULT_ACCOUNT_COLS
    selected = [(c, ACCOUNT_COLS[c]) for c in cols]
    available = [(cid, label) for cid, label in ACCOUNT_COL_DEFS if cid not in cols]
    need_perf = any(c in cols for c in ('y1', 'twr'))
    need_div = any(c in cols for c in ('div_ttm', 'div_all'))

    accounts = []
    for a in get_account_summary():
        tv = a['total_value']
        oc = (a['all_time_gain'] / a['net_contributions'] * 100) if a['net_contributions'] else 0.0
        overall = a['unrealized_gl_pct'] if basis == 'unrealized' else oc
        y1, twr = _account_perf(a['name']) if need_perf else (None, None)
        ds = get_dividend_stats(a['name']) if need_div else {'ttm': 0.0, 'all_time': 0.0}
        cells = {
            'value': _cell(_cad(tv)),
            'overall': _cell(_pct(overall), _cls(overall)),
            'y1': _cell(_pct(y1) if y1 is not None else '—', _cls(y1) if y1 is not None else 'text-dim'),
            'twr': _cell(_pct(twr) if twr is not None else '—', _cls(twr) if twr is not None else 'text-dim'),
            'unrealized_pct': _cell(_pct(a['unrealized_gl_pct']), _cls(a['unrealized_gl_pct'])),
            'div_ttm': _cell(_cad(ds['ttm']), 'text-green'),
            'div_all': _cell(_cad(ds['all_time']), 'text-green'),
            'realized': _cell(_signed(a['realized_total']), _cls(a['realized_total'])),
            'cash': _cell(_cad(a['cash_balance'])),
            'cash_pct': _cell(f"{a['cash_balance'] / tv * 100:.0f}%" if tv else '—'),
            'book': _cell(_cad(a['holdings_book'])),
            'holdings': _cell(str(a['num_holdings'])),
            'day': _cell(_signed(a['day_change']), _cls(a['day_change'])),
        }
        accounts.append({'name': a['name'], 'type': a['type'], 'cells': cells})
    return {'accounts': accounts, 'cols': selected, 'available': available, 'basis': basis}


def w_top_holdings(count='5'):
    hs = get_holdings()
    total = sum(h['market_value_cad'] or 0 for h in hs) or 1
    hs = sorted(hs, key=lambda h: (h['market_value_cad'] or 0), reverse=True)
    total_n = len(hs)
    # count is '5' / '10' / 'all' (default 5); anything else falls back to 5.
    n = total_n if count == 'all' else (int(count) if str(count).isdigit() else 5)
    shown = hs[:n]
    return {'count': count, 'total_n': total_n,
            'rows': [{'ticker': h['ticker'], 'account': h['account'],
                      'mv': _cad(h['market_value_cad'] or 0),
                      'gl': _signed(h['unrealized_gl'] or 0), 'gl_cls': _cls(h['unrealized_gl'] or 0),
                      'pct': f"{(h['market_value_cad'] or 0) / total * 100:.1f}%"} for h in shown]}


def w_recent_transactions():
    txns = (Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(8).all())
    return {'rows': [{'date': t.date.strftime('%b %d'), 'type': t.type, 'ticker': t.ticker,
                      'account': t.account, 'amount': _signed(t.net_cad or 0),
                      'amount_cls': _cls(t.net_cad or 0)} for t in txns]}


def w_recent_dividends():
    divs = (Transaction.query.filter_by(type='Dividend')
            .order_by(Transaction.date.desc(), Transaction.id.desc()).limit(8).all())
    return {'rows': [{'date': d.date.strftime('%b %d, %Y'), 'ticker': d.ticker,
                      'account': d.account, 'amount': _cad(d.amount_cad or 0)} for d in divs]}


def w_watchlist():
    from models import WatchlistItem, PriceCache
    rows = []
    for it in WatchlistItem.query.order_by(WatchlistItem.ticker).limit(10).all():
        pc = PriceCache.query.get(it.ticker)
        price = pc.price if pc else None
        dist = ((price - it.target_price) / it.target_price * 100) if (price and it.target_price) else None
        rows.append({'ticker': it.ticker, 'price': _cad(price) if price else '—',
                     'target': _cad(it.target_price) if it.target_price else '—',
                     'kind': it.target_type or '',
                     'dist': _pct(dist) if dist is not None else '—',
                     'dist_cls': _cls(dist) if dist is not None else 'text-dim'})
    return {'rows': rows}


def w_gic_maturity():
    g = get_gic_stats(show_matured=False)['gics']
    return {'rows': [{'name': x['name'] or x['institution'] or f"GIC {x['id']}",
                      'maturity': x['maturity_date'].strftime('%b %d, %Y') if x['maturity_date'] else '—',
                      'days': x['days_remaining'], 'value': _cad(x['value_at_maturity'])} for x in g[:8]]}


def w_rebalancer_drift():
    from calculations import get_rebalancer_gaps_all, get_rebalancer_gap_summary
    try:
        summ = get_rebalancer_gap_summary(get_rebalancer_gaps_all())
    except Exception:
        summ = []
    return {'rows': [{'account': s['account'], 'total': _cad(s['total']),
                      'detail': ', '.join(f"{_cad(l['amount'])} {l['label']}" for l in s['lines'][:2])}
                     for s in summ]}


def w_contribution_room():
    seen, rows = set(), []
    for a in Account.query.order_by(Account.name).all():
        t = (a.type or '').upper()
        if t in ROOM_TYPES and t not in seen:
            r = get_contribution_room(a.name, a.type)
            if r and r.get('ready'):
                cap = r.get('total_cap') or r.get('lifetime_cap')
                used_pct = max(0, min(100, (1 - r['total_remaining'] / cap) * 100)) if cap else None
                rows.append({'type': t, 'remaining': _cad(r['total_remaining']),
                             'cap': _cad(cap) if cap else '', 'pct': used_pct})
                seen.add(t)
    return {'rows': rows}


# id -> (name, size, fn)
HTML_WIDGETS = [
    ('account_highlights', 'Account Highlights', 'wide', w_account_highlights),
    ('top_holdings', 'Top Holdings', 'wide', w_top_holdings),
    ('recent_transactions', 'Recent Transactions', 'compact', w_recent_transactions),
    ('recent_dividends', 'Recent Dividends', 'compact', w_recent_dividends),
    ('watchlist', 'Watchlist', 'compact', w_watchlist),
    ('gic_maturity', 'GIC Maturity', 'compact', w_gic_maturity),
    ('rebalancer_drift', 'Rebalancer Drift', 'compact', w_rebalancer_drift),
    ('contribution_room', 'Contribution Room', 'compact', w_contribution_room),
]
HTML_WIDGET_FNS = {w[0]: w[3] for w in HTML_WIDGETS}
HTML_WIDGET_NAMES = {w[0]: w[1] for w in HTML_WIDGETS}


def widget_catalog_grouped():
    """Grouped widget library for the 'Add widget' menu: the table/feed widgets
    plus every chart (namespaced 'chart:<id>')."""
    from charts import catalog_grouped
    groups = [('Tables & Feeds', [{'id': w[0], 'name': w[1], 'size': w[2]} for w in HTML_WIDGETS])]
    for cat, items in catalog_grouped():
        groups.append((cat, [{'id': 'chart:' + it['id'], 'name': it['name'], 'size': it['size']}
                             for it in items]))
    return groups
