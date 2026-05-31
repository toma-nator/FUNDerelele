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
