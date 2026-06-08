"""Charts tab — a catalog of selectable charts rendered in a configurable
1/2/3/4-pane layout. Each builder reuses the existing calculations and returns a
Chart.js-ready payload: {ok, type, title, labels, datasets[, empty]}.

Chart `type` values understood by the frontend renderer:
  line · bar · stackedBar · hbar · divergingBar · pie · doughnut
A `size` of 'wide' (time-series) or 'compact' (allocation/snapshot) hints which
pane a chart suits; it never restricts where you can place it.
"""
from calculations import (
    get_holdings, get_dashboard_stats, get_account_breakdown, get_dividend_stats,
    get_cashflow_stats, get_gic_stats, get_performance_series, get_planning_stats,
    get_projections, run_monte_carlo, get_cash_by_account,
)
from models import Account, Transaction

ACCENT = '#00c8f0'
GREEN = '#00e676'
RED = '#ff5252'
GREY = '#90a4ae'
PALETTE = ['#00c8f0', '#00e676', '#ffb74d', '#ba68c8', '#4dd0e1', '#ff8a65',
           '#9ccc65', '#f06292', '#7986cb', '#a1887f', '#dce775', '#4db6ac']


def _palette(n):
    return [PALETTE[i % len(PALETTE)] for i in range(n)]


# ── Catalog ─────────────────────────────────────────────────────────────────
# (id, name, category, size). Order here drives the grouped dropdown.
CHART_CATALOG = [
    ('value_vs_contrib',        'Value vs Contributions',     'Growth & Performance', 'wide'),
    ('value_vs_benchmarks',     'Value vs Benchmarks',        'Growth & Performance', 'wide'),
    ('book_vs_market',          'Book vs Market Value',       'Growth & Performance', 'wide'),
    ('holdings_vs_cash',        'Holdings vs Cash',           'Growth & Performance', 'wide'),
    ('projection_scenarios',    'Projection Scenarios',       'Growth & Performance', 'wide'),
    ('nominal_vs_real',         'Nominal vs Real',            'Growth & Performance', 'wide'),
    ('monte_carlo',             'Monte Carlo Bands',          'Growth & Performance', 'wide'),

    ('monthly_dividends',       'Monthly Dividend Income',    'Income',               'wide'),
    ('cumulative_dividends',    'Cumulative Dividends',       'Income',               'wide'),
    ('dividends_by_year',       'Dividends by Year',          'Income',               'compact'),
    ('dividends_by_holding',    'Dividends by Holding',       'Income',               'compact'),
    ('forward_income',          'Forward Income by Holding',  'Income',               'compact'),
    ('reinvested_by_holding',   'Reinvested by Holding',      'Income',               'compact'),

    ('alloc_account',           'By Account',                 'Allocation',           'compact'),
    ('alloc_sector',            'By Sector',                  'Allocation',           'compact'),
    ('alloc_asset_type',        'By Asset Type',              'Allocation',           'compact'),
    ('alloc_currency',          'By Currency',                'Allocation',           'compact'),
    ('alloc_market_cap',        'By Market Cap',              'Allocation',           'compact'),
    ('time_horizon',            'By Time Horizon',            'Allocation',           'compact'),

    ('top_holdings',            'Top Holdings',               'Holdings',             'compact'),
    ('unrealized_by_holding',   'Unrealized G/L by Holding',  'Holdings',             'wide'),
    ('realized_by_year',        'Realized G/L by Year',       'Holdings',             'compact'),
    ('top_movers',              'Top Movers Today',           'Holdings',             'compact'),

    ('contribution_composition', 'Contributions by Source',   'Cash Flow',            'wide'),

    ('retirement_goal',         'Retirement Goal Progress',   'Planning',             'compact'),
    ('contribution_needed',     'Contribution Needed',        'Planning',             'compact'),

    ('fx_sensitivity',          'FX Sensitivity (USD/CAD)',   'Other',                'compact'),
    ('gic_values',              'GICs — Value Growth',        'Other',                'compact'),
]

CATALOG_BY_ID = {c[0]: c for c in CHART_CATALOG}


