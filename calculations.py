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

    # The three real deposit subtypes; anything else falls into an "Other" bucket
    # that only appears when such deposits exist (so totals always reconcile).
    KNOWN = ['Contribution', 'RDSP Grant', 'RDSP Bond']

    def norm_subtype(d):
        return d.subtype if d.subtype in KNOWN else 'Other'

    has_other = any(norm_subtype(d) == 'Other' for d in deposits)
    known_subtypes = KNOWN + (['Other'] if has_other else [])

    by_subtype = {}
    for st in known_subtypes:
        rows = [d for d in deposits if norm_subtype(d) == st]
        by_subtype[st] = {
            'total': sum(d.net_cad for d in rows),
            'ytd':   sum(d.net_cad for d in rows if d.date >= ytd_start),
            'count': len(rows),
        }

    # Per-account totals broken down by subtype
    account_totals = {}
    for d in deposits:
        rec = account_totals.setdefault(
            d.account, {'total': 0.0, 'by_subtype': {st: 0.0 for st in known_subtypes}})
        rec['total'] += d.net_cad
        rec['by_subtype'][norm_subtype(d)] += d.net_cad

    by_year = {}
    for d in deposits:
        by_year[d.date.year] = by_year.get(d.date.year, 0.0) + d.net_cad

    # Annual chart stacked by subtype
    years = sorted(by_year.keys())
    chart_by_subtype = {}
    for st in known_subtypes:
        chart_by_subtype[st] = [
            round(sum(d.net_cad for d in deposits
                      if d.date.year == yr and norm_subtype(d) == st), 2)
            for yr in years
        ]

    # Composition pie: free government money (grant + bond) vs self-contribution
    free_money = by_subtype['RDSP Grant']['total'] + by_subtype['RDSP Bond']['total']
    free_pct   = round(100 * free_money / all_time, 1) if all_time else 0.0
    pie_values = [round(by_subtype[st]['total'], 2) for st in known_subtypes]

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
        'filter_subtypes': KNOWN,
        'free_money': free_money,
        'free_pct': free_pct,
        'pie_values': pie_values,
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

