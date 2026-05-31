from models import Transaction, Account, PriceCache


def get_fx_rate():
    from price_service import get_fx_rate as _get
    return _get()


def get_holdings():
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
            rate = fx_rate if t.currency == 'USD' else 1.0
            price_cad = t.price * rate
            buy_cost = t.qty * price_cad + (t.fees_cad or 0)
            new_qty = pos['qty'] + t.qty
            new_cost = pos['total_cost_cad'] + buy_cost
            pos['avg_cost_cad'] = new_cost / new_qty if new_qty > 0 else 0
            pos['total_cost_cad'] = new_cost
            pos['qty'] = new_qty
            pos['total_fees_cad'] += t.fees_cad or 0
            pos['currency'] = t.currency

        elif t.type == 'Sell':
            rate = fx_rate if t.currency == 'USD' else 1.0
            price_cad = t.price * rate
            realized = (price_cad - pos['avg_cost_cad']) * t.qty - (t.fees_cad or 0)
            pos['realized_gl_cad'] += realized
            pos['qty'] -= t.qty
            pos['total_cost_cad'] = pos['avg_cost_cad'] * pos['qty']
            pos['total_fees_cad'] += t.fees_cad or 0

        elif t.type == 'Dividend':
            pos['dividends_cad'] += t.amount_cad or (t.qty * t.price)

    result = []
    for key, pos in positions.items():
        if pos['qty'] < 0.0001:
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
        })

    result.sort(key=lambda x: x['market_value_cad'] or 0, reverse=True)

    total_mv = sum(h['market_value_cad'] or 0 for h in result)
    for h in result:
        h['port_pct'] = (h['market_value_cad'] or 0) / total_mv * 100 if total_mv else 0

    return result


def get_dashboard_stats(holdings):
    total_mv = sum(h['market_value_cad'] or 0 for h in holdings)
    total_book = sum(h['book_value_cad'] for h in holdings)
    total_unrealized = total_mv - total_book
    total_unrealized_pct = (total_unrealized / total_book * 100) if total_book else 0
    total_day_change = sum(h['day_change'] or 0 for h in holdings)
    total_dividends = sum(h['dividends_cad'] for h in holdings)

    accounts = Account.query.all()
    total_cash = sum(a.cash_balance for a in accounts)

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
    all_holdings = get_holdings()
    accounts = Account.query.all()

    result = []
    for account in accounts:
        acct_holdings = [h for h in all_holdings if h['account'] == account.name]
        holdings_mv = sum(h['market_value_cad'] or 0 for h in acct_holdings)
        holdings_book = sum(h['book_value_cad'] for h in acct_holdings)
        unrealized = holdings_mv - holdings_book
        day_change = sum(h['day_change'] or 0 for h in acct_holdings)

        result.append({
            'name': account.name,
            'type': account.type,
            'id': account.id,
            'holdings_mv': holdings_mv,
            'holdings_book': holdings_book,
            'cash_balance': account.cash_balance,
            'total_value': holdings_mv + account.cash_balance,
            'unrealized_gl': unrealized,
            'unrealized_gl_pct': (unrealized / holdings_book * 100) if holdings_book else 0,
            'day_change': day_change,
            'num_holdings': len(acct_holdings),
            'holdings': acct_holdings,
        })

    return result


# ── Dividends ─────────────────────────────────────────────────────────────────

