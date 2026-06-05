from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from datetime import datetime
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'midnight-terminal-2024'

from models import db, Transaction, PriceCache, Account, Setting, GIC, WatchlistItem, PortfolioSnapshot, TickerMap
db.init_app(app)

with app.app_context():
    db.create_all()

    # Seed default accounts
    if not Account.query.first():
        for name, atype in [('TFSA', 'TFSA'), ('RRSP', 'RRSP'), ('FHSA', 'FHSA'), ('Non-Reg', 'Non-Reg')]:
            db.session.add(Account(name=name, type=atype, cash_balance=0))
        db.session.commit()

    if not Setting.query.get('fx_usd_cad'):
        db.session.add(Setting(key='fx_usd_cad', value='1.365'))
        db.session.commit()

    # Migrate existing tables with columns added after initial release
    from sqlalchemy import text, inspect as sa_inspect
    _insp = sa_inspect(db.engine)

    def _add_col(table, col, typedef):
        try:
            existing = [c['name'] for c in _insp.get_columns(table)]
            if col not in existing:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {typedef}'))
                db.session.commit()
        except Exception:
            db.session.rollback()

    _add_col('watchlist', 'added_date', 'DATE')
    _add_col('watchlist', 'added_price', 'FLOAT')
    _add_col('gics', 'institution', 'VARCHAR(100)')
    _add_col('transactions', 'subtype', 'VARCHAR(50) DEFAULT ""')
    _add_col('accounts', 'cash_balance', 'FLOAT DEFAULT 0')

from price_service import start_price_refresh
start_price_refresh(app)


# ── Template filters ──────────────────────────────────────────────────────────

@app.template_filter('cad')
def cad_filter(v):
    if v is None:
        return '—'
    return f'${v:,.2f}'


@app.template_filter('signed_cad')
def signed_cad_filter(v):
    if v is None:
        return '—'
    sign = '+' if v >= 0 else ''
    return f'{sign}${v:,.2f}'


@app.template_filter('pct')
def pct_filter(v):
    if v is None:
        return '—'
    sign = '+' if v >= 0 else ''
    return f'{sign}{v:.2f}%'


@app.template_filter('gl_class')
def gl_class_filter(v):
    if v is None:
        return 'text-dim'
    return 'text-green' if v >= 0 else 'text-red'


@app.template_filter('num')
def num_filter(v, decimals=4):
    if v is None:
        return '—'
    return f'{v:,.{decimals}f}'


# ── Core routes ───────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    from calculations import get_holdings, get_dashboard_stats
    holdings = get_holdings()
    stats = get_dashboard_stats(holdings)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('dashboard.html',
                           stats=stats,
                           holdings=holdings[:10],
                           last_updated=last_updated,
                           active='dashboard')


@app.route('/holdings')
def holdings():
    from calculations import get_holdings
    data = get_holdings(include_closed=True)
    accounts = sorted({h['account'] for h in data})
    currencies = sorted({h['currency'] for h in data})
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('holdings.html', holdings=data, accounts=accounts,
                           currencies=currencies, last_updated=last_updated, active='holdings')


@app.route('/transactions')
def transactions():
    txns = Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    accounts = Account.query.order_by(Account.name).all()
    return render_template('transactions.html', transactions=txns, accounts=accounts, active='transactions')


@app.route('/transactions/add', methods=['POST'])
def add_transaction():
    from price_service import get_fx_rate, refresh_prices
    try:
        txn_date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        ticker = request.form['ticker'].strip().upper()
        account = request.form['account']
        txn_type = request.form['type']
        qty = float(request.form['qty'])
        price = float(request.form['price'])
        currency = request.form['currency']
        fees = float(request.form.get('fees', 0) or 0)
        notes = request.form.get('notes', '')

        fx = get_fx_rate()
        amount_native = qty * price
        amount_cad = amount_native * (fx if currency == 'USD' else 1.0)
        net_cad = (amount_cad - fees) if txn_type in ('Sell', 'Dividend') else -(amount_cad + fees)

        db.session.add(Transaction(
            date=txn_date, ticker=ticker, account=account, type=txn_type,
            qty=qty, price=price, currency=currency,
            amount_native=amount_native, amount_cad=amount_cad,
            fees_cad=fees, net_cad=net_cad, notes=notes,
        ))
        db.session.commit()
        refresh_prices([ticker])
        flash(f'Added: {txn_type} {qty:g} {ticker} @ ${price:,.2f} {currency}', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('transactions'))


@app.route('/transactions/delete/<int:id>', methods=['POST'])
def delete_transaction(id):
    txn = Transaction.query.get_or_404(id)
    db.session.delete(txn)
    db.session.commit()
    flash('Transaction deleted.', 'info')
    next_page = request.args.get('next', 'transactions')
    return redirect(url_for(next_page))


