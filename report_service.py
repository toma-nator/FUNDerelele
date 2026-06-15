"""Deep-dive report data — assembles one `report_data` object from a cached AI plan
plus FREE yfinance enrichment (no AI call). Feeds both the Watchlist card (a subset)
and the full HTML/PDF report.

Everything here derives from data we already have — the cached plan's trades (already
validated with live price / shares / yield), the account's holdings, the cached
`meta_json` classification (beta, volatility, sector, market cap, MER, AUM, 52-wk range,
business description), and a SINGLE batched 1-year price-history fetch reused across
sparkline / correlation / 1-yr return. Nothing here costs money; the only billable step
is the AI plan generation itself.
"""

import math

# Allocation lenses shown in the report (label, dimension key). "Asset class" uses ETF
# look-through (stock/bond/cash); the others use the rebalancer bucket weights.
_ALLOC_LENSES = [('Asset class', 'asset_class'), ('Blended risk', 'blend'), ('Sector', 'sector')]


# ── Pure helpers (no DB / network — unit-tested in tests/test_report_service.py) ──

def _returns(closes):
    """Period-over-period simple returns from a close series."""
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def _corr(a, b):
    """Pearson correlation of two return series (aligned to the shorter, from the end)."""
    n = min(len(a), len(b))
    if n < 8:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def _max_drawdown(closes):
    """Largest peak-to-trough decline over the series (a negative fraction, or None)."""
    if len(closes) < 2:
        return None
    peak, mdd = closes[0], 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak:
            mdd = min(mdd, c / peak - 1)
    return mdd


def _ret_1y(closes):
    return (closes[-1] / closes[0] - 1) if len(closes) >= 2 and closes[0] else None


def _spark(closes, n=24):
    """Downsample a close series to ~n evenly-spaced points for a sparkline."""
    if not closes:
        return []
    if len(closes) <= n:
        return [round(c, 4) for c in closes]
    step = (len(closes) - 1) / (n - 1)
    return [round(closes[int(round(i * step))], 4) for i in range(n)]


def _corr_tag(c):
    """Plain-English read of a correlation-to-book value."""
    if c is None:
        return None
    if c < 0.3:
        return 'strong diversifier'
    if c < 0.7:
        return 'moderate'
    return 'higher overlap'


def _alloc_weights(positions, cash_val, lens):
    """{bucket: pct} for a {ticker: {value, ticker, m}} map under one allocation lens,
    including cash. Returns (rows sorted desc, total_value)."""
    from calculations import _bucket_weights, _asset_class_weights
    agg, tot = {}, 0.0
    for p in positions.values():
        mv = p['value']
        if mv <= 0:
            continue
        tot += mv
        if lens == 'asset_class':
            w = _asset_class_weights(p['m'])
        else:
            w = _bucket_weights({'ticker': p['ticker'], 'currency': 'CAD'}, p['m'], lens)
        for b, frac in w.items():
            agg[b] = agg.get(b, 0.0) + mv * frac
    if cash_val > 0:
        agg['Cash'] = agg.get('Cash', 0.0) + cash_val
        tot += cash_val
    if not tot:
        return {}, 0.0
    return {b: v / tot * 100 for b, v in agg.items()}, tot


def _portfolio_risk(positions, cash_val):
    """Value-weighted beta, volatility, blended fund MER, and top-holding weight. Cash
    contributes 0 to beta/vol but dilutes via the denominator. Volatility is a weighted
    average (ignores cross-correlation) — a KPI estimate, not a covariance model."""
    tot = sum(p['value'] for p in positions.values() if p['value'] > 0) + max(0.0, cash_val)
    if tot <= 0:
        return {'beta': None, 'volatility': None, 'mer': None, 'top_weight': None}
    beta = vol = 0.0
    fund_val = mer_wsum = 0.0
    have_beta = have_vol = False
    top = max(0.0, cash_val) / tot
    for p in positions.values():
        mv = p['value']
        if mv <= 0:
            continue
        m, w = p['m'], mv / tot
        top = max(top, w)
        if m.get('beta') is not None:
            beta += w * m['beta']; have_beta = True
        if m.get('volatility') is not None:
            vol += w * m['volatility']; have_vol = True
        if m.get('expense_ratio') is not None:
            fund_val += mv; mer_wsum += mv * m['expense_ratio']
    return {
        'beta': round(beta, 2) if have_beta else None,
        'volatility': round(vol * 100, 1) if have_vol else None,   # vol is a fraction → percent
        # expense_ratio from yfinance is already a percentage figure (SPY → 0.0945 ≈ 0.09%),
        # so the value-weighted blend is a percentage too — no ×100.
        'mer': round(mer_wsum / fund_val, 3) if fund_val else None,
        'top_weight': round(top * 100, 1),
    }


