from models import Transaction, Account, PriceCache


def get_fx_rate():
    from price_service import get_fx_rate as _get
    return _get()


def get_holdings(include_closed=False):
    transactions = (
        Transaction.query
        .order_by(Transaction.date.asc(), Transaction.id.asc())
        .all()
    )
    fx_rate = get_fx_rate()
    positions = {}

    for t in transactions:
        key = (t.ticker, t.account)
        if key not in positions:
            positions[key] = {
                'ticker': t.ticker,
                'account': t.account,
                'currency': t.currency,
                'qty': 0.0,
                'total_cost_cad': 0.0,
                'avg_cost_cad': 0.0,
                'dividends_cad': 0.0,
                'realized_gl_cad': 0.0,
                'total_fees_cad': 0.0,
            }
        pos = positions[key]

        if t.type == 'Buy':
            # amount_cad stores the actual CAD stock cost at purchase-day FX
            # (set by the importer from Net Amount). Add fees to get total book cost.
            buy_cost = (t.amount_cad or t.qty * t.price) + (t.fees_cad or 0)
            new_qty = pos['qty'] + t.qty
            new_cost = pos['total_cost_cad'] + buy_cost
            pos['avg_cost_cad'] = new_cost / new_qty if new_qty > 0 else 0
            pos['total_cost_cad'] = new_cost
            pos['qty'] = new_qty
            pos['total_fees_cad'] += t.fees_cad or 0
            pos['currency'] = t.currency

        elif t.type == 'Sell':
            # Proceeds = the actual CAD cash recorded at sale (net_cad already reflects
            # historical FX and fees). ACB removed is capped at shares actually held, so
            # corporate-action chains (EXCH then DISP on the same shares) can't remove
            # the cost basis twice and double-count the loss.
            sell_qty = min(t.qty, pos['qty']) if pos['qty'] > 0 else 0.0
            rate = fx_rate if t.currency == 'USD' else 1.0
            proceeds = t.net_cad if t.net_cad is not None else (t.price * rate * t.qty - (t.fees_cad or 0))
            pos['realized_gl_cad'] += proceeds - pos['avg_cost_cad'] * sell_qty
            pos['qty'] = max(0.0, pos['qty'] - t.qty)
            pos['total_cost_cad'] = pos['avg_cost_cad'] * pos['qty']
            pos['total_fees_cad'] += t.fees_cad or 0

        elif t.type == 'Split':
            # TD records the number of NEW shares created (not the post-split total).
            # Both positive (new shares) and negative (CXLSPL reversal) use ADD.
            pos['qty'] = max(0.0, pos['qty'] + t.qty)
            pos['avg_cost_cad'] = pos['total_cost_cad'] / pos['qty'] if pos['qty'] > 0 else 0.0

        elif t.type == 'Dividend':
            pos['dividends_cad'] += t.amount_cad or (t.qty * t.price)

    result = []
    for key, pos in positions.items():
        # 'CASH' is a pseudo-ticker for deposits/fees/ROC, not a real position.
        if pos['ticker'] == 'CASH':
            continue
        is_closed = pos['qty'] < 0.0001
        if is_closed and not include_closed:
            continue

        ticker = pos['ticker']
        cached = PriceCache.query.get(ticker)

        live_price = cached.price if cached else None
        prev_close = cached.prev_close if cached else None
        last_updated = cached.last_updated if cached else None

        rate = fx_rate if pos['currency'] == 'USD' else 1.0

        if live_price:
            price_cad = live_price * rate
            prev_cad = (prev_close or live_price) * rate
            market_value_cad = pos['qty'] * price_cad
            book_value_cad = pos['total_cost_cad']
            unrealized_gl = market_value_cad - book_value_cad
            unrealized_gl_pct = (unrealized_gl / book_value_cad * 100) if book_value_cad else 0
            day_change = pos['qty'] * (price_cad - prev_cad)
            day_change_pct = ((price_cad - prev_cad) / prev_cad * 100) if prev_cad else 0
        else:
            price_cad = None
            market_value_cad = None
            book_value_cad = pos['total_cost_cad']
            unrealized_gl = None
            unrealized_gl_pct = None
            day_change = None
            day_change_pct = None

        result.append({
            'ticker': ticker,
            'account': pos['account'],
            'currency': pos['currency'],
            'qty': pos['qty'],
            'avg_cost_cad': pos['avg_cost_cad'],
            'live_price': live_price,
            'live_price_cad': price_cad,
            'book_value_cad': book_value_cad,
            'market_value_cad': market_value_cad,
            'unrealized_gl': unrealized_gl,
            'unrealized_gl_pct': unrealized_gl_pct,
            'day_change': day_change,
            'day_change_pct': day_change_pct,
            'dividends_cad': pos['dividends_cad'],
            'realized_gl_cad': pos['realized_gl_cad'],
            'last_updated': last_updated,
            'port_pct': 0,
            'closed': is_closed,
        })

    result.sort(key=lambda x: x['market_value_cad'] or 0, reverse=True)

    total_mv = sum(h['market_value_cad'] or 0 for h in result)
    for h in result:
        h['port_pct'] = (h['market_value_cad'] or 0) / total_mv * 100 if total_mv else 0

    return result


def get_cash_by_account():
    """Net liquid cash per account: sum of net_cad across all transactions.
    Deposits (+), buys (−), sells/dividends (+). Reflects actual uninvested cash."""
    rows = (
        Transaction.query
        .with_entities(Transaction.account, Transaction.net_cad)
        .all()
    )
    result = {}
    for acc, net in rows:
        result[acc] = result.get(acc, 0.0) + (net or 0.0)
    return result