def catalog_grouped():
    """[(category, [{id, name, size}, ...]), ...] in catalog order, for the UI."""
    groups, order = {}, []
    for cid, name, cat, size in CHART_CATALOG:
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append({'id': cid, 'name': name, 'size': size})
    return [(cat, groups[cat]) for cat in order]


# ── Shared helpers ──────────────────────────────────────────────────────────
# Every builder accepts `account` (None = whole portfolio / "All Accounts").

def _holdings(account):
    return [h for h in get_holdings() if (not account or h['account'] == account)]


def _scope(account):
    return account if account else 'portfolio'


def _breakdown(account):
    """Allocation lists by asset_type/sector/market_cap/currency for one account,
    or the whole portfolio (merged) when account is None."""
    keys = ('asset_type', 'sector', 'market_cap', 'currency')
    if account:
        bd = get_account_breakdown(account)
        return {k: bd.get(k, []) for k in keys} if bd.get('ok') else {k: [] for k in keys}
    from collections import defaultdict
    agg = {k: defaultdict(float) for k in keys}
    for a in Account.query.all():
        bd = get_account_breakdown(a.name)
        if not bd.get('ok'):
            continue
        for k in keys:
            for item in bd.get(k, []):
                agg[k][item['label']] += item['value']
    out = {}
    for k, d in agg.items():
        total = sum(d.values()) or 1
        out[k] = sorted(
            ({'label': lab, 'value': round(v, 2), 'pct': round(v / total * 100, 1)}
             for lab, v in d.items()), key=lambda x: x['value'], reverse=True)
    return out


def _scoped_value(account):
    mv = sum(h['market_value_cad'] or 0 for h in _holdings(account))
    cash_map = get_cash_by_account()
    cash = cash_map.get(account, 0.0) if account else sum(cash_map.values())
    return mv + cash


def _default_monthly_contrib(account=None):
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=365)
    q = Transaction.query.filter(Transaction.type == 'Deposit', Transaction.date >= cutoff)
    if account:
        q = q.filter_by(account=account)
    tot = sum((r.net_cad or 0) for r in q.all()
              if r.subtype not in ('RDSP Grant', 'RDSP Bond') and (r.net_cad or 0) > 0)
    return round(tot / 12, 2) if tot else 500.0


def _planning(account):
    return _scoped_value(account), _default_monthly_contrib(account)


def _empty(ctype, title, msg):
    return {'ok': True, 'type': ctype, 'title': title, 'labels': [], 'datasets': [], 'empty': msg}


# ── Builders ────────────────────────────────────────────────────────────────
def _b_value_vs_contrib(account=None):
    s = get_performance_series(_scope(account))
    if not s['labels']:
        return _empty('line', 'Value vs Contributions', 'No transactions yet.')
    total = [round((s['market_value'][i] or 0) + (s['cash'][i] or 0), 2) for i in range(len(s['labels']))]
    cum, run = [], 0.0
    for f in s['flows']:
        run += f or 0
        cum.append(round(run, 2))
    return {'ok': True, 'type': 'line', 'title': 'Portfolio Value vs Contributions', 'labels': s['labels'],
            'datasets': [{'label': 'Portfolio Value', 'data': total, 'color': ACCENT, 'fill': True},
                         {'label': 'Contributions', 'data': cum, 'color': GREY}]}


def _b_value_vs_benchmarks(account=None):
    s = get_performance_series(_scope(account))
    if not s['labels']:
        return _empty('line', 'Value vs Benchmarks', 'No transactions yet.')
    total = [round((s['market_value'][i] or 0) + (s['cash'][i] or 0), 2) for i in range(len(s['labels']))]
    ds = [{'label': 'Portfolio', 'data': total, 'color': ACCENT}]
    cols = {'S&P 500': GREEN, 'NASDAQ': '#ba68c8', 'TSX': '#ffb74d'}
    for name, series in s.get('benchmarks', {}).items():
        ds.append({'label': name, 'data': series, 'color': cols.get(name, GREY)})
    return {'ok': True, 'type': 'line', 'title': 'Portfolio vs Benchmarks (money-weighted, CAD)',
            'labels': s['labels'], 'datasets': ds}