def _portfolio_returns(positions, rets):
    """Value-weighted blended return series for a holdings map (for correlation)."""
    parts = [(p['value'], rets[tk]) for tk, p in positions.items()
             if p['value'] > 0 and rets.get(tk)]
    if not parts:
        return []
    L = min(len(r) for _, r in parts)
    if L < 8:
        return []
    wsum = sum(v for v, _ in parts) or 1
    return [sum(v / wsum * r[-L:][i] for v, r in parts) for i in range(L)]


# ── History fetch (free, batched, best-effort) ───────────────────────────────────

def _history(tickers):
    """{ticker: [weekly closes ~1y]} via a single batched download. Best-effort:
    silently drops anything that doesn't resolve so the report still renders."""
    import yfinance as yf
    tickers = [t for t in {*tickers} if t and t != 'CASH' and ' ' not in t]
    if not tickers:
        return {}
    try:
        df = yf.download(tickers, period='1y', interval='1wk', auto_adjust=True,
                         progress=False, group_by='ticker', threads=True)
    except Exception:
        return {}
    out = {}
    for t in tickers:
        try:
            s = df['Close'] if len(tickers) == 1 else df[t]['Close']
            vals = [float(x) for x in s.dropna().tolist()]
            if len(vals) >= 8:
                out[t] = vals
        except Exception:
            pass
    return out


def _alt_stats(alt, metas, hist):
    """Comparison-table row for one alternate: profile + free fundamentals."""
    tk = alt.get('ticker', '')
    m = metas.get(tk, {}) or {}
    closes = hist.get(tk, [])
    return {
        'ticker': tk,
        'profile': alt.get('profile', ''),
        'drift_note': alt.get('drift_note', ''),
        'yield': round(m['dividend_yield'], 2) if m.get('dividend_yield') is not None else None,
        'mer': round(m['expense_ratio'], 3) if m.get('expense_ratio') is not None else None,
        'aum': m.get('total_assets'),
        'ret_1y': round(_ret_1y(closes) * 100, 1) if _ret_1y(closes) is not None else None,
        'volatility': round(m['volatility'] * 100, 1) if m.get('volatility') is not None else None,
    }


# ── Orchestrator ─────────────────────────────────────────────────────────────────