def get_gic_stats(account_filter=None, show_matured=False):
    from models import GIC
    from datetime import date

    today = date.today()
    gics = GIC.query.order_by(GIC.maturity_date.asc()).all()

    # Accounts that actually hold a GIC — drives the chip filter row.
    gic_accounts = sorted({g.account for g in gics if g.account})

    comp_periods = {'Monthly': 12, 'Quarterly': 4, 'Semi-Annual': 2, 'Annual': 1}

    rows = []
    for g in gics:
        if not g.start_date or not g.maturity_date:
            continue
        if account_filter and g.account != account_filter:
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

        rows.append({
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
            'interest_accrued': current_value - g.principal,
            'interest_at_maturity': value_at_maturity - g.principal,
            'value_at_maturity': value_at_maturity,
            'is_matured': today >= g.maturity_date,
        })

    # rows is maturity-ascending, so these preserve that order.
    active_rows = [r for r in rows if not r['is_matured']]
    matured_rows = [r for r in rows if r['is_matured']]

    # Stat totals reflect ACTIVE GICs only — matured principal has been paid out.
    total_principal = sum(r['principal'] for r in active_rows)
    total_interest = sum(r['interest_accrued'] for r in active_rows)
    total_at_maturity = sum(r['value_at_maturity'] for r in active_rows)
    wavg_rate = (sum(r['rate'] * r['principal'] for r in active_rows) / total_principal
                 if total_principal else 0.0)

    next_gic = active_rows[0] if active_rows else None  # soonest maturity
    next_maturity_date = next_gic['maturity_date'] if next_gic else None
    next_maturity_days = next_gic['days_remaining'] if next_gic else None

    display = (active_rows + matured_rows) if show_matured else active_rows

    return {
        'gics': display,
        'total_principal': total_principal,
        'total_interest': total_interest,
        'total_at_maturity': total_at_maturity,
        'wavg_rate': wavg_rate,
        'next_maturity_date': next_maturity_date,
        'next_maturity_days': next_maturity_days,
        'active_count': len(active_rows),
        'matured_count': len(matured_rows),
        'gic_accounts': gic_accounts,
        'active_account': account_filter or '',
        'show_matured': show_matured,
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
#
# Per-account rebalancing: pick one account, set target % by a classification
# dimension (sector / asset type / market cap / currency), and get concrete
# buy/sell recommendations that prioritise the account's available cash.
# ETFs are decomposed via fractional look-through, so trading one spills across
# several buckets — the engine works in fractional weights and shows the result.

REBAL_DIMENSIONS = ['sector', 'asset_type', 'market_cap', 'currency', 'beta', 'blend']

REBAL_DIM_LABELS = {
    'sector': 'Sector', 'asset_type': 'Asset Type',
    'market_cap': 'Market Cap', 'currency': 'Currency',
    'beta': 'Beta', 'blend': 'Blended Risk',
}

# Dimensions whose buckets have a natural low→high order (shown in that order
# rather than by size).
ORDINAL_DIMENSIONS = {'market_cap', 'beta', 'blend'}

# A targeted bucket is flagged as needing a focused new position when, after the
# recommended trades, it's still under target by REBAL_GAP_PP percentage points
# (big absolute gap) OR filled to less than REBAL_FILL_RATIO of its target (a
# small target can be proportionally far short with only a few points of gap).
REBAL_GAP_PP = 3.0
REBAL_FILL_RATIO = 0.8

_BETA_BUCKETS = ['Low', 'Medium', 'High']
_BLEND_BUCKETS = ['Very Low', 'Low', 'Moderate', 'High', 'Very High']


def _beta_bucket(beta, asset_type):
    """Volatility tier from market beta; falls back to asset type when beta is
    unavailable (bonds/cash safest, ETFs medium, individual equities highest)."""
    if beta is not None:
        if beta < 0.8:
            return 'Low'
        if beta <= 1.2:
            return 'Medium'
        return 'High'
    at = asset_type or 'Equity'
    if at in ('Bond', 'Cash', 'Mutualfund'):
        return 'Low'
    if at == 'ETF':
        return 'Medium'
    return 'High'


def _blend_bucket(beta, market_cap, asset_type, fund_sectors=None):
    """Blended risk/uncertainty (0=Very Low … 4=Very High) combining volatility
    (beta) with company size/maturity (market cap). A big, established name stays
    moderate even if volatile; a small, unknown name rates highest. Funds are
    discounted by how diversified they are (sector concentration)."""
    at = asset_type or 'Equity'

    # Volatility component (0..4)
    if beta is None:
        b = {'Bond': 0, 'Cash': 0, 'Mutualfund': 1, 'ETF': 1}.get(at, 2)
    elif beta < 0.7:
        b = 0
    elif beta < 1.0:
        b = 1
    elif beta < 1.3:
        b = 2
    elif beta < 1.7:
        b = 3
    else:
        b = 4

    # Size / idiosyncratic-uncertainty component (0=mega/diversified … 4=micro/unknown).
    if at in ('Bond', 'Cash'):
        s = 0
    elif at in ('ETF', 'Mutualfund'):
        # More diversified (lower sector concentration) → lower uncertainty.
        fs = fund_sectors or {}
        if fs:
            tot = sum(fs.values()) or 1
            hhi = sum((v / tot) ** 2 for v in fs.values())  # 1/N (broad) … 1 (one sector)
            s = 0 if hhi < 0.25 else (1 if hhi < 0.5 else 2)
        else:
            s = 1
    elif market_cap is None:
        s = 4
    elif market_cap >= 200e9:
        s = 0
    elif market_cap >= 10e9:
        s = 1
    elif market_cap >= 2e9:
        s = 2
    elif market_cap >= 300e6:
        s = 3
    else:
        s = 4

    return _BLEND_BUCKETS[int(round((b + s) / 2))]

_ALL_SECTORS = ['Technology', 'Financial Services', 'Healthcare', 'Consumer Cyclical',
                'Consumer Defensive', 'Industrials', 'Energy', 'Utilities',
                'Real Estate', 'Basic Materials', 'Communication Services']
_ALL_ASSET_TYPES = ['Equity', 'ETF', 'Bond', 'Mutualfund', 'Cash']
_ALL_MARKET_CAPS = ['Mega cap', 'Large cap', 'Mid cap', 'Small cap', 'Fund']
_ALL_CURRENCIES = ['CAD', 'USD']


def _known_buckets(dimension):
    return {
        'sector': _ALL_SECTORS, 'asset_type': _ALL_ASSET_TYPES,
        'market_cap': _ALL_MARKET_CAPS, 'currency': _ALL_CURRENCIES,
        'beta': _BETA_BUCKETS, 'blend': _BLEND_BUCKETS,
    }.get(dimension, [])


def _bucket_weights(h, m, dimension):
    """Fractional {bucket: weight} for one holding (weights sum to ~1).
    Sector & market-cap use ETF look-through when available."""
    if dimension == 'sector':
        if m.get('fund_sectors'):
            tot = sum(m['fund_sectors'].values()) or 1
            return {sec: w / tot for sec, w in m['fund_sectors'].items()}
        if m.get('sector'):
            return {m['sector']: 1.0}
        return {'Unclassified': 1.0}
    if dimension == 'asset_type':
        return {(m.get('asset_type') or 'Equity'): 1.0}
    if dimension == 'market_cap':
        if (m.get('asset_type') or 'Equity') == 'ETF':
            return {'Fund': 1.0}
        return {(_cap_bucket(m.get('market_cap')) or 'Unclassified'): 1.0}
    if dimension == 'currency':
        return {h['currency']: 1.0}
    if dimension == 'beta':
        return {_beta_bucket(m.get('beta'), m.get('asset_type')): 1.0}
    if dimension == 'blend':
        return {_blend_bucket(m.get('beta'), m.get('market_cap'),
                              m.get('asset_type'), m.get('fund_sectors')): 1.0}
    return {'Unclassified': 1.0}


def _bucket_of_ticker(ticker, currency, m, dimension):
    """Single dominant bucket for a non-held ticker (watchlist classification)."""
    w = _bucket_weights({'currency': currency}, m, dimension)
    return max(w, key=w.get) if w else 'Unclassified'


_ASSET_CLASS_LABELS = {
    'stockPosition': 'Stock', 'bondPosition': 'Bond', 'cashPosition': 'Cash',
    'preferredPosition': 'Preferred', 'convertiblePosition': 'Convertible',
    'otherPosition': 'Other', 'realestatePosition': 'Real Estate',
}


def _asset_class_weights(m):
    """Fractional stock/bond/cash/... weights for a holding via ETF look-through;
    individual securities default to Stock."""
    if m.get('fund_assets'):
        tot = sum(m['fund_assets'].values()) or 1
        out = {}
        for k, v in m['fund_assets'].items():
            label = _ASSET_CLASS_LABELS.get(k, k.replace('Position', '').title())
            out[label] = out.get(label, 0) + v / tot
        return out
    return {'Stock': 1.0}


def _rebal_key(account, dimension):
    slug = account.lower().replace(' ', '_').replace('-', '_')
    return f'rebal_{dimension}_{slug}'


def get_rebal_targets(account, dimension):
    from models import Setting
    import json
    if not account:
        return {}
    s = Setting.query.get(_rebal_key(account, dimension))
    if not s or not s.value:
        return {}
    try:
        return {k: float(v) for k, v in json.loads(s.value).items()}
    except Exception:
        return {}


def save_rebal_targets(account, dimension, targets):
    from models import Setting, db
    import json
    cleaned = {}
    for k, v in targets.items():
        try:
            fv = round(float(v), 2)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            cleaned[k] = fv
    key = _rebal_key(account, dimension)
    s = Setting.query.get(key)
    payload = json.dumps(cleaned)
    if s:
        s.value = payload
    else:
        db.session.add(Setting(key=key, value=payload))
    db.session.commit()


def _watchlist_by_bucket(dimension):
    """{bucket: [{ticker, company}]} for watchlist items, for suggesting names
    in under-target buckets where the account holds nothing."""
    from models import WatchlistItem
    from price_service import get_holdings_metadata
    items = WatchlistItem.query.all()
    if not items:
        return {}
    metas = get_holdings_metadata([w.ticker for w in items])
    out = {}
    for w in items:
        m = metas.get(w.ticker, {})
        if dimension == 'sector' and not m.get('sector') and not m.get('fund_sectors') and w.sector:
            bucket = w.sector  # fall back to the stored watchlist sector
        else:
            bucket = _bucket_of_ticker(w.ticker, w.currency or 'CAD', m, dimension)
        focused = (m.get('asset_type') or 'Equity') != 'ETF'  # pure-play, not another broad ETF
        out.setdefault(bucket, []).append(
            {'ticker': w.ticker, 'company': w.company or '', 'focused': focused})
    for cands in out.values():  # focused (pure-play) names first
        cands.sort(key=lambda x: (not x['focused'], x['ticker']))
    return out


def get_rebalancer_data(account=None, dimension='sector', mode='cash', deploy_cash=None):
    """Per-account rebalancing analysis + trade recommendations."""
    from price_service import get_holdings_metadata

    if dimension not in REBAL_DIMENSIONS:
        dimension = 'sector'
    if mode not in ('cash', 'full'):
        mode = 'cash'

    all_holdings = [h for h in get_holdings() if (h['market_value_cad'] or 0) > 0]
    cash_by = get_cash_by_account()
    meta_all = get_holdings_metadata([h['ticker'] for h in all_holdings])

    # Accounts with holdings or cash drive the chip selector.
    acct_names = sorted(
        {h['account'] for h in all_holdings} |
        {a for a, c in cash_by.items() if abs(c) > 0.005}
    )
    if account not in acct_names:
        account = acct_names[0] if acct_names else None

    # ---- Overall portfolio allocation (read-only), multiple view lenses ----
    # Each lens is fractional (ETF look-through) and includes total cash so the
    # view sums to the whole portfolio. Independent of the targeting dimension.
    total_cash = sum(c for c in cash_by.values() if c > 0)
    view_fns = {
        'sector': lambda h, m: _bucket_weights(h, m, 'sector'),
        'asset_class': lambda h, m: _asset_class_weights(m),
        'market_cap': lambda h, m: _bucket_weights(h, m, 'market_cap'),
        'currency': lambda h, m: _bucket_weights(h, m, 'currency'),
        'beta': lambda h, m: _bucket_weights(h, m, 'beta'),
        'blend': lambda h, m: _bucket_weights(h, m, 'blend'),
    }
    overall_views = {}
    for vk, fn in view_fns.items():
        agg, tot = {}, 0.0
        for h in all_holdings:
            mv = h['market_value_cad']
            tot += mv
            for b, w in fn(h, meta_all.get(h['ticker'], {})).items():
                agg[b] = agg.get(b, 0) + mv * w
        if total_cash > 0:
            cb = 'CAD' if vk == 'currency' else 'Cash'
            agg[cb] = agg.get(cb, 0) + total_cash
            tot += total_cash
        overall_views[vk] = sorted(
            [{'label': k, 'value': round(v, 2),
              'pct': round(v / tot * 100, 1) if tot else 0}
             for k, v in agg.items()],
            key=lambda x: x['value'], reverse=True)

    base_result = {
        'account': account, 'accounts': acct_names,
        'dimension': dimension, 'dimension_label': REBAL_DIM_LABELS[dimension],
        'dimensions': [(d, REBAL_DIM_LABELS[d]) for d in REBAL_DIMENSIONS],
        'mode': mode, 'overall_views': overall_views,
        'overall_view_labels': [('sector', 'Sector'), ('asset_class', 'Asset Class'),
                                ('market_cap', 'Market Cap'), ('currency', 'Currency'),
                                ('beta', 'Beta'), ('blend', 'Blended Risk')],
        'known_buckets': _known_buckets(dimension),
    }
    if not account:
        return {**base_result, 'buckets': [], 'trades': [], 'new_positions': [],
                'cash': 0, 'deploy_cash': 0, 'invested': 0, 'targets_set': False,
                'cash_after': 0, 'target_total': 0}

    holdings = [h for h in all_holdings if h['account'] == account]
    cash = cash_by.get(account, 0.0)
    avail_cash = max(0.0, cash)
    invested = sum(h['market_value_cad'] for h in holdings)
    targets = get_rebal_targets(account, dimension)   # {bucket: pct}

    # For Asset Type, cash is itself an asset class held in the account, so it's
    # a bucket and percentages are of the whole account. For sector/market-cap/
    # currency, cash isn't a category — it's external fuel, so the base is the
    # invested holdings plus whatever cash we're deploying.
    cash_as_bucket = (dimension == 'asset_type')
    if cash_as_bucket:
        base = pct_base = invested + cash
        if deploy_cash is None:
            # Deploy everything except what a Cash target wants kept.
            keep = base * float(targets.get('Cash', 0)) / 100
            deploy_cash = avail_cash - keep
        deploy_cash = max(0.0, min(deploy_cash, avail_cash))
    else:
        if deploy_cash is None:
            deploy_cash = avail_cash
        deploy_cash = max(0.0, min(deploy_cash, avail_cash))
        base = invested + deploy_cash
        pct_base = invested

    # Per-holding fractional exposure and current bucket dollars.
    hold_exp, current = [], {}
    for h in holdings:
        w = _bucket_weights(h, meta_all.get(h['ticker'], {}), dimension)
        hold_exp.append((h, w))
        for b, frac in w.items():
            current[b] = current.get(b, 0) + h['market_value_cad'] * frac
    if cash_as_bucket and cash > 0:
        current['Cash'] = current.get('Cash', 0) + cash

    targets_set = bool(targets)
    target_total = round(sum(targets.values()), 1)

    # Include every known bucket so the editor can target ones not yet held.
    bucket_labels = sorted(set(current) | set(targets) | set(_known_buckets(dimension)))
    target_val = {b: base * float(targets.get(b, 0)) / 100 for b in bucket_labels}
    shortfalls = {b: max(0.0, target_val[b] - current.get(b, 0)) for b in bucket_labels}
    surpluses = {b: max(0.0, current.get(b, 0) - target_val[b]) for b in bucket_labels}

    trade_by_ticker = {h['ticker']: 0.0 for h in holdings}

    def _exposed(b):
        exps = [(h, w[b] * h['market_value_cad']) for h, w in hold_exp if w.get(b, 0) > 0]
        return exps, sum(e for _, e in exps)

    # Cash is never a tradable holding — it's the funding source, not a buy/sell.
    tradable_short = {b: s for b, s in shortfalls.items() if b != 'Cash'}

    # Buckets with no holding to buy into can't be filled by trades; their share
    # of cash stays uninvested and they're surfaced as new-position suggestions.
    if mode == 'cash':
        total_short = sum(tradable_short.values())
        spend = min(deploy_cash, total_short)
        for b, short in tradable_short.items():
            if short <= 0 or total_short <= 0:
                continue
            buy_b = short / total_short * spend
            exps, tot = _exposed(b)
            if tot <= 0:
                continue
            for h, e in exps:
                trade_by_ticker[h['ticker']] += buy_b * (e / tot)
    else:  # full rebalance — sell surpluses, buy shortfalls
        for b, surp in surpluses.items():
            if surp <= 0 or b == 'Cash':
                continue
            exps, tot = _exposed(b)
            if tot <= 0:
                continue
            for h, e in exps:
                trade_by_ticker[h['ticker']] -= surp * (e / tot)
        for b, short in tradable_short.items():
            if short <= 0:
                continue
            exps, tot = _exposed(b)
            if tot <= 0:
                continue
            for h, e in exps:
                trade_by_ticker[h['ticker']] += short * (e / tot)

    # Clamp sells to what's actually held.
    for h in holdings:
        if trade_by_ticker[h['ticker']] < -h['market_value_cad']:
            trade_by_ticker[h['ticker']] = -h['market_value_cad']

    # Build trade list with share counts from live prices.
    trades = []
    for h in holdings:
        amt = trade_by_ticker.get(h['ticker'], 0.0)
        if abs(amt) < 1.0:
            continue
        price = h['live_price_cad']
        trades.append({
            'ticker': h['ticker'], 'action': 'Buy' if amt > 0 else 'Sell',
            'amount_cad': round(abs(amt), 2), 'currency': h['currency'],
            'price_cad': round(price, 2) if price else None,
            'shares': round(abs(amt) / price, 4) if price else None,
        })
    trades.sort(key=lambda t: t['amount_cad'], reverse=True)

    net_cash_used = sum(t['amount_cad'] if t['action'] == 'Buy' else -t['amount_cad'] for t in trades)
    cash_after = cash - net_cash_used

    # Projected allocation after the recommended trades — reflects only what the
    # existing holdings + trades actually achieve (so ETF dilution shows up as a
    # residual gap rather than being optimistically filled).
    proj, proj_total = {}, 0.0
    for h, w in hold_exp:
        mv = h['market_value_cad'] + trade_by_ticker.get(h['ticker'], 0.0)
        proj_total += mv
        for b, frac in w.items():
            proj[b] = proj.get(b, 0) + mv * frac
    if cash_as_bucket and cash > 0:
        proj['Cash'] = proj.get('Cash', 0) + cash_after
        proj_total += cash_after

    buckets = []
    for b in bucket_labels:
        cur = current.get(b, 0)
        buckets.append({
            'label': b,
            'current_value': round(cur, 2),
            'current_pct': round(cur / pct_base * 100, 1) if pct_base else 0,
            'target_pct': round(float(targets.get(b, 0)), 1),
            'target_value': round(target_val[b], 2),
            'projected_pct': round(proj.get(b, 0) / proj_total * 100, 1) if proj_total else 0,
            'drift': round(cur - target_val[b], 2),
        })
    if dimension in ORDINAL_DIMENSIONS:
        order = {b: i for i, b in enumerate(_known_buckets(dimension))}
        buckets.sort(key=lambda x: order.get(x['label'], 999))
    else:
        buckets.sort(key=lambda x: (x['target_pct'], x['current_value']), reverse=True)

    # Suggest a focused new position wherever a targeted bucket is still well
    # under target after trades: either nothing is held there, or what's held
    # (broad ETFs) can't close the gap. Cash is funding, never a suggestion.
    wl = _watchlist_by_bucket(dimension)
    floor = max(250.0, 0.01 * base)  # ignore trivial gaps (< ~1% of the account)
    new_positions = []
    for bk in buckets:
        b = bk['label']
        if b == 'Cash' or bk['target_pct'] <= 0:
            continue
        gap_val = bk['target_value'] - proj.get(b, 0)
        gap_pct = bk['target_pct'] - bk['projected_pct']
        if gap_val < floor:
            continue
        held = current.get(b, 0) > 0.005
        # New bucket, big absolute gap, or filled well under its target.
        under = (not held) or gap_pct >= REBAL_GAP_PP or proj.get(b, 0) < REBAL_FILL_RATIO * bk['target_value']
        if not under:
            continue
        new_positions.append({
            'bucket': b,
            'amount_cad': round(gap_val, 2),
            'gap_pct': round(gap_pct, 1),
            'reason': 'Holdings here (ETFs) leave it short' if held else 'No position yet',
            'suggestions': wl.get(b, []),
        })
    new_positions.sort(key=lambda x: x['amount_cad'], reverse=True)

    return {
        **base_result,
        'buckets': buckets,
        'trades': trades,
        'new_positions': new_positions,
        'cash': round(cash, 2),
        'deploy_cash': round(deploy_cash, 2),
        'invested': round(invested, 2),
        'cash_after': round(cash_after, 2),
        'targets_set': targets_set,
        'target_total': target_total,
    }


# ── Watchlist ─────────────────────────────────────────────────────────────────

# Curated, well-known ETFs per bucket — research ideas when filling a rebalancer
# gap (no market screener is available, so individual stocks can't be sourced).
_SECTOR_ETFS = {
    'Technology': ['XLK', 'VGT'], 'Financial Services': ['XLF'], 'Healthcare': ['XLV'],
    'Consumer Cyclical': ['XLY'], 'Consumer Defensive': ['XLP'], 'Industrials': ['XLI'],
    'Energy': ['XLE'], 'Utilities': ['XLU'], 'Real Estate': ['XLRE', 'VNQ'],
    'Basic Materials': ['XLB'], 'Communication Services': ['XLC'],
}


def _curated_for_bucket(dimension, bucket):
    if dimension == 'sector':
        return _SECTOR_ETFS.get(bucket, [])
    if dimension == 'asset_type':
        return {'ETF': ['VTI', 'XEQT'], 'Bond': ['BND', 'AGG']}.get(bucket, [])
    return []


def _fmt_mktcap(mc):
    if not mc:
        return None
    if mc >= 1e12:
        return f'${mc / 1e12:.1f}T'
    if mc >= 1e9:
        return f'${mc / 1e9:.0f}B'
    if mc >= 1e6:
        return f'${mc / 1e6:.0f}M'
    return f'${mc:.0f}'


def get_watchlist_data():
    """Enriched watchlist rows: live + CAD price, day change, classification
    (sector / asset type / market cap / beta / yield), target distance + hit
    (direction-aware), and whether the ticker is already held."""
    from models import WatchlistItem
    from price_service import get_fx_rate, get_holdings_metadata

    items = WatchlistItem.query.order_by(WatchlistItem.ticker).all()
    fx = get_fx_rate()
    metas = get_holdings_metadata([i.ticker for i in items])

    owned = {}
    for h in get_holdings():
        owned[h['ticker']] = owned.get(h['ticker'], 0) + (h['market_value_cad'] or 0)

    rows = []
    for it in items:
        pc = PriceCache.query.get(it.ticker)
        live = pc.price if pc else None
        prev = pc.prev_close if pc else None
        m = metas.get(it.ticker, {})
        rate = fx if it.currency == 'USD' else 1.0
        live_cad = live * rate if live else None
        day_pct = ((live - prev) / prev * 100) if (live and prev) else None

        ttype = it.target_type or 'below'
        pct_to_target = ((it.target_price - live) / live * 100) if (live and it.target_price) else None
        hit = None
        if live and it.target_price:
            hit = (live <= it.target_price) if ttype == 'below' else (live >= it.target_price)
        pct_since_added = ((live - it.added_price) / it.added_price * 100) if (live and it.added_price) else None

        # Dividend yield — prefer forward rate / price (currency-consistent).
        dr, dy = m.get('dividend_rate'), m.get('dividend_yield')
        yield_pct = (dr / live * 100) if (dr and live) else (dy if dy else None)

        mv = owned.get(it.ticker, 0)
        rows.append({
            'id': it.id, 'ticker': it.ticker,
            'company': m.get('long_name') or it.company or '',
            'currency': it.currency,
            'sector': m.get('sector') or it.sector or '',
            'asset_type': m.get('asset_type') or '',
            'market_cap': _fmt_mktcap(m.get('market_cap')),
            'market_cap_raw': m.get('market_cap') or 0,
            'beta': round(m['beta'], 2) if m.get('beta') is not None else None,
            'yield_pct': round(yield_pct, 2) if yield_pct is not None else None,
            'live_price': live, 'live_price_cad': live_cad, 'day_pct': day_pct,
            'target_price': it.target_price, 'target_type': ttype,
            'pct_to_target': pct_to_target, 'target_hit': hit,
            'added_price': it.added_price, 'pct_since_added': pct_since_added,
            'owned': mv > 0.005, 'owned_value': round(mv, 2),
            'notes': it.notes or '',
        })

    sectors = sorted({r['sector'] for r in rows if r['sector']})
    currencies = sorted({r['currency'] for r in rows if r['currency']})
    return {'rows': rows, 'sectors': sectors, 'currencies': currencies}


def get_rebalancer_gaps_all():
    """Every under-target rebalancer bucket across accounts that have targets,
    each with a few candidate tickers to fill it: watchlist names in the bucket,
    your own focused holdings there, and curated ETF ideas."""
    from models import WatchlistItem
    from price_service import get_holdings_metadata

    all_holdings = [h for h in get_holdings() if (h['market_value_cad'] or 0) > 0]
    cash_by = get_cash_by_account()
    accounts = sorted({h['account'] for h in all_holdings} |
                      {a for a, c in cash_by.items() if abs(c) > 0.005})
    meta_all = get_holdings_metadata([h['ticker'] for h in all_holdings])
    wl_tickers = {i.ticker for i in WatchlistItem.query.all()}

    out = []
    for acc in accounts:
        for dim in REBAL_DIMENSIONS:
            if not get_rebal_targets(acc, dim):
                continue
            d = get_rebalancer_data(account=acc, dimension=dim)
            for np in d['new_positions']:
                bucket = np['bucket']
                cands, seen = [], set()
                for s in np['suggestions']:  # watchlist names in this bucket, focused-first
                    cands.append({'ticker': s['ticker'], 'source': 'watchlist',
                                  'focused': s['focused'], 'in_wl': True})
                    seen.add(s['ticker'])
                for h in all_holdings:  # your own focused holdings in this bucket
                    if h['account'] != acc or h['ticker'] in seen:
                        continue
                    m = meta_all.get(h['ticker'], {})
                    if (m.get('asset_type') or 'Equity') == 'ETF':
                        continue
                    if _bucket_weights(h, m, dim).get(bucket, 0) > 0.5:
                        cands.append({'ticker': h['ticker'], 'source': 'holding',
                                      'focused': True, 'in_wl': h['ticker'] in wl_tickers})
                        seen.add(h['ticker'])
                for tk in _curated_for_bucket(dim, bucket):  # curated ETF ideas
                    if tk not in seen:
                        cands.append({'ticker': tk, 'source': 'idea',
                                      'focused': True, 'in_wl': tk in wl_tickers})
                        seen.add(tk)
                out.append({
                    'account': acc, 'dimension': dim, 'dimension_label': REBAL_DIM_LABELS[dim],
                    'bucket': bucket, 'gap_pct': np['gap_pct'], 'amount_cad': np['amount_cad'],
                    'reason': np['reason'], 'candidates': cands[:6],
                })
    out.sort(key=lambda x: x['amount_cad'], reverse=True)
    return out


def get_rebalancer_gap_summary(gaps):
    """A vague, plain-language 'how to balance everything' line per account,
    built from the gaps. Uses the sector gaps as the buy list (dollars don't
    double-count within one dimension) and folds in a size/risk hint from the
    other dimensions' gaps — e.g. '$3,000 large cap Healthcare'."""
    by_acct = {}
    for g in gaps:
        by_acct.setdefault(g['account'], []).append(g)

    out = []
    for acct, items in by_acct.items():
        size = [g['bucket'] for g in items if g['dimension'] == 'market_cap']
        risk = [g['bucket'] for g in items if g['dimension'] in ('beta', 'blend')]
        adj = size[0].lower() if len(size) == 1 else None  # single size gap → adjective

        has_sector = any(g['dimension'] == 'sector' for g in items)
        if has_sector:
            primary = 'sector'
        else:
            tot = {}
            for g in items:
                tot[g['dimension']] = tot.get(g['dimension'], 0) + g['amount_cad']
            primary = max(tot, key=tot.get)

        prim = sorted([g for g in items if g['dimension'] == primary],
                      key=lambda x: -x['amount_cad'])
        lines = []
        for g in prim:
            label = f'{adj} {g["bucket"]}' if (adj and primary == 'sector') else g['bucket']
            lines.append({'amount': g['amount_cad'], 'label': label})

        # Secondary "favour" hints from the other dimensions' gaps, largest first.
        folded_size = adj and primary == 'sector'
        others = sorted(
            [g for g in items
             if g['dimension'] in ('market_cap', 'beta', 'blend')
             and not (folded_size and g['dimension'] == 'market_cap')],
            key=lambda x: -x['amount_cad'])
        bias = []
        for g in others:
            lbl = f'{g["bucket"]} risk' if g['dimension'] in ('beta', 'blend') else g['bucket']
            if lbl not in bias:
                bias.append(lbl)
        bias = bias[:3]

        out.append({
            'account': acct,
            'primary_label': REBAL_DIM_LABELS.get(primary, primary),
            'total': round(sum(g['amount_cad'] for g in prim), 2),
            'lines': lines,
            'bias': bias,
        })
    return out


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