@app.route('/transactions/delete-bulk', methods=['POST'])
def delete_bulk_transactions():
    ids_raw = request.form.get('ids', '')
    ids = [int(i) for i in ids_raw.split(',') if i.strip().isdigit()]
    if ids:
        Transaction.query.filter(Transaction.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        flash(f'Deleted {len(ids)} transaction(s).', 'info')
    return redirect(url_for('transactions'))


@app.route('/transactions/delete-all', methods=['POST'])
def delete_all_transactions():
    count = Transaction.query.count()
    Transaction.query.delete()
    db.session.commit()
    flash(f'Deleted all {count} transactions.', 'info')
    return redirect(url_for('transactions'))


@app.route('/accounts')
def accounts():
    from calculations import get_account_summary
    data = get_account_summary()
    return render_template('accounts.html', accounts=data, active='accounts',
                           account_types=ACCOUNT_TYPES)


@app.route('/accounts/<name>/cash', methods=['POST'])
def update_cash(name):
    account = Account.query.filter_by(name=name).first_or_404()
    try:
        account.cash_balance = float(request.form['cash_balance'])
        db.session.commit()
        flash(f'Cash updated for {name}.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('accounts'))


# Account types — registered ones are tax-sheltered (see Tax & ACB tab).
ACCOUNT_TYPES = ['Non-Reg', 'TFSA', 'RRSP', 'FHSA', 'RDSP', 'RESP', 'LIRA', 'RRIF']


@app.route('/accounts/<name>/type', methods=['POST'])
def update_account_type(name):
    account = Account.query.filter_by(name=name).first_or_404()
    new_type = request.form.get('type', '').strip()
    if new_type:
        account.type = new_type
        db.session.commit()
        flash(f'Account type for {name} set to {new_type}.', 'success')
    return redirect(url_for('accounts'))


@app.route('/import')
def import_page():
    log = Transaction.query.order_by(Transaction.created_at.desc(), Transaction.id.desc()).all()
    mappings = TickerMap.query.order_by(TickerMap.description).all()
    # Tickers with spaces are unresolved broker descriptions, not real ticker symbols
    unmapped = (
        db.session.query(Transaction.ticker)
        .filter(Transaction.ticker.like('% %'))
        .distinct()
        .order_by(Transaction.ticker)
        .all()
    )
    unmapped = [row[0] for row in unmapped]
    return render_template('import.html', active='import', log=log,
                           mappings=mappings, unmapped=unmapped)


@app.route('/import/ticker-map/add', methods=['POST'])
def ticker_map_add():
    from price_service import refresh_prices
    description = request.form.get('description', '').strip()
    ticker = request.form.get('ticker', '').strip().upper()
    if not description or not ticker:
        flash('Both description and ticker are required.', 'error')
        return redirect(url_for('import_page'))

    existing = TickerMap.query.get(description)
    old_ticker = existing.ticker if existing else None

    if existing:
        existing.ticker = ticker
    else:
        db.session.add(TickerMap(description=description, ticker=ticker))

    # Update transactions still carrying the raw description as their ticker (first-time map)
    updated = Transaction.query.filter_by(ticker=description).update({'ticker': ticker})
    # Also update transactions already carrying the old real ticker (re-map / correction)
    if old_ticker and old_ticker != ticker:
        updated += Transaction.query.filter_by(ticker=old_ticker).update({'ticker': ticker})
    db.session.commit()

    refresh_prices([ticker])
    flash(f'Mapped "{description}" → {ticker}'
          + (f' and updated {updated} transaction(s).' if updated else '.'), 'success')
    return redirect(url_for('import_page'))


@app.route('/import/ticker-map/delete', methods=['POST'])
def ticker_map_delete():
    description = request.form.get('description', '').strip()
    mapping = TickerMap.query.get(description)
    if mapping:
        db.session.delete(mapping)
        db.session.commit()
        flash(f'Removed mapping for "{description}".', 'info')
    return redirect(url_for('import_page'))


@app.route('/import/upload', methods=['POST'])
def import_upload():
    from importers import parse_upload
    file = request.files.get('file')
    broker = request.form.get('broker', 'auto')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('import_page'))
    try:
        count = parse_upload(file, broker)
        flash(f'Imported {count} transactions.', 'success')
    except Exception as e:
        flash(f'Import error: {e}', 'error')
    return redirect(url_for('import_page'))


# ── Cash Flows ────────────────────────────────────────────────────────────────

@app.route('/cashflows')
def cashflows():
    from calculations import get_cashflow_stats
    account_filter = request.args.get('account', '').strip()
    subtype_filter = request.args.get('subtype', '').strip()
    data = get_cashflow_stats(
        account_filter=account_filter or None,
        subtype_filter=subtype_filter or None,
    )
    return render_template('cashflows.html', data=data, active='cashflows')


# ── Dividends ─────────────────────────────────────────────────────────────────

@app.route('/dividends')
def dividends():
    from calculations import get_dividend_stats
    stats = get_dividend_stats()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('dividends.html', stats=stats, last_updated=last_updated, active='dividends')


# ── Watchlist ─────────────────────────────────────────────────────────────────

@app.route('/watchlist')
def watchlist():
    from price_service import get_fx_rate
    items = WatchlistItem.query.order_by(WatchlistItem.ticker).all()
    fx = get_fx_rate()
    enriched = []
    for item in items:
        cached = PriceCache.query.get(item.ticker)
        live = cached.price if cached else None
        pct_to_target = ((item.target_price - live) / live * 100) if (live and item.target_price) else None
        pct_since_added = ((live - item.added_price) / item.added_price * 100) if (live and item.added_price) else None
        enriched.append({
            'id': item.id, 'ticker': item.ticker, 'company': item.company or '',
            'sector': item.sector or '', 'currency': item.currency,
            'target_price': item.target_price, 'added_price': item.added_price,
            'added_date': item.added_date, 'notes': item.notes or '',
            'live_price': live, 'pct_to_target': pct_to_target, 'pct_since_added': pct_since_added,
        })
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('watchlist.html', items=enriched, last_updated=last_updated, active='watchlist')


@app.route('/watchlist/add', methods=['POST'])
def watchlist_add():
    from price_service import get_cached_price, refresh_prices
    from datetime import date
    try:
        ticker = request.form['ticker'].strip().upper()
        cached = get_cached_price(ticker)
        if not cached:
            refresh_prices([ticker])
            cached = get_cached_price(ticker)
        target_raw = request.form.get('target_price', '').strip()
        db.session.add(WatchlistItem(
            ticker=ticker,
            company=request.form.get('company', '').strip(),
            sector=request.form.get('sector', '').strip(),
            currency=request.form.get('currency', 'CAD'),
            target_price=float(target_raw) if target_raw else None,
            added_price=cached.price if cached else None,
            added_date=date.today(),
            notes=request.form.get('notes', '').strip(),
        ))
        db.session.commit()
        flash(f'Added {ticker} to watchlist.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('watchlist'))


@app.route('/watchlist/delete/<int:id>', methods=['POST'])
def watchlist_delete(id):
    item = WatchlistItem.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Removed from watchlist.', 'info')
    return redirect(url_for('watchlist'))


# ── GICs ──────────────────────────────────────────────────────────────────────

@app.route('/gics')
def gics():
    from calculations import get_gic_stats
    data = get_gic_stats()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('gics.html', data=data, last_updated=last_updated, active='gics')


@app.route('/gics/add', methods=['POST'])
def gics_add():
    try:
        start = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        maturity = datetime.strptime(request.form['maturity_date'], '%Y-%m-%d').date()
        db.session.add(GIC(
            name=request.form.get('name', '').strip(),
            institution=request.form.get('institution', '').strip(),
            account=request.form.get('account', '').strip(),
            principal=float(request.form['principal']),
            rate=float(request.form['rate']),
            compounding=request.form.get('compounding', 'Annual'),
            start_date=start,
            maturity_date=maturity,
        ))
        db.session.commit()
        flash('GIC added.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('gics'))


@app.route('/gics/delete/<int:id>', methods=['POST'])
def gics_delete(id):
    gic = GIC.query.get_or_404(id)
    db.session.delete(gic)
    db.session.commit()
    flash('GIC deleted.', 'info')
    return redirect(url_for('gics'))


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        keys = [
            'room_tfsa', 'room_rrsp', 'room_fhsa',
            'target_alloc_tfsa', 'target_alloc_rrsp', 'target_alloc_fhsa', 'target_alloc_non_reg',
            'auto_import_folder', 'fx_manual', 'fx_manual_rate',
        ]
        for key in keys:
            val = request.form.get(key, '').strip()
            s = Setting.query.get(key)
            if s:
                s.value = val
            else:
                db.session.add(Setting(key=key, value=val))
        db.session.commit()
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    def gs(key, default=''):
        s = Setting.query.get(key)
        return s.value if s else default

    accounts = Account.query.order_by(Account.name).all()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('settings.html',
                           fx_rate=gs('fx_usd_cad', '1.365'),
                           fx_manual=gs('fx_manual', '0'),
                           fx_manual_rate=gs('fx_manual_rate', ''),
                           room_tfsa=gs('room_tfsa', ''),
                           room_rrsp=gs('room_rrsp', ''),
                           room_fhsa=gs('room_fhsa', ''),
                           target_alloc_tfsa=gs('target_alloc_tfsa', ''),
                           target_alloc_rrsp=gs('target_alloc_rrsp', ''),
                           target_alloc_fhsa=gs('target_alloc_fhsa', ''),
                           target_alloc_non_reg=gs('target_alloc_non_reg', ''),
                           auto_import_folder=gs('auto_import_folder', ''),
                           accounts=accounts,
                           last_updated=last_updated,
                           active='settings')


# ── Tax & ACB ─────────────────────────────────────────────────────────────────

@app.route('/tax')
def tax():
    from calculations import get_tax_summary
    year = request.args.get('year', type=int)
    data = get_tax_summary(year)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('tax.html', **data, last_updated=last_updated, active='tax')


# ── Rebalancer ────────────────────────────────────────────────────────────────

@app.route('/rebalancer')
def rebalancer():
    from calculations import get_rebalancer_data
    data = get_rebalancer_data()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('rebalancer.html', data=data, last_updated=last_updated, active='rebalancer')


# ── Performance ───────────────────────────────────────────────────────────────

@app.route('/performance')
def performance():
    from calculations import get_performance_data
    perf = get_performance_data()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('performance.html', perf=perf, last_updated=last_updated, active='performance')


@app.route('/performance/snapshot', methods=['POST'])
def performance_snapshot():
    from calculations import take_portfolio_snapshot
    taken = take_portfolio_snapshot()
    flash('Portfolio snapshot saved.' if taken else 'Snapshot already taken today.', 'success' if taken else 'info')
    return redirect(url_for('performance'))


@app.route('/performance/backfill', methods=['POST'])
def performance_backfill():
    from calculations import backfill_performance_history
    try:
        count = backfill_performance_history()
        if count:
            flash(f'Backfilled {count} monthly snapshot(s).', 'success')
        else:
            flash('No new snapshots to add — all months already covered, or no transactions found.', 'info')
    except Exception as e:
        flash(f'Backfill failed: {e}', 'error')
    return redirect(url_for('performance'))


# ── Projections ───────────────────────────────────────────────────────────────

@app.route('/projections')
def projections():
    from calculations import get_projections, get_holdings, get_dashboard_stats
    holdings = get_holdings()
    stats = get_dashboard_stats(holdings)
    current_value = stats['total_portfolio']
    monthly_contrib = request.args.get('monthly_contrib', 500.0, type=float)
    years = min(50, max(1, request.args.get('years', 25, type=int)))
    proj = get_projections(current_value, monthly_contrib, years)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('projections.html',
                           proj=proj, current_value=current_value,
                           monthly_contrib=monthly_contrib, years=years,
                           last_updated=last_updated, active='projections')


# ── Monte Carlo ───────────────────────────────────────────────────────────────

@app.route('/montecarlo')
def montecarlo():
    from calculations import run_monte_carlo, get_holdings, get_dashboard_stats
    holdings = get_holdings()
    stats = get_dashboard_stats(holdings)
    current_value = stats['total_portfolio']
    monthly_contrib = request.args.get('monthly_contrib', 500.0, type=float)
    years = min(50, max(1, request.args.get('years', 25, type=int)))
    mean_return = request.args.get('mean_return', 7.0, type=float)
    std_dev = request.args.get('std_dev', 15.0, type=float)
    mc = run_monte_carlo(current_value, monthly_contrib, years, mean_return / 100, std_dev / 100)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('montecarlo.html',
                           mc=mc, current_value=current_value,
                           monthly_contrib=monthly_contrib, years=years,
                           mean_return=mean_return, std_dev=std_dev,
                           last_updated=last_updated, active='montecarlo')


# ── API ───────────────────────────────────────────────────────────────────────

@app.route('/api/refresh-prices', methods=['POST'])
def api_refresh_prices():
    from price_service import refresh_prices
    tickers = [r[0] for r in Transaction.query.with_entities(Transaction.ticker).distinct()]
    refresh_prices(tickers)
    return jsonify({'status': 'ok', 'refreshed': len(tickers)})


@app.route('/api/prices')
def api_prices():
    prices = PriceCache.query.all()
    return jsonify({
        p.ticker: {
            'price': p.price,
            'prev_close': p.prev_close,
            'updated': p.last_updated.isoformat() if p.last_updated else None,
        }
        for p in prices
    })


@app.route('/api/fx')
def api_fx():
    from price_service import get_fx_rate
    return jsonify({'usd_cad': get_fx_rate()})


if __name__ == '__main__':
    app.run(debug=True)