def _b_book_vs_market(account=None):
    s = get_performance_series(_scope(account))
    if not s['labels']:
        return _empty('line', 'Book vs Market Value', 'No transactions yet.')
    return {'ok': True, 'type': 'line', 'title': 'Book Value vs Market Value (holdings)', 'labels': s['labels'],
            'datasets': [{'label': 'Market Value', 'data': s['market_value'], 'color': ACCENT, 'fill': True},
                         {'label': 'Book Value (ACB)', 'data': s['book_value'], 'color': GREY}]}


def _b_holdings_vs_cash(account=None):
    s = get_performance_series(_scope(account))
    if not s['labels']:
        return _empty('line', 'Holdings vs Cash', 'No transactions yet.')
    return {'ok': True, 'type': 'line', 'title': 'Holdings vs Cash', 'labels': s['labels'],
            'datasets': [{'label': 'Holdings', 'data': s['market_value'], 'color': ACCENT, 'fill': True},
                         {'label': 'Cash', 'data': s['cash'], 'color': '#ffb74d', 'fill': True}]}


def _b_monthly_dividends(account=None):
    mc = get_dividend_stats(_scope(account))['month_chart']
    if not mc:
        return _empty('bar', 'Monthly Dividend Income', 'No dividends recorded yet.')
    return {'ok': True, 'type': 'bar', 'title': 'Monthly Dividend Income (net CAD)',
            'labels': [x['label'] for x in mc],
            'datasets': [{'label': 'Net Dividends', 'data': [x['value'] for x in mc], 'color': GREEN}]}


def _b_cumulative_dividends(account=None):
    mc = get_dividend_stats(_scope(account))['month_chart']
    if not mc:
        return _empty('line', 'Cumulative Dividends', 'No dividends recorded yet.')
    cum, run = [], 0.0
    for x in mc:
        run += x['value']
        cum.append(round(run, 2))
    return {'ok': True, 'type': 'line', 'title': 'Cumulative Dividends Received',
            'labels': [x['label'] for x in mc],
            'datasets': [{'label': 'Cumulative', 'data': cum, 'color': GREEN, 'fill': True}]}


def _b_dividends_by_year(account=None):
    by = get_dividend_stats(_scope(account))['by_year']
    if not by:
        return _empty('bar', 'Dividends by Year', 'No dividends recorded yet.')
    years = sorted(by.keys())
    return {'ok': True, 'type': 'bar', 'title': 'Dividends by Year (net CAD)',
            'labels': [str(y) for y in years],
            'datasets': [{'label': 'Net Dividends', 'data': [round(by[y], 2) for y in years], 'color': GREEN}]}


def _b_dividends_by_holding(account=None):
    rows = [r for r in get_dividend_stats(_scope(account))['by_ticker'] if r['net']]
    rows = sorted(rows, key=lambda r: r['net'], reverse=True)[:12]
    if not rows:
        return _empty('hbar', 'Dividends by Holding', 'No dividends recorded yet.')
    return {'ok': True, 'type': 'hbar', 'title': 'Dividends by Holding (net, all-time)',
            'labels': [r['ticker'] for r in rows],
            'datasets': [{'data': [round(r['net'], 2) for r in rows], 'color': GREEN}]}


def _b_forward_income(account=None):
    rows = [r for r in get_dividend_stats(_scope(account))['by_ticker'] if r.get('fwd_income')]
    rows = sorted(rows, key=lambda r: r['fwd_income'], reverse=True)[:12]
    if not rows:
        return _empty('hbar', 'Forward Income by Holding', 'No dividend-paying holdings.')
    return {'ok': True, 'type': 'hbar', 'title': 'Forward Annual Income by Holding',
            'labels': [r['ticker'] for r in rows],
            'datasets': [{'data': [round(r['fwd_income'], 2) for r in rows], 'color': GREEN}]}


def _b_reinvested_by_holding(account=None):
    agg = {}
    for h in get_holdings(include_closed=True):
        if account and h['account'] != account:
            continue
        if h.get('reinvested_cad'):
            agg[h['ticker']] = agg.get(h['ticker'], 0) + h['reinvested_cad']
    rows = sorted(agg.items(), key=lambda x: x[1], reverse=True)[:12]
    if not rows:
        return _empty('hbar', 'Reinvested by Holding', 'No reinvested distributions (DRIP) recorded.')
    return {'ok': True, 'type': 'hbar', 'title': 'Reinvested by Holding (DRIP, all-time)',
            'labels': [t for t, _ in rows],
            'datasets': [{'data': [round(v, 2) for _, v in rows], 'color': GREEN}]}