def get_dividend_stats():
    from datetime import date
    from datetime import datetime as dt

    dividends = Transaction.query.filter_by(type='Dividend').order_by(Transaction.date.asc()).all()

    today = date.today()
    ytd_start = date(today.year, 1, 1)
    ttm_start = date(today.year - 1, today.month, today.day)

    all_time = sum(d.amount_cad for d in dividends)
    ytd = sum(d.amount_cad for d in dividends if d.date >= ytd_start)
    ttm = sum(d.amount_cad for d in dividends if d.date >= ttm_start)

    by_ticker = {}
    for d in dividends:
        t = d.ticker
        if t not in by_ticker:
            by_ticker[t] = {'ticker': t, 'total': 0.0, 'ytd': 0.0, 'ttm': 0.0, 'count': 0, 'last_date': None}
        by_ticker[t]['total'] += d.amount_cad
        if d.date >= ytd_start:
            by_ticker[t]['ytd'] += d.amount_cad
        if d.date >= ttm_start:
            by_ticker[t]['ttm'] += d.amount_cad
        by_ticker[t]['count'] += 1
        if not by_ticker[t]['last_date'] or d.date > by_ticker[t]['last_date']:
            by_ticker[t]['last_date'] = d.date

    holdings = get_holdings()
    book_by_ticker = {}
    for h in holdings:
        book_by_ticker[h['ticker']] = book_by_ticker.get(h['ticker'], 0) + h['book_value_cad']

    for row in by_ticker.values():
        book = book_by_ticker.get(row['ticker'], 0)
        row['yield_on_cost'] = (row['ttm'] / book * 100) if book and row['ttm'] else None

    ticker_list = sorted(by_ticker.values(), key=lambda x: x['total'], reverse=True)

    by_year = {}
    for d in dividends:
        by_year[d.date.year] = by_year.get(d.date.year, 0) + d.amount_cad

    by_month = {}
    for d in dividends:
        if d.date >= ttm_start:
            key = d.date.strftime('%b %Y')
            by_month[key] = by_month.get(key, 0) + d.amount_cad

    sorted_months = sorted(by_month.keys(), key=lambda k: dt.strptime(k, '%b %Y'))
    month_chart = [{'label': k, 'value': round(by_month[k], 2)} for k in sorted_months]

    return {
        'all_time': all_time,
        'ytd': ytd,
        'ttm': ttm,
        'ttm_monthly_avg': ttm / 12 if ttm else 0,
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

def get_tax_summary(year=None):
    from datetime import date

    if year is None:
        year = date.today().year

    all_txns = Transaction.query.order_by(Transaction.date.asc(), Transaction.id.asc()).all()
    fx_rate = get_fx_rate()

    positions_running = {}
    tax_rows = []

    for t in all_txns:
        key = (t.ticker, t.account)
        if key not in positions_running:
            positions_running[key] = {'qty': 0.0, 'total_cost_cad': 0.0, 'avg_cost_cad': 0.0}
        pos = positions_running[key]

        if t.type == 'Buy':
            rate = fx_rate if t.currency == 'USD' else 1.0
            buy_cost = t.qty * t.price * rate + (t.fees_cad or 0)
            new_qty = pos['qty'] + t.qty
            new_cost = pos['total_cost_cad'] + buy_cost
            pos['avg_cost_cad'] = new_cost / new_qty if new_qty > 0 else 0
            pos['total_cost_cad'] = new_cost
            pos['qty'] = new_qty

        elif t.type == 'Sell':
            rate = fx_rate if t.currency == 'USD' else 1.0
            proceeds_cad = t.qty * t.price * rate - (t.fees_cad or 0)
            acb = pos['avg_cost_cad'] * t.qty
            gl = proceeds_cad - acb

            if t.date.year == year:
                tax_rows.append({
                    'date': t.date,
                    'ticker': t.ticker,
                    'account': t.account,
                    'qty': t.qty,
                    'proceeds_cad': proceeds_cad,
                    'acb': acb,
                    'gl': gl,
                })

            pos['qty'] = max(0, pos['qty'] - t.qty)
            pos['total_cost_cad'] = pos['avg_cost_cad'] * pos['qty']

    total_gl = sum(r['gl'] for r in tax_rows)
    total_gains = sum(r['gl'] for r in tax_rows if r['gl'] > 0)
    total_losses = sum(r['gl'] for r in tax_rows if r['gl'] < 0)
    taxable_gain = max(0, total_gl) * 0.5  # 50% inclusion rate

    sell_years = sorted(set(
        t.date.year for t in all_txns if t.type == 'Sell'
    ), reverse=True)

    return {
        'year': year,
        'rows': sorted(tax_rows, key=lambda r: r['date']),
        'total_gl': total_gl,
        'total_gains': total_gains,
        'total_losses': total_losses,
        'taxable_gain': taxable_gain,
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
    total_cash = sum(a.cash_balance for a in accounts_db)
    total_portfolio = total_mv + total_cash

    account_current = {}
    for h in holdings:
        acc = h['account']
        account_current[acc] = account_current.get(acc, 0) + (h['market_value_cad'] or 0)
    for a in accounts_db:
        account_current[a.name] = account_current.get(a.name, 0) + a.cash_balance

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

def take_portfolio_snapshot():
    from models import PortfolioSnapshot, db
    from datetime import date

    today = date.today()
    if PortfolioSnapshot.query.filter_by(date=today).first():
        return False

    holdings = get_holdings()
    accounts = Account.query.all()
    total_mv = sum(h['market_value_cad'] or 0 for h in holdings)
    total_book = sum(h['book_value_cad'] for h in holdings)
    total_cash = sum(a.cash_balance for a in accounts)

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
    values = [round(s.total_market + s.total_cash, 2) for s in snaps]
    book_values = [round(s.total_book, 2) for s in snaps]

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
