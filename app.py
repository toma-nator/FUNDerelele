from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from datetime import datetime
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'midnight-terminal-2024'

from models import db, Transaction, PriceCache, Account, Setting, GIC, WatchlistItem
db.init_app(app)

with app.app_context():
    db.create_all()
    if not Account.query.first():
        for name, atype in [('TFSA', 'TFSA'), ('RRSP', 'RRSP'), ('FHSA', 'FHSA'), ('Non-Reg', 'Non-Reg')]:
            db.session.add(Account(name=name, type=atype, cash_balance=0))
        db.session.commit()
    if not Setting.query.get('fx_usd_cad'):
        db.session.add(Setting(key='fx_usd_cad', value='1.365'))
        db.session.commit()

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


# ── Routes ────────────────────────────────────────────────────────────────────

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
    data = get_holdings()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('holdings.html', holdings=data, last_updated=last_updated, active='holdings')


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
            date=txn_date,
            ticker=ticker,
            account=account,
            type=txn_type,
            qty=qty,
            price=price,
            currency=currency,
            amount_native=amount_native,
            amount_cad=amount_cad,
            fees_cad=fees,
            net_cad=net_cad,
            notes=notes,
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


@app.route('/accounts')
def accounts():
    from calculations import get_account_summary
    data = get_account_summary()
    return render_template('accounts.html', accounts=data, active='accounts')


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


@app.route('/import')
def import_page():
    from models import Transaction
    log = Transaction.query.order_by(Transaction.created_at.desc(), Transaction.id.desc()).all()
    return render_template('import.html', active='import', log=log)


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


# ── API endpoints ─────────────────────────────────────────────────────────────

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