def _alloc(key, title, ctype, account):
    items = _breakdown(account).get(key, [])
    if not items:
        return _empty(ctype, title, 'No holdings to break down.')
    return {'ok': True, 'type': ctype, 'title': title, 'labels': [i['label'] for i in items],
            'datasets': [{'data': [i['value'] for i in items], 'colors': _palette(len(items))}]}


def _b_alloc_sector(account=None):
    return _alloc('sector', 'Allocation by Sector (ETF look-through)', 'pie', account)


def _b_alloc_asset_type(account=None):
    return _alloc('asset_type', 'Allocation by Asset Type', 'doughnut', account)


def _b_alloc_currency(account=None):
    return _alloc('currency', 'Allocation by Currency', 'pie', account)


def _b_alloc_market_cap(account=None):
    return _alloc('market_cap', 'Allocation by Market Cap', 'hbar', account)


def _b_time_horizon(account=None):
    from calculations import get_horizon_breakdown, HORIZON_BUCKETS, HORIZON_COLORS
    buckets = get_horizon_breakdown(account)
    items = [(b, buckets[b]) for b in HORIZON_BUCKETS if buckets[b] > 0.005]
    if not items:
        return _empty('hbar', 'By Time Horizon', 'Nothing to bucket yet.')
    return {'ok': True, 'type': 'hbar', 'title': 'Portfolio by Time Horizon',
            'labels': [b for b, _ in items],
            'datasets': [{'data': [round(v, 2) for _, v in items],
                          'colors': [HORIZON_COLORS[b] for b, _ in items]}]}


def _b_alloc_account(account=None):
    # Inherently a cross-account view — always shows every account.
    ab = get_dashboard_stats(get_holdings())['account_breakdown']
    items = sorted(((k, v) for k, v in ab.items() if v), key=lambda x: x[1], reverse=True)
    if not items:
        return _empty('doughnut', 'Portfolio by Account', 'No holdings yet.')
    return {'ok': True, 'type': 'doughnut', 'title': 'Portfolio by Account',
            'labels': [k for k, _ in items],
            'datasets': [{'data': [round(v, 2) for _, v in items], 'colors': _palette(len(items))}]}


def _b_top_holdings(account=None):
    hs = sorted(_holdings(account), key=lambda h: (h['market_value_cad'] or 0), reverse=True)[:12]
    if not hs:
        return _empty('hbar', 'Top Holdings', 'No holdings yet.')
    return {'ok': True, 'type': 'hbar', 'title': 'Top Holdings by Market Value',
            'labels': [h['ticker'] for h in hs],
            'datasets': [{'data': [round(h['market_value_cad'] or 0, 2) for h in hs], 'color': ACCENT}]}


def _diverging(field, title, account):
    hs = [h for h in _holdings(account) if (h.get(field) or 0)]
    hs = sorted(hs, key=lambda h: abs(h.get(field) or 0), reverse=True)[:15]
    hs = sorted(hs, key=lambda h: (h.get(field) or 0), reverse=True)
    if not hs:
        return _empty('divergingBar', title, 'Nothing to show.')
    vals = [round(h.get(field) or 0, 2) for h in hs]
    return {'ok': True, 'type': 'divergingBar', 'title': title, 'labels': [h['ticker'] for h in hs],
            'datasets': [{'data': vals, 'colors': [GREEN if v >= 0 else RED for v in vals]}]}


def _b_unrealized_by_holding(account=None):
    return _diverging('unrealized_gl', 'Unrealized G/L by Holding', account)