def build_report_data(account, plan):
    """Assemble the deep-dive `report_data` for one account's cached plan. Free."""
    from calculations import get_holdings, get_cash_by_account, get_dividend_stats
    from price_service import get_holdings_metadata
    from models import Account

    trades = plan.get('trades', [])
    buys = [t for t in trades if t.get('action') == 'Buy']
    sells = [t for t in trades if t.get('action') == 'Sell']
    buy_total = sum(t['amount_cad'] for t in buys)
    sell_total = sum(t['amount_cad'] for t in sells)

    holdings = [h for h in get_holdings()
                if h['account'] == account and (h['market_value_cad'] or 0) > 0]
    cash = max(0.0, get_cash_by_account().get(account, 0.0))
    acct = Account.query.filter_by(name=account).first()
    acct_type = (acct.type if acct else None) or 'Non-Reg'

    # One metadata read + one batched history fetch over every ticker we'll touch.
    alt_tickers = [a['ticker'] for t in trades for a in t.get('alternates', [])]
    union = list({*(h['ticker'] for h in holdings), *(t['ticker'] for t in trades), *alt_tickers})
    metas = get_holdings_metadata(union) if union else {}
    hist = _history(union)
    rets = {t: _returns(c) for t, c in hist.items()}

    # before / after position maps {ticker: {value, ticker, m}}
    before = {h['ticker']: {'value': h['market_value_cad'], 'ticker': h['ticker'],
                            'm': metas.get(h['ticker'], {})} for h in holdings}
    after = {tk: {'value': p['value'], 'ticker': tk, 'm': p['m']} for tk, p in before.items()}
    for t in buys:
        tk = t['ticker']
        after.setdefault(tk, {'value': 0.0, 'ticker': tk, 'm': metas.get(tk, {})})
        after[tk]['value'] += t['amount_cad']
    for t in sells:
        if t['ticker'] in after:
            after[t['ticker']]['value'] = max(0.0, after[t['ticker']]['value'] - t['amount_cad'])
    after_cash = max(0.0, cash + sell_total - buy_total)

    # allocation before → after, per lens
    allocation = []
    for label, lens in _ALLOC_LENSES:
        bw, _ = _alloc_weights(before, cash, lens)
        aw, _ = _alloc_weights(after, after_cash, lens)
        labels = sorted(set(bw) | set(aw), key=lambda k: aw.get(k, bw.get(k, 0)), reverse=True)
        allocation.append({
            'label': label,
            'rows': [{'bucket': k, 'before': round(bw.get(k, 0), 1), 'after': round(aw.get(k, 0), 1)}
                     for k in labels],
        })

    # portfolio risk & cost before → after
    risk = {'before': _portfolio_risk(before, cash), 'after': _portfolio_risk(after, after_cash)}

    # dividend impact before → after (account-wide forward income)
    before_fwd = get_dividend_stats(account).get('forward_income', 0.0) or 0.0
    buy_inc = sum((t.get('yield_pct') or 0) / 100 * t['amount_cad'] for t in buys)
    sell_inc = sum((t.get('yield_pct') or 0) / 100 * t['amount_cad'] for t in sells)
    after_fwd = max(0.0, before_fwd + buy_inc - sell_inc)
    inv_before = sum(h['market_value_cad'] for h in holdings) or 0.0
    inv_after = max(0.0, inv_before + buy_total - sell_total)
    dividend = {
        'before': round(before_fwd, 0), 'after': round(after_fwd, 0),
        'delta': round(after_fwd - before_fwd, 0),
        'yield_before': round(before_fwd / inv_before * 100, 1) if inv_before else None,
        'yield_after': round(after_fwd / inv_after * 100, 1) if inv_after else None,
    }

    # per-holding deep dive (the buys / new names)
    port_ret = _portfolio_returns(before, rets)
    holdings_detail = []
    for t in buys:
        tk = t['ticker']
        m = metas.get(tk, {}) or {}
        closes = hist.get(tk, [])
        corr = _corr(rets.get(tk, []), port_ret)
        holdings_detail.append({
            'ticker': tk, 'name': m.get('long_name') or tk,
            'amount_cad': t['amount_cad'],
            'pct_of_buys': round(t['amount_cad'] / buy_total * 100, 0) if buy_total else 0,
            'price_cad': t.get('live_price_cad'), 'yield': t.get('yield_pct'),
            'mer': round(m['expense_ratio'], 3) if m.get('expense_ratio') is not None else None,
            'market_cap': m.get('market_cap'), 'aum': m.get('total_assets'),
            'is_fund': t.get('is_fund'), 'sector': t.get('sector'), 'risk_bucket': t.get('risk_bucket'),
            'beta': round(m['beta'], 2) if m.get('beta') is not None else None,
            'volatility': round(m['volatility'] * 100, 1) if m.get('volatility') is not None else None,
            'max_dd': round(_max_drawdown(closes) * 100, 1) if _max_drawdown(closes) is not None else None,
            'ret_1y': round(_ret_1y(closes) * 100, 1) if _ret_1y(closes) is not None else None,
            'range_low': m.get('range_low'), 'range_high': m.get('range_high'),
            'corr_to_book': round(corr, 2) if corr is not None else None,
            'corr_tag': _corr_tag(corr),
            'spark': _spark(closes),
            'description': m.get('description'),
            'rationale': t.get('rationale'), 'gaps_addressed': t.get('gaps_addressed', []),
            'sources': t.get('sources', []),
            'alternates': [_alt_stats(a, metas, hist) for a in t.get('alternates', [])],
        })

    return {
        'account': account, 'account_type': acct_type,
        'summary': plan.get('summary', ''),
        'thesis': plan.get('thesis'),                 # set once Phase 2 adds it to the schema
        'risks_remaining': plan.get('risks_remaining'),
        'stats': {
            'buys': round(buy_total, 2), 'sells': round(sell_total, 2),
            'net': round(buy_total - sell_total, 2),
            'leftover': round(plan.get('leftover_cash', after_cash) or after_cash, 2),
            'n_new': len(plan.get('_verified', [])),
        },
        'allocation': allocation,
        'risk': risk,
        'dividend': dividend,
        'trades': trades,
        'holdings_detail': holdings_detail,
        'cap_notes': plan.get('cap_notes', []),
        'caveats': plan.get('caveats', []),
        'provider': plan.get('_provider'), 'model': plan.get('_model'),
    }