def get_dashboard_stats(holdings):
    total_mv = sum(h['market_value_cad'] or 0 for h in holdings)
    total_book = sum(h['book_value_cad'] for h in holdings)
    total_unrealized = total_mv - total_book
    total_unrealized_pct = (total_unrealized / total_book * 100) if total_book else 0
    total_day_change = sum(h['day_change'] or 0 for h in holdings)
    total_dividends = sum(h['dividends_cad'] for h in holdings)

    cash_by_account = get_cash_by_account()
    total_cash = sum(cash_by_account.values())

    account_breakdown = {}
    for h in holdings:
        acc = h['account']
        account_breakdown[acc] = account_breakdown.get(acc, 0) + (h['market_value_cad'] or 0)

    return {
        'total_portfolio': total_mv + total_cash,
        'total_mv': total_mv,
        'total_book': total_book,
        'total_unrealized': total_unrealized,
        'total_unrealized_pct': total_unrealized_pct,
        'total_day_change': total_day_change,
        'total_dividends': total_dividends,
        'total_cash': total_cash,
        'num_holdings': len(holdings),
        'account_breakdown': account_breakdown,
    }


def get_account_summary():
    all_holdings = get_holdings(include_closed=True)
    accounts = Account.query.all()
    cash_by_account = get_cash_by_account()

    # Personal contributions vs. free government money (RDSP grants/bonds).
    # Grants and bonds aren't money the user put in, so they count toward gain.
    contrib_by_account, grants_by_account = {}, {}
    for acc, subtype, net in (Transaction.query.filter_by(type='Deposit')
                              .with_entities(Transaction.account, Transaction.subtype,
                                             Transaction.net_cad).all()):
        n = net or 0.0
        if subtype in ('RDSP Grant', 'RDSP Bond'):
            grants_by_account[acc] = grants_by_account.get(acc, 0.0) + n
        else:
            contrib_by_account[acc] = contrib_by_account.get(acc, 0.0) + n

    result = []
    for account in accounts:
        acct_all = [h for h in all_holdings if h['account'] == account.name]
        acct_holdings = [h for h in acct_all if not h.get('closed')]
        closed_holdings = [h for h in acct_all if h.get('closed')]
        holdings_mv = sum(h['market_value_cad'] or 0 for h in acct_holdings)
        holdings_book = sum(h['book_value_cad'] for h in acct_holdings)
        unrealized = holdings_mv - holdings_book
        day_change = sum(h['day_change'] or 0 for h in acct_holdings)
        # Dividends and realized G/L span the account's full history (closed too).
        dividends_total = sum(h['dividends_cad'] for h in acct_all)
        realized_total = sum(h['realized_gl_cad'] for h in acct_all)
        cash = cash_by_account.get(account.name, 0.0)

        # Hide empty accounts — only show ones holding money or positions.
        if abs(holdings_mv) < 0.005 and abs(cash) < 0.005:
            continue

        total_value = holdings_mv + cash
        net_contributions = contrib_by_account.get(account.name, 0.0)
        grants_bonds = grants_by_account.get(account.name, 0.0)

        result.append({
            'name': account.name,
            'type': account.type,
            'id': account.id,
            'holdings_mv': holdings_mv,
            'holdings_book': holdings_book,
            'cash_balance': cash,
            'total_value': total_value,
            'unrealized_gl': unrealized,
            'unrealized_gl_pct': (unrealized / holdings_book * 100) if holdings_book else 0,
            'day_change': day_change,
            'dividends_total': dividends_total,
            'realized_total': realized_total,
            'net_contributions': net_contributions,
            'grants_bonds': grants_bonds,
            'all_time_gain': total_value - net_contributions,
            'num_holdings': len(acct_holdings),
            'holdings': acct_holdings,
            'closed_holdings': closed_holdings,
        })

    return result


def _cap_bucket(mc):
    if not mc:
        return None
    if mc >= 200e9:
        return 'Mega cap'
    if mc >= 10e9:
        return 'Large cap'
    if mc >= 2e9:
        return 'Mid cap'
    return 'Small cap'


def get_account_breakdown(account_name):
    """Allocation breakdowns for one account's current holdings: by asset type,
    sector (with ETF look-through), market-cap size, currency, holdings-vs-cash,
    and position weights. Classification metadata is fetched/cached on demand."""
    from price_service import get_holdings_metadata

    holdings = [h for h in get_holdings() if h['account'] == account_name]
    cash = get_cash_by_account().get(account_name, 0.0)
    total_mv = sum(h['market_value_cad'] or 0 for h in holdings)

    if total_mv <= 0:
        return {'ok': True, 'total_mv': 0, 'cash': round(cash, 2),
                'asset_type': [], 'sector': [], 'market_cap': [],
                'currency': [], 'invested_vs_cash': [], 'positions': []}

    meta = get_holdings_metadata([h['ticker'] for h in holdings])
    asset_type, sector, market_cap, currency = {}, {}, {}, {}
    positions = []

    for h in holdings:
        mv = h['market_value_cad'] or 0
        if mv <= 0:
            continue
        m = meta.get(h['ticker'], {})
        at = m.get('asset_type') or 'Equity'
        asset_type[at] = asset_type.get(at, 0) + mv
        currency[h['currency']] = currency.get(h['currency'], 0) + mv

        # Sector — ETF look-through when available, else the equity's own sector
        if m.get('fund_sectors'):
            tot = sum(m['fund_sectors'].values()) or 1
            for sec, w in m['fund_sectors'].items():
                sector[sec] = sector.get(sec, 0) + mv * (w / tot)
        elif m.get('sector'):
            sector[m['sector']] = sector.get(m['sector'], 0) + mv
        else:
            sector['Unclassified'] = sector.get('Unclassified', 0) + mv

        # Market cap — ETFs grouped as Fund; stocks bucketed
        if at == 'ETF':
            market_cap['Fund'] = market_cap.get('Fund', 0) + mv
        else:
            b = _cap_bucket(m.get('market_cap')) or 'Unclassified'
            market_cap[b] = market_cap.get(b, 0) + mv

        positions.append({'ticker': h['ticker'], 'label': h['ticker'], 'value': round(mv, 2),
                          'pct': round(mv / total_mv * 100, 1)})

    def to_list(d):
        return sorted(
            [{'label': k, 'value': round(v, 2), 'pct': round(v / total_mv * 100, 1)}
             for k, v in d.items()],
            key=lambda x: x['value'], reverse=True)

    account_total = total_mv + cash
    invested_vs_cash = [
        {'label': 'Invested', 'value': round(total_mv, 2),
         'pct': round(total_mv / account_total * 100, 1) if account_total else 0},
        {'label': 'Cash', 'value': round(cash, 2),
         'pct': round(cash / account_total * 100, 1) if account_total else 0},
    ]
    positions.sort(key=lambda x: x['value'], reverse=True)

    return {
        'ok': True,
        'total_mv': round(total_mv, 2),
        'cash': round(cash, 2),
        'asset_type': to_list(asset_type),
        'sector': to_list(sector),
        'market_cap': to_list(market_cap),
        'currency': to_list(currency),
        'invested_vs_cash': invested_vs_cash,
        'positions': positions,
    }