def _realized_by_year(account):
    """Realized capital gain/loss per year (CAD), average-cost — across all
    account types (registered included) so it's not empty for sheltered accounts."""
    from collections import defaultdict
    txns = Transaction.query.order_by(Transaction.date.asc(), Transaction.id.asc()).all()
    pos, yearly = {}, defaultdict(float)
    for t in txns:
        if account and t.account != account:
            continue
        p = pos.setdefault((t.ticker, t.account), {'qty': 0.0, 'cost': 0.0, 'avg': 0.0})
        if t.type == 'Buy':
            p['cost'] += (t.amount_cad or t.qty * t.price) + (t.fees_cad or 0)
            p['qty'] += t.qty
            p['avg'] = p['cost'] / p['qty'] if p['qty'] else 0
        elif t.type == 'Split':
            p['qty'] = max(0.0, p['qty'] + t.qty)
            p['avg'] = p['cost'] / p['qty'] if p['qty'] else 0
        elif t.type == 'Sell':
            sell_qty = min(t.qty, p['qty']) if p['qty'] > 0 else 0.0
            proceeds = t.net_cad if t.net_cad is not None else 0.0
            yearly[t.date.year] += proceeds - p['avg'] * sell_qty
            p['qty'] = max(0.0, p['qty'] - t.qty)
            p['cost'] = p['avg'] * p['qty']
    return yearly


def _b_realized_by_year(account=None):
    yearly = _realized_by_year(account)
    if not yearly:
        return _empty('bar', 'Realized G/L by Year', 'No sells recorded yet.')
    years = sorted(yearly.keys())
    vals = [round(yearly[y], 2) for y in years]
    return {'ok': True, 'type': 'bar', 'title': 'Realized G/L by Year (CAD)',
            'labels': [str(y) for y in years],
            'datasets': [{'data': vals, 'colors': [GREEN if v >= 0 else RED for v in vals]}]}


def _b_top_movers(account=None):
    return _diverging('day_change', "Today's Movers (day change CAD)", account)


def _b_contribution_composition(account=None):
    c = get_cashflow_stats(account_filter=account)
    if not c['chart_years']:
        return _empty('stackedBar', 'Contributions by Source', 'No contributions recorded yet.')
    cols = {'Contribution': ACCENT, 'RDSP Grant': GREEN, 'RDSP Bond': '#ffb74d', 'Other': GREY}
    ds = [{'label': st, 'data': c['chart_by_subtype'][st], 'color': cols.get(st, GREY)}
          for st in c['known_subtypes']]
    return {'ok': True, 'type': 'stackedBar', 'title': 'Contributions by Source (yearly)',
            'labels': [str(y) for y in c['chart_years']], 'datasets': ds}


def _b_projection_scenarios(account=None):
    cv, mc = _planning(account)
    pr = get_projections(cv, mc, 30)
    return {'ok': True, 'type': 'line', 'title': f'Projection Scenarios — 30yr, ${mc:,.0f}/mo',
            'labels': pr['labels'],
            'datasets': [{'label': s['label'], 'data': s['data'], 'color': s['color']} for s in pr['scenarios']]}


def _b_nominal_vs_real(account=None):
    cv, mc = _planning(account)
    p = get_planning_stats(cv, mc, 30, 0.07, 0.02, 1_000_000, 0.05)['inflation']
    return {'ok': True, 'type': 'line', 'title': 'Nominal vs Real (30yr @ 7% / 2% inflation)',
            'labels': p['labels'],
            'datasets': [{'label': 'Nominal', 'data': p['nominal_data'], 'color': ACCENT},
                         {'label': 'Real', 'data': p['real_data'], 'color': '#ffb74d'}]}


def _b_monte_carlo(account=None):
    cv, mc = _planning(account)
    m = run_monte_carlo(cv, mc, 30, 0.07, 0.15, n_sims=300)
    return {'ok': True, 'type': 'line', 'title': 'Monte Carlo — Percentile Bands (30yr)',
            'labels': m['labels'],
            'datasets': [{'label': '90th pct', 'data': m['p90'], 'color': GREEN},
                         {'label': 'Median', 'data': m['p50'], 'color': ACCENT},
                         {'label': '10th pct', 'data': m['p10'], 'color': RED}]}


def _b_retirement_goal(account=None):
    cv, mc = _planning(account)
    r = get_planning_stats(cv, mc, 30, 0.07, 0.02, 1_000_000, 0.05)['retirement']
    return {'ok': True, 'type': 'bar', 'title': 'Retirement Goal (30yr, $1M target)',
            'labels': ['Projected', 'Target'],
            'datasets': [{'data': [r['projected'], r['target']], 'colors': [ACCENT, GREY]}]}


def _b_contribution_needed(account=None):
    cv, mc = _planning(account)
    g = get_planning_stats(cv, mc, 30, 0.07, 0.02, 1_000_000, 0.05)['contrib_grid']
    return {'ok': True, 'type': 'hbar', 'title': 'Monthly Contribution Needed (30yr @ 7%)',
            'labels': [f"${x['target'] // 1000:.0f}k" for x in g],
            'datasets': [{'data': [x['pmt'] for x in g], 'color': ACCENT}]}


def _b_fx_sensitivity(account=None):
    """How the portfolio's CAD value moves as USD/CAD changes. USD holdings'
    CAD value scales linearly with the rate, so gain/loss = USD exposure ×
    (scenario rate − current rate)."""
    from calculations import get_fx_rate
    fx = get_fx_rate()
    usd_native = sum((h['market_value_cad'] or 0) / fx
                     for h in _holdings(account) if h['currency'] == 'USD')
    if usd_native <= 0 or fx <= 0:
        return _empty('bar', 'FX Sensitivity (USD/CAD)', 'No USD holdings — nothing FX-sensitive.')
    labels, data = [], []
    for s in (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10):
        r = fx * (1 + s)
        labels.append(f'{r:.2f} ({s * 100:+.0f}%)')
        data.append(round(usd_native * (r - fx), 2))
    return {'ok': True, 'type': 'bar',
            'title': f'FX Sensitivity — USD/CAD (now {fx:.4f})',
            'labels': labels,
            'datasets': [{'data': data, 'colors': [GREEN if v >= 0 else RED for v in data]}]}


def _b_gic_values(account=None):
    gics = get_gic_stats(account_filter=account, show_matured=False)['gics']
    if not gics:
        return _empty('bar', 'GICs — Value Growth', 'No active GICs. Add one on the GICs tab.')
    labels = [g['name'] or g['institution'] or f"GIC {g['id']}" for g in gics]
    return {'ok': True, 'type': 'bar', 'title': 'GICs — Principal → Current → Maturity',
            'labels': labels,
            'datasets': [
                {'label': 'Principal', 'data': [round(g['principal'], 2) for g in gics], 'color': GREY},
                {'label': 'Current', 'data': [round(g['current_value'], 2) for g in gics], 'color': ACCENT},
                {'label': 'At Maturity', 'data': [round(g['value_at_maturity'], 2) for g in gics], 'color': GREEN},
            ]}


_BUILDERS = {
    'value_vs_contrib': _b_value_vs_contrib,
    'value_vs_benchmarks': _b_value_vs_benchmarks,
    'book_vs_market': _b_book_vs_market,
    'holdings_vs_cash': _b_holdings_vs_cash,
    'projection_scenarios': _b_projection_scenarios,
    'nominal_vs_real': _b_nominal_vs_real,
    'monte_carlo': _b_monte_carlo,
    'monthly_dividends': _b_monthly_dividends,
    'cumulative_dividends': _b_cumulative_dividends,
    'dividends_by_year': _b_dividends_by_year,
    'dividends_by_holding': _b_dividends_by_holding,
    'reinvested_by_holding': _b_reinvested_by_holding,
    'forward_income': _b_forward_income,
    'alloc_account': _b_alloc_account,
    'alloc_sector': _b_alloc_sector,
    'alloc_asset_type': _b_alloc_asset_type,
    'alloc_currency': _b_alloc_currency,
    'alloc_market_cap': _b_alloc_market_cap,
    'time_horizon': _b_time_horizon,
    'top_holdings': _b_top_holdings,
    'unrealized_by_holding': _b_unrealized_by_holding,
    'realized_by_year': _b_realized_by_year,
    'top_movers': _b_top_movers,
    'contribution_composition': _b_contribution_composition,
    'retirement_goal': _b_retirement_goal,
    'contribution_needed': _b_contribution_needed,
    'fx_sensitivity': _b_fx_sensitivity,
    'gic_values': _b_gic_values,
}


def build_chart(chart_id, account=None):
    fn = _BUILDERS.get(chart_id)
    if not fn:
        return {'ok': False, 'error': 'Unknown chart'}
    try:
        return fn(account)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