# ── Cash Flows ────────────────────────────────────────────────────────────────

def get_cashflow_stats(account_filter=None, subtype_filter=None):
    from datetime import date

    today = date.today()
    ytd_start = date(today.year, 1, 1)

    query = Transaction.query.filter_by(type='Deposit')
    if account_filter:
        query = query.filter_by(account=account_filter)
    if subtype_filter:
        query = query.filter_by(subtype=subtype_filter)

    deposits = query.order_by(Transaction.date.desc()).all()

    all_time = sum(d.net_cad for d in deposits)
    ytd      = sum(d.net_cad for d in deposits if d.date >= ytd_start)

    known_subtypes = ['Contribution', 'RDSP Grant', 'RDSP Bond']
    by_subtype = {}
    for st in known_subtypes:
        rows = [d for d in deposits if d.subtype == st]
        by_subtype[st] = {
            'total': sum(d.net_cad for d in rows),
            'ytd':   sum(d.net_cad for d in rows if d.date >= ytd_start),
            'count': len(rows),
        }

    # Per-account totals broken down by subtype
    account_totals = {}
    for d in deposits:
        acc = d.account
        if acc not in account_totals:
            account_totals[acc] = {'total': 0.0, 'by_subtype': {st: 0.0 for st in known_subtypes}}
        account_totals[acc]['total'] += d.net_cad
        st = d.subtype if d.subtype in known_subtypes else 'Other'
        account_totals[acc]['by_subtype'][st] = account_totals[acc]['by_subtype'].get(st, 0.0) + d.net_cad

    by_year = {}
    for d in deposits:
        by_year[d.date.year] = by_year.get(d.date.year, 0.0) + d.net_cad

    # Annual chart stacked by subtype
    years = sorted(by_year.keys())
    chart_by_subtype = {}
    for st in known_subtypes:
        yearly = []
        for yr in years:
            yearly.append(round(sum(
                d.net_cad for d in deposits
                if d.date.year == yr and d.subtype == st
            ), 2))
        chart_by_subtype[st] = yearly

    accounts = [a.name for a in Account.query.order_by(Account.name).all()]

    return {
        'deposits': deposits,
        'all_time': all_time,
        'ytd': ytd,
        'count': len(deposits),
        'by_subtype': by_subtype,
        'by_year': dict(sorted(by_year.items())),
        'account_totals': dict(sorted(account_totals.items())),
        'known_subtypes': known_subtypes,
        'chart_years': years,
        'chart_by_subtype': chart_by_subtype,
        'accounts': accounts,
        'active_account': account_filter or '',
        'active_subtype': subtype_filter or '',
    }


# ── Dividends ─────────────────────────────────────────────────────────────────

def get_dividend_stats(scope='portfolio'):
    from datetime import date
    from datetime import datetime as dt
    from price_service import get_holdings_metadata

    dq = Transaction.query.filter_by(type='Dividend')
    wq = Transaction.query.filter_by(type='WithholdingTax')
    if scope and scope != 'portfolio':
        dq = dq.filter_by(account=scope)
        wq = wq.filter_by(account=scope)
    dividends = dq.order_by(Transaction.date.asc()).all()
    withholdings = wq.all()

    today = date.today()
    ytd_start = date(today.year, 1, 1)
    ttm_start = date(today.year - 1, today.month, today.day)

    def period_sums(rows):
        return (sum(r.amount_cad for r in rows),
                sum(r.amount_cad for r in rows if r.date >= ytd_start),
                sum(r.amount_cad for r in rows if r.date >= ttm_start))

    g_all, g_ytd, g_ttm = period_sums(dividends)      # gross
    w_all, w_ytd, w_ttm = period_sums(withholdings)   # withholding tax (amount_cad positive)
    n_all, n_ytd, n_ttm = g_all - w_all, g_ytd - w_ytd, g_ttm - w_ttm  # net received

    # Per-ticker gross / net / withheld / TTM(net) / count / last payment
    by_ticker = {}

    def _row(tk):
        return by_ticker.setdefault(tk, {'ticker': tk, 'gross': 0.0, 'withheld': 0.0,
                                         'ttm': 0.0, 'count': 0, 'last_date': None})
    for d in dividends:
        r = _row(d.ticker)
        r['gross'] += d.amount_cad
        if d.date >= ttm_start:
            r['ttm'] += d.amount_cad
        r['count'] += 1
        if not r['last_date'] or d.date > r['last_date']:
            r['last_date'] = d.date
    for w in withholdings:
        r = _row(w.ticker)
        r['withheld'] += w.amount_cad
        if w.date >= ttm_start:
            r['ttm'] -= w.amount_cad

    # Current holdings → book, market value, and forward income (current yields)
    holdings = [h for h in get_holdings() if scope == 'portfolio' or h['account'] == scope]
    book_by, mv_by, fwd_by = {}, {}, {}
    for h in holdings:
        book_by[h['ticker']] = book_by.get(h['ticker'], 0) + h['book_value_cad']
        mv_by[h['ticker']] = mv_by.get(h['ticker'], 0) + (h['market_value_cad'] or 0)

    fx = get_fx_rate()
    meta = get_holdings_metadata([h['ticker'] for h in holdings])
    for h in holdings:
        m = meta.get(h['ticker'], {})
        rate = m.get('dividend_rate')
        if not rate and m.get('dividend_yield') and h['live_price']:
            rate = m['dividend_yield'] / 100.0 * h['live_price']
        if rate:
            inc = rate * h['qty'] * (fx if h['currency'] == 'USD' else 1.0)
            fwd_by[h['ticker']] = fwd_by.get(h['ticker'], 0) + inc

    for r in by_ticker.values():
        r['net'] = r['gross'] - r['withheld']
        book = book_by.get(r['ticker'], 0)
        r['yield_on_cost'] = (r['ttm'] / book * 100) if book and r['ttm'] else None
        mv, fwd = mv_by.get(r['ticker'], 0), fwd_by.get(r['ticker'], 0)
        r['fwd_income'] = fwd
        r['current_yield'] = (fwd / mv * 100) if mv and fwd else None
    # include current holdings that pay but have no recorded dividends yet
    for tk, fwd in fwd_by.items():
        if tk not in by_ticker and fwd:
            mv = mv_by.get(tk, 0)
            by_ticker[tk] = {'ticker': tk, 'gross': 0.0, 'withheld': 0.0, 'net': 0.0, 'ttm': 0.0,
                             'count': 0, 'last_date': None, 'yield_on_cost': None,
                             'fwd_income': fwd, 'current_yield': (fwd / mv * 100) if mv else None}

    ticker_list = sorted(by_ticker.values(), key=lambda x: (x['net'], x['fwd_income']), reverse=True)

    fwd_total = sum(fwd_by.values())
    total_mv = sum(mv_by.values())

    # Net by year, and net monthly across all history (frontend range-selects)
    by_year, div_m, wh_m = {}, {}, {}
    for d in dividends:
        by_year[d.date.year] = by_year.get(d.date.year, 0) + d.amount_cad
        div_m[d.date.strftime('%Y-%m')] = div_m.get(d.date.strftime('%Y-%m'), 0) + d.amount_cad
    for w in withholdings:
        by_year[w.date.year] = by_year.get(w.date.year, 0) - w.amount_cad
        wh_m[w.date.strftime('%Y-%m')] = wh_m.get(w.date.strftime('%Y-%m'), 0) + w.amount_cad

    months = sorted(set(div_m) | set(wh_m))
    month_chart = [{'label': dt.strptime(mk, '%Y-%m').strftime('%b %Y'),
                    'value': round(div_m.get(mk, 0) - wh_m.get(mk, 0), 2)} for mk in months]

    return {
        'scope': scope,
        'all_time': n_all, 'ytd': n_ytd, 'ttm': n_ttm,
        'gross_all': g_all, 'withheld_all': w_all,
        'ttm_monthly_avg': n_ttm / 12 if n_ttm else 0,
        'forward_income': fwd_total,
        'forward_yield': (fwd_total / total_mv * 100) if total_mv else 0,
        'by_ticker': ticker_list,
        'by_year': dict(sorted(by_year.items())),
        'month_chart': month_chart,
        'count': len(dividends),
    }


# ── GICs ──────────────────────────────────────────────────────────────────────

def get_gic_stats():
    from models import GIC
    from datetime import date

    today = date.today()
    gics = GIC.query.order_by(GIC.maturity_date.asc()).all()

    result = []
    total_principal = 0.0
    total_interest = 0.0
    total_at_maturity = 0.0

    comp_periods = {'Monthly': 12, 'Quarterly': 4, 'Semi-Annual': 2, 'Annual': 1}

    for g in gics:
        if not g.start_date or not g.maturity_date:
            continue

        days_total = max(1, (g.maturity_date - g.start_date).days)
        days_elapsed = max(0, min(days_total, (today - g.start_date).days))
        days_remaining = max(0, (g.maturity_date - today).days)
        pct_elapsed = days_elapsed / days_total * 100

        rate = (g.rate or 0) / 100
        years_total = days_total / 365
        years_elapsed = days_elapsed / 365

        n = comp_periods.get(g.compounding, 0)
        if n > 0:
            value_at_maturity = g.principal * (1 + rate / n) ** (n * years_total)
            current_value = g.principal * (1 + rate / n) ** (n * years_elapsed)
        else:  # Simple interest
            value_at_maturity = g.principal * (1 + rate * years_total)
            current_value = g.principal * (1 + rate * years_elapsed)

        interest_accrued = current_value - g.principal
        interest_at_maturity = value_at_maturity - g.principal

        total_principal += g.principal
        total_interest += interest_accrued
        total_at_maturity += value_at_maturity

        result.append({
            'id': g.id,
            'name': g.name or '',
            'institution': g.institution or '',
            'account': g.account or '',
            'principal': g.principal,
            'rate': g.rate or 0,
            'compounding': g.compounding,
            'start_date': g.start_date,
            'maturity_date': g.maturity_date,
            'days_remaining': days_remaining,
            'pct_elapsed': pct_elapsed,
            'current_value': current_value,
            'interest_accrued': interest_accrued,
            'interest_at_maturity': interest_at_maturity,
            'value_at_maturity': value_at_maturity,
            'is_matured': today >= g.maturity_date,
        })

    return {
        'gics': result,
        'total_principal': total_principal,
        'total_interest': total_interest,
        'total_at_maturity': total_at_maturity,
        'accounts': [a.name for a in Account.query.order_by(Account.name).all()],
    }


# ── Tax & ACB ─────────────────────────────────────────────────────────────────

# Registered (tax-sheltered) account types — capital gains here are not taxable.
REGISTERED_TYPES = {'TFSA', 'RRSP', 'FHSA', 'RDSP', 'RESP', 'LIRA', 'LRSP', 'RRIF'}


def get_tax_summary(year=None):
    from datetime import date

    if year is None:
        year = date.today().year

    all_txns = Transaction.query.order_by(Transaction.date.asc(), Transaction.id.asc()).all()
    fx_rate = get_fx_rate()

    # Map account name -> registered? Gains in registered accounts are tax-sheltered.
    acct_type = {a.name: (a.type or 'Non-Reg') for a in Account.query.all()}

    def is_registered(account_name):
        return acct_type.get(account_name, 'Non-Reg').strip().upper() in REGISTERED_TYPES

    positions_running = {}
    tax_rows = []

    for t in all_txns:
        key = (t.ticker, t.account)
        if key not in positions_running:
            positions_running[key] = {'qty': 0.0, 'total_cost_cad': 0.0, 'avg_cost_cad': 0.0}
        pos = positions_running[key]

        if t.type == 'Buy':
            # Use recorded CAD cost (historical FX), consistent with get_holdings.
            buy_cost = (t.amount_cad or t.qty * t.price) + (t.fees_cad or 0)
            new_qty = pos['qty'] + t.qty
            new_cost = pos['total_cost_cad'] + buy_cost
            pos['avg_cost_cad'] = new_cost / new_qty if new_qty > 0 else 0
            pos['total_cost_cad'] = new_cost
            pos['qty'] = new_qty

        elif t.type == 'Split':
            pos['qty'] = max(0.0, pos['qty'] + t.qty)
            pos['avg_cost_cad'] = pos['total_cost_cad'] / pos['qty'] if pos['qty'] > 0 else 0.0

        elif t.type == 'Sell':
            # Proceeds = actual recorded cash; ACB capped at shares actually held so
            # corporate-action chains don't remove the same cost basis twice.
            sell_qty = min(t.qty, pos['qty']) if pos['qty'] > 0 else 0.0
            rate = fx_rate if t.currency == 'USD' else 1.0
            proceeds_cad = t.net_cad if t.net_cad is not None else (t.qty * t.price * rate - (t.fees_cad or 0))
            acb = pos['avg_cost_cad'] * sell_qty
            gl = proceeds_cad - acb

            if t.date.year == year:
                tax_rows.append({
                    'date': t.date,
                    'ticker': t.ticker,
                    'account': t.account,
                    'registered': is_registered(t.account),
                    'qty': t.qty,
                    'proceeds_cad': proceeds_cad,
                    'acb': acb,
                    'gl': gl,
                })

            pos['qty'] = max(0.0, pos['qty'] - t.qty)
            pos['total_cost_cad'] = pos['avg_cost_cad'] * pos['qty']

    # Realized G/L across all accounts (performance tracking)
    total_realized = sum(r['gl'] for r in tax_rows)

    # Taxable figures come from non-registered accounts only
    taxable_rows = [r for r in tax_rows if not r['registered']]
    total_gl = sum(r['gl'] for r in taxable_rows)
    total_gains = sum(r['gl'] for r in taxable_rows if r['gl'] > 0)
    total_losses = sum(r['gl'] for r in taxable_rows if r['gl'] < 0)
    taxable_gain = max(0, total_gl) * 0.5  # 50% inclusion rate

    registered_realized = total_realized - total_gl
    registered_names = sorted({r['account'] for r in tax_rows if r['registered']})

    sell_years = sorted(set(
        t.date.year for t in all_txns if t.type == 'Sell'
    ), reverse=True)

    return {
        'year': year,
        'rows': sorted(tax_rows, key=lambda r: r['date']),
        'total_realized': total_realized,
        'total_gl': total_gl,
        'total_gains': total_gains,
        'total_losses': total_losses,
        'taxable_gain': taxable_gain,
        'registered_realized': registered_realized,
        'registered_names': registered_names,
        'has_taxable': len(taxable_rows) > 0,
        'available_years': sell_years,
    }


# ── Rebalancer ────────────────────────────────────────────────────────────────

def get_rebalancer_data():
    from models import Setting

    holdings = get_holdings()
    accounts_db = Account.query.order_by(Account.name).all()

    def gs(key, default=0.0):
        s = Setting.query.get(key)
        try:
            return float(s.value) if s else float(default)
        except Exception:
            return float(default)

    total_mv = sum(h['market_value_cad'] or 0 for h in holdings)
    cash_by_account = get_cash_by_account()
    total_cash = sum(cash_by_account.values())
    total_portfolio = total_mv + total_cash

    account_current = {}
    for h in holdings:
        acc = h['account']
        account_current[acc] = account_current.get(acc, 0) + (h['market_value_cad'] or 0)
    for a in accounts_db:
        account_current[a.name] = account_current.get(a.name, 0) + cash_by_account.get(a.name, 0.0)

    rows = []
    for a in accounts_db:
        current_val = account_current.get(a.name, 0)
        key = 'target_alloc_' + a.name.lower().replace(' ', '_').replace('-', '_')
        target_pct = gs(key, 0)
        target_val = total_portfolio * target_pct / 100
        current_pct = (current_val / total_portfolio * 100) if total_portfolio else 0
        drift = current_val - target_val
        drift_pct = current_pct - target_pct

        rows.append({
            'account': a.name,
            'current_value': current_val,
            'current_pct': current_pct,
            'target_pct': target_pct,
            'target_value': target_val,
            'drift': drift,
            'drift_pct': drift_pct,
        })

    chart_current = [round(r['current_pct'], 2) for r in rows]
    chart_target = [round(r['target_pct'], 2) for r in rows]
    chart_labels = [r['account'] for r in rows]
    targets_set = any(r['target_pct'] > 0 for r in rows)

    return {
        'rows': rows,
        'total_portfolio': total_portfolio,
        'chart_labels': chart_labels,
        'chart_current': chart_current,
        'chart_target': chart_target,
        'targets_set': targets_set,
    }


# ── Performance History ───────────────────────────────────────────────────────

def _holdings_acb(txns, fx_rate):
    """ACB computation over a filtered transaction list (mirrors get_holdings core logic)."""
    positions = {}
    for t in txns:
        if t.type not in ('Buy', 'Sell', 'Split'):
            continue
        key = (t.ticker, t.account)
        if key not in positions:
            positions[key] = {
                'ticker': t.ticker, 'currency': t.currency,
                'qty': 0.0, 'total_cost_cad': 0.0, 'avg_cost_cad': 0.0,
            }
        pos = positions[key]
        if t.type == 'Buy':
            cost = (t.amount_cad or t.qty * t.price) + (t.fees_cad or 0)
            new_qty = pos['qty'] + t.qty
            new_cost = pos['total_cost_cad'] + cost
            pos['avg_cost_cad'] = new_cost / new_qty if new_qty else 0
            pos['total_cost_cad'] = new_cost
            pos['qty'] = new_qty
            pos['currency'] = t.currency
        elif t.type == 'Sell':
            pos['qty'] = max(pos['qty'] - t.qty, 0)
            pos['total_cost_cad'] = pos['avg_cost_cad'] * pos['qty']
        elif t.type == 'Split':
            pos['qty'] = max(0.0, pos['qty'] + t.qty)
            pos['avg_cost_cad'] = pos['total_cost_cad'] / pos['qty'] if pos['qty'] > 0 else 0.0
    return [
        {'ticker': pos['ticker'], 'currency': pos['currency'],
         'qty': pos['qty'], 'book_value_cad': pos['total_cost_cad']}
        for pos in positions.values() if pos['qty'] > 0.0001
    ]


def backfill_performance_history():
    """
    Generates one PortfolioSnapshot per past month-end using transaction history
    and historical prices fetched from yfinance. Skips months already snapshotted.
    Returns the number of new snapshots created.
    """
    from models import PortfolioSnapshot, db
    import yfinance as yf
    import pandas as pd
    from datetime import date, timedelta

    transactions = (Transaction.query
                    .order_by(Transaction.date.asc(), Transaction.id.asc()).all())
    buy_sell = [t for t in transactions if t.type in ('Buy', 'Sell', 'Split')]
    if not buy_sell:
        return 0

    first_date = buy_sell[0].date
    today = date.today()

    # Build list of month-ends from the first transaction month up to last complete month
    months = []
    y, m = first_date.year, first_date.month
    while (y, m) < (today.year, today.month):
        if m == 12:
            months.append(date(y + 1, 1, 1) - timedelta(days=1))
            y += 1
            m = 1
        else:
            months.append(date(y, m + 1, 1) - timedelta(days=1))
            m += 1

    if not months:
        return 0

    existing_dates = {s.date for s in PortfolioSnapshot.query.all()}
    months_to_fill = [d for d in months if d not in existing_dates]
    if not months_to_fill:
        return 0

    # Tickers to price — skip unmapped descriptions (contain spaces) and CASH
    tickers = sorted({
        t.ticker for t in buy_sell
        if ' ' not in t.ticker and t.ticker != 'CASH'
    })
    if not tickers:
        return 0

    all_symbols = tickers + ['USDCAD=X']
    start_str = first_date.strftime('%Y-%m-%d')
    end_str = (today + timedelta(days=1)).strftime('%Y-%m-%d')

    raw = yf.download(all_symbols, start=start_str, end=end_str,
                      auto_adjust=True, progress=False)
    if raw.empty:
        return 0

    close_df = raw['Close'] if isinstance(raw.columns, pd.MultiIndex) else raw[['Close']].rename(columns={'Close': all_symbols[0]})
    close_df.index = pd.to_datetime(close_df.index).normalize()

    def price_on(ticker, as_of):
        if ticker not in close_df.columns:
            return None
        col = close_df[ticker].dropna()
        before = col[col.index <= pd.Timestamp(as_of)]
        return float(before.iloc[-1]) if not before.empty else None

    count = 0
    for month_end in months_to_fill:
        txns_to_date = [t for t in transactions if t.date <= month_end]
        fx = price_on('USDCAD=X', month_end) or 1.365
        holdings = _holdings_acb(txns_to_date, fx)

        total_book = sum(h['book_value_cad'] for h in holdings)
        total_mv = 0.0
        for h in holdings:
            p = price_on(h['ticker'], month_end)
            if p:
                rate = fx if h['currency'] == 'USD' else 1.0
                total_mv += h['qty'] * p * rate

        db.session.add(PortfolioSnapshot(
            date=month_end,
            total_book=round(total_book, 2),
            total_market=round(total_mv, 2),
            total_cash=0,
        ))
        count += 1

    db.session.commit()
    return count


def take_portfolio_snapshot():
    from models import PortfolioSnapshot, db
    from datetime import date

    today = date.today()
    if PortfolioSnapshot.query.filter_by(date=today).first():
        return False

    holdings = get_holdings()
    total_mv = sum(h['market_value_cad'] or 0 for h in holdings)
    total_book = sum(h['book_value_cad'] for h in holdings)
    total_cash = sum(get_cash_by_account().values())

    db.session.add(PortfolioSnapshot(
        date=today,
        total_book=total_book,
        total_market=total_mv,
        total_cash=total_cash,
    ))
    db.session.commit()
    return True


def get_performance_data():
    from models import PortfolioSnapshot
    import math

    snaps = PortfolioSnapshot.query.order_by(PortfolioSnapshot.date.asc()).all()
    if not snaps:
        return {
            'snapshots': [], 'labels': [], 'values': [], 'book_values': [],
            'cagr': None, 'total_return': None, 'monthly_returns': [],
        }

    labels = [s.date.strftime('%b %Y') for s in snaps]
    values = [round((s.total_market or 0) + (s.total_cash or 0), 2) for s in snaps]
    book_values = [round(s.total_book or 0, 2) for s in snaps]

    first_val = values[0] if values[0] > 0 else 1
    last_val = values[-1]
    total_return = (last_val - first_val) / first_val * 100

    years = (snaps[-1].date - snaps[0].date).days / 365.25
    cagr = (math.pow(last_val / first_val, 1 / years) - 1) * 100 if years > 0.08 and first_val > 0 else None

    monthly_returns = []
    for i in range(1, len(values)):
        prev = values[i - 1] if values[i - 1] > 0 else 1
        monthly_returns.append({
            'label': labels[i],
            'value': values[i],
            'return_pct': (values[i] - prev) / prev * 100,
        })

    return {
        'snapshots': snaps,
        'labels': labels,
        'values': values,
        'book_values': book_values,
        'cagr': cagr,
        'total_return': total_return,
        'monthly_returns': list(reversed(monthly_returns[-12:])),
        'first_date': snaps[0].date,
        'last_value': last_val,
    }


# ── Projections ───────────────────────────────────────────────────────────────

def get_projections(current_value, monthly_contrib, years):
    scenarios = [
        ('Bear (4%)', 0.04, '#ff4d4d'),
        ('Base (7%)', 0.07, '#00c8f0'),
        ('Bull (10%)', 0.10, '#00e676'),
    ]

    months = years * 12
    result = []
    for label, annual_rate, color in scenarios:
        monthly_rate = annual_rate / 12
        data = []
        value = float(current_value)
        for _ in range(months + 1):
            data.append(round(value, 2))
            value = value * (1 + monthly_rate) + monthly_contrib
        result.append({'label': label, 'color': color, 'data': data, 'final': data[-1]})

    tick_labels = [f'Year {m // 12}' if m % 12 == 0 else '' for m in range(months + 1)]

    return {'scenarios': result, 'labels': tick_labels, 'months': months}


# ── Monte Carlo ───────────────────────────────────────────────────────────────

def run_monte_carlo(current_value, monthly_contrib, years, mean_annual, std_annual, n_sims=500):
    import random
    import math

    months = years * 12
    mean_monthly = mean_annual / 12
    std_monthly = std_annual / math.sqrt(12)

    sim_paths = []
    for _ in range(n_sims):
        value = float(current_value)
        path = [value]
        for _ in range(months):
            r = random.gauss(mean_monthly, std_monthly)
            value = max(0.0, value * (1 + r) + monthly_contrib)
            path.append(round(value, 2))
        sim_paths.append(path)

    p10, p50, p90 = [], [], []
    for step in range(months + 1):
        vals = sorted(p[step] for p in sim_paths)
        p10.append(vals[int(n_sims * 0.10)])
        p50.append(vals[int(n_sims * 0.50)])
        p90.append(vals[int(n_sims * 0.90)])

    finals = sorted(p[-1] for p in sim_paths)
    tick_labels = [f'Year {m // 12}' if m % 12 == 0 else '' for m in range(months + 1)]

    return {
        'p10': p10,
        'p50': p50,
        'p90': p90,
        'final_p10': round(finals[int(n_sims * 0.10)], 2),
        'final_p50': round(finals[int(n_sims * 0.50)], 2),
        'final_p90': round(finals[int(n_sims * 0.90)], 2),
        'labels': tick_labels,
        'n_sims': n_sims,
        'months': months,
    }


# ── Performance series (live, per-scope, with benchmarks) ─────────────────────

# name -> (yfinance symbol, currency)
_PERF_BENCHMARKS = {
    'S&P 500': ('^GSPC', 'USD'),
    'NASDAQ': ('^IXIC', 'USD'),
    'TSX': ('^GSPTSE', 'CAD'),
}
_perf_series_cache = {}


def get_performance_series(scope='portfolio'):
    """
    Monthly value series for the whole portfolio or a single account: market
    value (holdings at month-end historical prices), book value (ACB), and cash
    — plus money-weighted benchmark series (CAD) that invest the same external
    contributions into each index. Computed live and cached by transaction set.
    """
    import yfinance as yf
    import pandas as pd
    from datetime import date, timedelta

    q = Transaction.query
    if scope and scope != 'portfolio':
        q = q.filter_by(account=scope)
    txns = q.order_by(Transaction.date.asc(), Transaction.id.asc()).all()

    if not txns:
        return {'ok': True, 'scope': scope, 'labels': [], 'dates': [],
                'market_value': [], 'book_value': [], 'cash': [], 'benchmarks': {}}

    sig = (scope, len(txns), max(t.id for t in txns))

    def _with_live(hist):
        # The latest point should reflect live prices (matching the rest of the
        # app), not the last historical close. Applied fresh each call (uncached).
        out = dict(hist)
        out['market_value'] = list(hist['market_value'])
        try:
            live = [h for h in get_holdings() if scope == 'portfolio' or h['account'] == scope]
            live_mv = sum(h['market_value_cad'] or 0 for h in live)
            if out['market_value'] and live_mv:
                out['market_value'][-1] = round(live_mv, 2)
        except Exception:
            pass
        return out

    cached = _perf_series_cache.get(scope)
    if cached and cached[0] == sig:
        return _with_live(cached[1])

    first = min(t.date for t in txns)
    today = date.today()

    months = []
    y, m = first.year, first.month
    while (y, m) <= (today.year, today.month):
        last_day = date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)
        months.append(min(last_day, today))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    ccy = {t.ticker: t.currency for t in txns
           if t.type in ('Buy', 'Sell', 'Split') and t.ticker != 'CASH' and ' ' not in t.ticker}
    tickers = sorted(ccy.keys())

    symbols = tickers + [s for s, _ in _PERF_BENCHMARKS.values()] + ['USDCAD=X']
    prices, ok = {}, True
    try:
        # auto_adjust=False → split-adjusted but NOT dividend-adjusted prices.
        # Dividends are already tracked as cash, so this avoids double-counting them.
        raw = yf.download(symbols, start=first.strftime('%Y-%m-%d'),
                          end=(today + timedelta(days=1)).strftime('%Y-%m-%d'),
                          auto_adjust=False, progress=False)
        close = raw['Close'] if isinstance(raw.columns, pd.MultiIndex) else raw[['Close']].rename(columns={'Close': symbols[0]})
        close.index = pd.to_datetime(close.index).normalize()
        for s in symbols:
            if s in close.columns:
                col = close[s].dropna()
                if not col.empty:
                    prices[s] = col
    except Exception:
        ok = False

    def price_on(sym, as_of):
        col = prices.get(sym)
        if col is None:
            return None
        before = col[col.index <= pd.Timestamp(as_of)]
        return float(before.iloc[-1]) if not before.empty else None

    # Per-ticker split events (date, ratio) from the SPLIT transactions. yfinance
    # prices are split-adjusted (today's terms), so to value a month *before* a
    # split we scale actual shares up by the split ratios that occur after it —
    # otherwise the share-count change creates a spurious jump at the split date.
    split_events, _running = {}, {}
    for t in txns:
        tk = t.ticker
        if tk == 'CASH' or ' ' in tk:
            continue
        q = _running.get(tk, 0.0)
        if t.type == 'Buy':
            _running[tk] = q + t.qty
        elif t.type == 'Sell':
            _running[tk] = max(0.0, q - t.qty)
        elif t.type == 'Split':
            newq = max(0.0, q + t.qty)
            if q > 0.0001 and newq > 0.0001:
                split_events.setdefault(tk, []).append((t.date, newq / q))
            _running[tk] = newq

    def future_split_factor(tk, me):
        factor = 1.0
        for d, r in split_events.get(tk, []):
            if d > me:
                factor *= r
        return factor

    market_value, book_value, cash_series = [], [], []
    for me in months:
        cash, pos = 0.0, {}
        for t in txns:
            if t.date > me:
                break
            cash += t.net_cad or 0.0
            if t.ticker == 'CASH' or ' ' in t.ticker:
                continue
            p = pos.setdefault(t.ticker, {'qty': 0.0, 'cost': 0.0, 'avg': 0.0})
            if t.type == 'Buy':
                p['cost'] += (t.amount_cad or t.qty * t.price) + (t.fees_cad or 0)
                p['qty'] += t.qty
                p['avg'] = p['cost'] / p['qty'] if p['qty'] else 0
            elif t.type == 'Sell':
                p['qty'] = max(0.0, p['qty'] - t.qty)
                p['cost'] = p['avg'] * p['qty']
            elif t.type == 'Split':
                p['qty'] = max(0.0, p['qty'] + t.qty)
                p['avg'] = p['cost'] / p['qty'] if p['qty'] else 0
        fx = price_on('USDCAD=X', me) or 1.365
        mv, bk = 0.0, 0.0
        for tk, p in pos.items():
            if p['qty'] <= 0.0001:
                continue
            bk += p['cost']
            pr = price_on(tk, me)
            if pr:
                shares = p['qty'] * future_split_factor(tk, me)
                mv += shares * pr * (fx if ccy.get(tk) == 'USD' else 1.0)
        market_value.append(round(mv, 2))
        book_value.append(round(bk, 2))
        cash_series.append(round(cash, 2))

    # Benchmarks — money-weighted in CAD: each month's deposits buy index units.
    ym_to_me = {(me.year, me.month): me for me in months}
    flows = {me: 0.0 for me in months}
    for t in txns:
        if t.type == 'Deposit':
            me = ym_to_me.get((t.date.year, t.date.month))
            if me is not None:
                flows[me] += t.net_cad or 0.0

    benchmarks = {}
    for name, (sym, cur) in _PERF_BENCHMARKS.items():
        if sym not in prices:
            continue
        units, series = 0.0, []
        for me in months:
            idx = price_on(sym, me)
            fx = price_on('USDCAD=X', me) or 1.365
            cad_price = (idx * fx if cur == 'USD' else idx) if idx else None
            if cad_price and flows[me]:
                units += flows[me] / cad_price
            series.append(round(units * cad_price, 2) if cad_price else None)
        benchmarks[name] = series

    result = {
        'ok': ok,
        'scope': scope,
        'labels': [me.strftime('%b %Y') for me in months],
        'dates': [me.strftime('%Y-%m-%d') for me in months],
        'market_value': market_value,
        'book_value': book_value,
        'cash': cash_series,
        'flows': [round(flows[me], 2) for me in months],
        'benchmarks': benchmarks,
    }
    _perf_series_cache[scope] = (sig, result)
    return _with_live(result)
