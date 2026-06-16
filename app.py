from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, Response
from datetime import datetime
import json
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Secret key from the environment in production; a fixed dev fallback keeps local
# runs zero-config (sessions/flash only — no sensitive data rides on it).
app.secret_key = os.environ.get('SECRET_KEY', 'midnight-terminal-dev')
# Don't let cookies ride along on cross-site POSTs (basic CSRF hardening for a
# local single-user app — blocks a malicious page from triggering Reset/Restore).
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

from models import db, Transaction, PriceCache, Account, Setting, GIC, WatchlistItem, PortfolioSnapshot, TickerMap, RecurringRule, RDSPPlanYear
db.init_app(app)

def run_migrations():
    """Create tables, seed the FX default, and add columns introduced after the
    initial release. Safe to run repeatedly (startup, and after a DB restore)."""
    from sqlalchemy import text, inspect as sa_inspect
    with app.app_context():
        db.create_all()
        if not Setting.query.get('fx_usd_cad'):
            db.session.add(Setting(key='fx_usd_cad', value='1.365'))
            db.session.commit()

        insp = sa_inspect(db.engine)

        def _add_col(table, col, typedef):
            try:
                existing = [c['name'] for c in insp.get_columns(table)]
                if col not in existing:
                    db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {typedef}'))
                    db.session.commit()
            except Exception:
                db.session.rollback()

        _add_col('watchlist', 'added_date', 'DATE')
        _add_col('watchlist', 'added_price', 'FLOAT')
        _add_col('watchlist', 'target_type', "VARCHAR(10) DEFAULT 'below'")
        _add_col('gics', 'institution', 'VARCHAR(100)')
        _add_col('transactions', 'subtype', 'VARCHAR(50) DEFAULT ""')
        _add_col('transactions', 'import_batch', 'VARCHAR(40)')
        _add_col('transactions', 'recurring_id', 'INTEGER')
        _add_col('accounts', 'cash_balance', 'FLOAT DEFAULT 0')
        _add_col('accounts', 'horizon', 'VARCHAR(20)')
        _add_col('accounts', 'managed', 'BOOLEAN DEFAULT 0')
        _add_col('price_cache', 'meta_json', 'TEXT')
        _add_col('recurring_rules', 'dollar_based', 'BOOLEAN DEFAULT 0')


def resolve_db_path():
    """Absolute path to the live SQLite file, wherever Flask put it."""
    path = db.engine.url.database
    if path and not os.path.isabs(path):
        for cand in (os.path.join(app.instance_path, path),
                     os.path.join(app.root_path, path), os.path.abspath(path)):
            if os.path.exists(cand):
                return cand
    return path


run_migrations()

from price_service import start_price_refresh
start_price_refresh(app)

# One-time scan of the auto-import folder on startup (no-op unless configured).
with app.app_context():
    _f = Setting.query.get('auto_import_folder')
    if _f and _f.value:
        try:
            from importers import scan_import_folder
            scan_import_folder(_f.value)
        except Exception:
            pass

# Materialize any due recurring transactions on startup. Gated to the reloader
# child (like the price thread) so the dev parent/child don't both generate.
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    with app.app_context():
        try:
            from recurring import generate_due
            generate_due()
        except Exception:
            pass


# Best-effort ticker guess for an unmapped broker description (cached, non-fatal).
_ticker_guess_cache = {}


def _guess_ticker(desc):
    if desc in _ticker_guess_cache:
        return _ticker_guess_cache[desc]
    guess = ''
    try:
        import yfinance as yf
        res = yf.Search(desc, max_results=1)
        quotes = getattr(res, 'quotes', None) or []
        if quotes:
            guess = quotes[0].get('symbol', '') or ''
    except Exception:
        guess = ''
    _ticker_guess_cache[desc] = guess
    return guess


@app.context_processor
def inject_unmapped_count():
    try:
        n = (db.session.query(Transaction.ticker)
             .filter(Transaction.ticker.like('% %')).distinct().count())
    except Exception:
        n = 0
    return {'unmapped_count': n}


# ── Sidebar navigation (data-driven so it can be reordered / hidden per user) ────
NAV_SECTION_ORDER = ['MAIN', 'ANALYTICS', 'ADVANCED', 'TOOLS']

# The full tab catalog. `id` matches each page's `active` token; `endpoint` is the
# Flask route. The order/section here is only the default — it's overridden per
# user by the `sidebar_layout` setting (drag-to-reorder + hide, edited from the
# sidebar footer's Customize button).
NAV_TABS = [
    {'id': 'dashboard',    'section': 'MAIN',      'endpoint': 'dashboard',    'icon': '⬡', 'label': 'Dashboard'},
    {'id': 'holdings',     'section': 'MAIN',      'endpoint': 'holdings',     'icon': '◧', 'label': 'Holdings'},
    {'id': 'transactions', 'section': 'MAIN',      'endpoint': 'transactions', 'icon': '⇄', 'label': 'Transactions'},
    {'id': 'accounts',     'section': 'MAIN',      'endpoint': 'accounts',     'icon': '▣', 'label': 'Accounts'},
    {'id': 'performance',  'section': 'ANALYTICS', 'endpoint': 'performance',  'icon': '↗', 'label': 'Performance'},
    {'id': 'charts',       'section': 'ANALYTICS', 'endpoint': 'charts',       'icon': '◔', 'label': 'Charts'},
    {'id': 'dividends',    'section': 'ANALYTICS', 'endpoint': 'dividends',    'icon': '◎', 'label': 'Dividends'},
    {'id': 'cashflows',    'section': 'ANALYTICS', 'endpoint': 'cashflows',    'icon': '⬇', 'label': 'Cash Flows'},
    {'id': 'gics',         'section': 'ANALYTICS', 'endpoint': 'gics',         'icon': '▤', 'label': 'GICs'},
    {'id': 'rebalancer',   'section': 'ANALYTICS', 'endpoint': 'rebalancer',   'icon': '⇌', 'label': 'Rebalancer'},
    {'id': 'watchlist',    'section': 'ANALYTICS', 'endpoint': 'watchlist',    'icon': '◉', 'label': 'Watchlist'},
    {'id': 'projections',  'section': 'ADVANCED',  'endpoint': 'projections',  'icon': '⤴', 'label': 'Projections'},
    {'id': 'rdsp',         'section': 'ADVANCED',  'endpoint': 'rdsp_tab',     'icon': '◈', 'label': 'RDSP'},
    {'id': 'tax',          'section': 'ADVANCED',  'endpoint': 'tax',          'icon': '⊟', 'label': 'Tax & ACB'},
    {'id': 'import',       'section': 'TOOLS',     'endpoint': 'import_page',  'icon': '↑', 'label': 'Import'},
    {'id': 'settings',     'section': 'TOOLS',     'endpoint': 'settings',     'icon': '≡', 'label': 'Settings'},
]
_TAB_BY_ID = {t['id']: t for t in NAV_TABS}


def _load_sidebar_layout():
    """Return (sections, hidden): the per-section ordered tab lists and the hidden
    id set, from the saved `sidebar_layout` setting. Unknown ids are dropped and any
    tab missing from the save (e.g. one added in a later release) is appended to its
    default section, so the nav self-heals instead of silently dropping tabs."""
    saved = {}
    raw = Setting.query.get('sidebar_layout')
    if raw and raw.value:
        try:
            saved = json.loads(raw.value)
        except (ValueError, TypeError):
            saved = {}
    if not isinstance(saved, dict):
        saved = {}
    saved_sections = saved.get('sections', {}) or {}
    hidden = set(saved.get('hidden', []) or [])

    placed = set()
    sections = {sec: [] for sec in NAV_SECTION_ORDER}
    for sec in NAV_SECTION_ORDER:
        for tid in saved_sections.get(sec, []):
            if tid in _TAB_BY_ID and tid not in placed:
                sections[sec].append(_TAB_BY_ID[tid])
                placed.add(tid)
    for t in NAV_TABS:                       # append any tab not placed by the save
        if t['id'] not in placed:
            sections[t['section']].append(t)
            placed.add(t['id'])
    return sections, hidden


@app.context_processor
def inject_nav():
    sections, hidden = _load_sidebar_layout()
    nav_sections = [
        {'id': sec, 'items': [{**t, 'hidden': t['id'] in hidden} for t in sections[sec]]}
        for sec in NAV_SECTION_ORDER
    ]
    return {'nav_sections': nav_sections}


@app.route('/sidebar-layout', methods=['POST'])
def save_sidebar_layout():
    """Persist the user's drag-reordered / hidden sidebar. Validates ids against the
    catalog so a stale or tampered payload can't inject junk tabs."""
    data = request.get_json(silent=True) or {}
    incoming = data.get('sections', {}) or {}
    valid, seen = set(_TAB_BY_ID), set()
    sections = {}
    for sec in NAV_SECTION_ORDER:
        ids = []
        for tid in incoming.get(sec, []) or []:
            if tid in valid and tid not in seen:
                ids.append(tid)
                seen.add(tid)
        sections[sec] = ids
    hidden = [t for t in (data.get('hidden') or []) if t in valid]
    payload = json.dumps({'sections': sections, 'hidden': hidden})
    s = Setting.query.get('sidebar_layout')
    if s:
        s.value = payload
    else:
        db.session.add(Setting(key='sidebar_layout', value=payload))
    db.session.commit()
    return jsonify(ok=True)


@app.context_processor
def inject_last_updated():
    # Powers the "prices updated HH:MM" stamp in the topbar on every page.
    try:
        lu = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    except Exception:
        lu = None
    return {'last_updated': lu}


@app.context_processor
def inject_static_versioner():
    # Cache-busting: append the file's mtime so edited CSS/JS/images reload
    # without a hard refresh.
    def static_v(filename):
        try:
            ver = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            ver = 0
        return url_for('static', filename=filename, v=ver)
    return {'static_v': static_v}


# ── Template filters ──────────────────────────────────────────────────────────

@app.template_filter('cad')
def cad_filter(v):
    if v is None:
        return '—'
    return f'${v:,.2f}'


@app.template_filter('usd')
def usd_filter(v):
    if v is None:
        return '—'
    return f'US${v:,.2f}'


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


def _clean_fund_name(name):
    import re
    name = re.sub(r'\s*\([^)]*\)\s*$', '', name or '')   # drop trailing "(code)"
    name = re.sub(r'\s+NL$', '', name)                    # drop trailing " NL"
    return name.strip()


def fund_label_map():
    """{ticker: friendly name} for mutual funds — shown by default instead of the
    cryptic symbol. Tickers listed in the `fund_show_ticker` setting are excluded
    (the user chose to keep the symbol). Cached per request."""
    from flask import g
    if hasattr(g, '_fund_labels'):
        return g._fund_labels
    import json
    show_ticker = set()
    s = Setting.query.get('fund_show_ticker')
    if s and s.value:
        show_ticker = {t.strip().upper() for t in s.value.split(',') if t.strip()}
    out = {}
    for pc in PriceCache.query.all():
        if not pc.meta_json:
            continue
        try:
            m = json.loads(pc.meta_json)
        except Exception:
            continue
        if (m.get('asset_type') == 'Mutual Fund' and m.get('long_name')
                and pc.ticker.upper() not in show_ticker):
            out[pc.ticker] = _clean_fund_name(m['long_name'])
    g._fund_labels = out
    return out


@app.template_filter('tlabel')
def tlabel_filter(ticker):
    """Display a mutual fund by its friendly name; everything else by its ticker."""
    if not ticker:
        return ticker
    return fund_label_map().get(ticker, ticker)


@app.template_filter('mktcap')
def mktcap_filter(v):
    from calculations import _fmt_mktcap
    return _fmt_mktcap(v) if v else '—'


_STYLE_LABELS = {'etf_heavy': 'ETF-heavy', 'mixed': 'Mixed', 'stock_heavy': 'Stock-heavy'}


@app.template_filter('stylelabel')
def stylelabel_filter(s):
    """Implementation-style key → human label (etf_heavy → ETF-heavy)."""
    if not s:
        return '—'
    return _STYLE_LABELS.get(s, s.replace('_', ' ').title())


@app.template_filter('localdt')
def localdt_filter(dt, mode='datetime'):
    """A UTC datetime → a <time> element that app.js re-renders in the BROWSER's
    timezone (autodetected). The server-side text is the UTC fallback if JS is off.
    mode 'time' → HH:MM, 'datetime' → date + time."""
    from markupsafe import Markup
    from datetime import timezone
    if not dt:
        return Markup('<time>—</time>')
    iso = dt.replace(tzinfo=timezone.utc).isoformat()
    fb = dt.strftime('%H:%M' if mode == 'time' else '%Y-%m-%d %H:%M')
    return Markup(f'<time data-localize="{mode}" datetime="{iso}">{fb}</time>')


@app.template_filter('sparkline')
def sparkline_filter(series, width=150, height=34):
    """A list of closes → an SVG polyline 'points' string scaled to width×height
    (y inverted so up = up), with a little vertical padding."""
    pts = [float(v) for v in (series or []) if v is not None]
    if len(pts) < 2:
        return ''
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    pad, n = 3.0, len(pts)
    out = []
    for i, v in enumerate(pts):
        x = i / (n - 1) * width
        y = (height - pad) - (v - lo) / rng * (height - 2 * pad)
        out.append(f'{x:.1f},{y:.1f}')
    return ' '.join(out)


# ── Core routes ───────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    import dashboard as dash
    overview = dash.build_overview()
    return render_template('dashboard.html',
                           hero=overview['hero'],
                           kpis=overview['kpis'],
                           alerts=dash.price_alerts(),
                           kpi_catalog=dash.KPI_CATALOG,
                           widget_groups=dash.widget_catalog_grouped(),
                           active='dashboard')


@app.route('/dashboard/sparkline')
def dashboard_sparkline():
    import dashboard as dash
    return jsonify(dash.sparkline())


@app.route('/dashboard/widget')
def dashboard_widget():
    import dashboard as dash
    wid = request.args.get('id', '')
    account = request.args.get('account', '').strip() or None
    if wid.startswith('chart:'):
        from charts import build_chart
        return jsonify({'kind': 'chart', 'data': build_chart(wid[6:], account)})
    fn = dash.HTML_WIDGET_FNS.get(wid)
    if not fn:
        return jsonify({'kind': 'error', 'error': 'Unknown widget'})
    if wid == 'account_highlights':
        ctx = fn(request.args.get('basis', 'contrib'), request.args.get('cols', ''))
    elif wid == 'top_holdings':
        ctx = fn(request.args.get('count', '5'))
    else:
        ctx = fn()
    html = render_template(f'widgets/{wid}.html', **ctx)
    return jsonify({'kind': 'html', 'title': dash.HTML_WIDGET_NAMES.get(wid, wid),
                    'title_suffix': ctx.get('title_suffix'), 'html': html})


@app.route('/holdings')
def holdings():
    from calculations import get_holdings
    from price_service import get_holdings_metadata
    data = get_holdings(include_closed=True)
    accounts = sorted({h['account'] for h in data})
    currencies = sorted({h['currency'] for h in data})
    # Friendly names (e.g. cryptic mutual-fund symbols → "CIBC Smart Growth A").
    tickers = sorted({h['ticker'] for h in data if h['ticker'] and h['ticker'] != 'CASH'})
    meta = get_holdings_metadata(tickers)
    names = {t: (meta.get(t, {}).get('long_name') or '') for t in tickers}
    # Blended-risk bucket + measured volatility per ticker (same classifier as the
    # Rebalancer), for the Vol / Risk columns. `rank` drives the risk sort order.
    from calculations import _blend_bucket, _BLEND_BUCKETS
    risk = {}
    for t in tickers:
        m = meta.get(t, {})
        vol = m.get('volatility')
        bucket = _blend_bucket(t, m)
        risk[t] = {
            'vol_pct': round(vol * 100, 1) if vol is not None else None,
            'bucket': bucket,
            'rank': _BLEND_BUCKETS.index(bucket) if bucket in _BLEND_BUCKETS else None,
        }
    # Region/country classification + whether it's a manual override (editable inline).
    from calculations import _region_of, region_overrides, _ALL_REGIONS
    rov = region_overrides()
    region = {}
    for t in tickers:
        o = rov.get(t.upper())
        region[t] = {'value': _region_of(t, meta.get(t, {})), 'overridden': t.upper() in rov,
                     'weighted': isinstance(o, dict), 'split': o if isinstance(o, dict) else None}
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('holdings.html', holdings=data, accounts=accounts,
                           currencies=currencies, names=names, risk=risk,
                           region=region, regions=_ALL_REGIONS,
                           last_updated=last_updated, active='holdings')


def _used_account_names():
    """Names of accounts that have at least one transaction — used to keep
    empty/stale accounts out of selection dropdowns (you can still type a new
    name where the field allows it)."""
    return {r[0] for r in Transaction.query.with_entities(Transaction.account).distinct()}


@app.route('/transactions')
def transactions():
    from recurring import generate_due
    generate_due()  # materialize any due recurring rows before listing
    txns = Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    used = _used_account_names()
    accounts = [a for a in Account.query.order_by(Account.name).all() if a.name in used]
    rules = RecurringRule.query.order_by(
        RecurringRule.account, RecurringRule.active.desc(), RecurringRule.next_date).all()
    return render_template('transactions.html', transactions=txns, accounts=accounts,
                           recurring_rules=rules, active='transactions')


@app.route('/transactions/add', methods=['POST'])
def add_transaction():
    from price_service import get_fx_rate, refresh_prices
    try:
        txn_date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        account = request.form['account'].strip()
        if not account:
            raise ValueError('Account is required.')
        # Create the account on first use so transactions can be added before any import.
        if not Account.query.filter_by(name=account).first():
            db.session.add(Account(name=account, type='Non-Reg', cash_balance=0))
        txn_type = request.form['type']
        qty = float(request.form.get('qty') or 0)
        price = float(request.form.get('price') or 0)
        amount_in = float(request.form.get('amount') or 0)
        currency = request.form.get('currency', 'CAD') or 'CAD'
        fees = float(request.form.get('fees', 0) or 0)
        notes = request.form.get('notes', '')
        # Mutual-fund style: buy a fixed $ amount; units = amount ÷ NAV.
        dollar_based = request.form.get('dollar_based') == 'on'

        # Currency Exchange is a two-legged cash transfer (one side must be CAD),
        # handled by its own helper before the share/cash field logic below.
        if txn_type == 'CurrencyExchange':
            from currency import add_exchange
            from_ccy = request.form.get('from_currency', 'CAD')
            to_ccy = request.form.get('to_currency', 'USD')
            from_amt = float(request.form.get('from_amount') or 0)
            to_amt = float(request.form.get('to_amount') or 0)
            add_exchange(account, txn_date, from_ccy, from_amt, to_ccy, to_amt, notes=notes)
            flash(f'Recorded exchange: {from_amt:g} {from_ccy} → {to_amt:g} {to_ccy} in {account}.', 'success')
            return redirect(url_for('transactions'))

        subtype = request.form.get('subtype', '').strip() if txn_type == 'Deposit' else ''

        # Cash-only types live on the CASH pseudo-ticker; the rest use the field.
        CASH_TYPES = ('Interest', 'ReturnOfCapital', 'Deposit', 'Fee')
        ticker = 'CASH' if txn_type in CASH_TYPES else request.form.get('ticker', '').strip().upper()
        if txn_type not in CASH_TYPES and not ticker:
            raise ValueError('Ticker is required for this type.')

        # A repeat cadence turns this into a recurring rule instead of a one-off:
        # the engine then materializes the first (and any already-due) occurrences.
        from recurring import FREQUENCIES, compute_amounts, generate_due
        repeat = (request.form.get('repeat', '') or '').strip().lower()
        if repeat in FREQUENCIES:
            end_raw = request.form.get('repeat_end', '').strip()
            count_raw = request.form.get('repeat_count', '').strip()
            rule = RecurringRule(
                account=account, ticker=ticker, type=txn_type,
                qty=qty, price=price, currency=currency, amount=amount_in,
                fees=fees, notes=notes, subtype=subtype, frequency=repeat,
                next_date=txn_date,
                end_date=datetime.strptime(end_raw, '%Y-%m-%d').date() if end_raw else None,
                count_remaining=int(count_raw) if count_raw.isdigit() else None,
                dollar_based=dollar_based and txn_type in ('Buy', 'Reinvest'),
            )
            db.session.add(rule)
            db.session.commit()
            made = generate_due()
            flash(f'Recurring rule added ({repeat}); generated {made} transaction(s) to date.', 'success')
            return redirect(url_for('transactions'))

        # One-off dollar-based buy: derive units from the NAV (use the entered price
        # per unit if given, otherwise look up the fund's NAV on the trade date).
        if dollar_based and txn_type in ('Buy', 'Reinvest') and amount_in:
            from price_service import get_nav_on
            nav = price if price > 0 else (get_nav_on(ticker, txn_date) or 0)
            if not nav:
                raise ValueError('Could not find a NAV for that fund on that date — enter the price per unit.')
            price, qty = nav, amount_in / nav

        fx = get_fx_rate()
        rate = fx if currency == 'USD' else 1.0
        amount_native, amount_cad, net_cad = compute_amounts(txn_type, qty, price, amount_in, fees, rate)

        db.session.add(Transaction(
            date=txn_date, ticker=ticker, account=account, type=txn_type,
            qty=qty, price=price, currency=currency,
            amount_native=amount_native, amount_cad=amount_cad,
            fees_cad=fees, net_cad=net_cad, notes=notes, subtype=subtype,
        ))
        db.session.commit()
        if ticker and ticker != 'CASH':
            refresh_prices([ticker])
        if txn_type in ('Buy', 'Sell', 'Reinvest'):
            flash(f'Added: {txn_type} {qty:g} {ticker} @ ${price:,.2f} {currency}', 'success')
        elif txn_type == 'Split':
            flash(f'Added: Split {ticker} +{qty:g} shares', 'success')
        else:
            tail = f' · {ticker}' if ticker != 'CASH' else ''
            flash(f'Added: {txn_type} ${amount_in:,.2f} {currency}{tail}', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('transactions'))


@app.route('/recurring/<int:id>/pause', methods=['POST'])
def toggle_recurring(id):
    rule = RecurringRule.query.get_or_404(id)
    nxt = request.args.get('next')
    rule.active = not rule.active
    db.session.commit()
    flash(f"Recurring payment {'resumed' if rule.active else 'stopped'}.", 'info')
    return redirect(nxt or url_for('transactions'))


@app.route('/recurring/<int:id>/delete', methods=['POST'])
def delete_recurring(id):
    rule = RecurringRule.query.get_or_404(id)
    db.session.delete(rule)
    db.session.commit()
    flash('Recurring rule deleted (generated transactions kept).', 'info')
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
    from calculations import get_account_summary, HORIZON_BUCKETS, gic_value
    from datetime import date
    data = get_account_summary()
    # Recurring payments grouped by account, for a per-account list + Stop button.
    rules_by_account = {}
    for r in RecurringRule.query.order_by(RecurringRule.active.desc(), RecurringRule.next_date).all():
        rules_by_account.setdefault(r.account, []).append(r)
    # Active GICs grouped by account, shown as positions in the account detail.
    today = date.today()
    gics_by_account = {}
    for g in GIC.query.order_by(GIC.maturity_date).all():
        if not g.account or (g.maturity_date and today >= g.maturity_date):
            continue  # matured GICs have been returned to cash
        cur, vat = gic_value(g, today)
        gics_by_account.setdefault(g.account, []).append({
            'name': g.name or '', 'institution': g.institution or '', 'principal': g.principal or 0,
            'rate': g.rate or 0, 'current_value': cur, 'value_at_maturity': vat,
            'maturity_date': g.maturity_date})
    return render_template('accounts.html', accounts=data, active='accounts',
                           account_types=ACCOUNT_TYPES, horizon_buckets=HORIZON_BUCKETS,
                           recurring_by_account=rules_by_account, gics_by_account=gics_by_account)


@app.route('/accounts/<name>/reconcile-fx', methods=['POST'])
def reconcile_fx(name):
    Account.query.filter_by(name=name).first_or_404()
    try:
        from currency import reconcile_account_fx
        made = reconcile_account_fx(name)
        if made:
            flash(f'Reconciled {name}: added {made} balancing exchange(s); foreign cash zeroed.', 'success')
        else:
            flash(f'Nothing to reconcile for {name} (no residual foreign cash).', 'info')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('accounts'))


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
ACCOUNT_TYPES = ['Non-Reg', 'TFSA', 'RRSP', 'FHSA', 'RDSP', 'RESP', 'LIRA', 'LIF', 'RRIF', 'Savings']


@app.route('/accounts/<name>/breakdown')
def account_breakdown(name):
    from calculations import get_account_breakdown
    return jsonify(get_account_breakdown(name))


@app.route('/accounts/<name>/type', methods=['POST'])
def update_account_type(name):
    account = Account.query.filter_by(name=name).first_or_404()
    new_type = request.form.get('type', '').strip()
    if new_type:
        account.type = new_type
        db.session.commit()
        flash(f'Account type for {name} set to {new_type}.', 'success')
    return redirect(url_for('accounts'))


@app.route('/accounts/<name>/management', methods=['POST'])
def update_account_management(name):
    account = Account.query.filter_by(name=name).first_or_404()
    account.managed = (request.form.get('management', '') == 'Managed')
    db.session.commit()
    flash(f"{name} set to {'Managed' if account.managed else 'Self-directed'}.", 'success')
    return redirect(url_for('accounts'))


@app.route('/accounts/<name>/horizon', methods=['POST'])
def update_account_horizon(name):
    account = Account.query.filter_by(name=name).first_or_404()
    account.horizon = request.form.get('horizon', '').strip()
    db.session.commit()
    flash(f'Time horizon for {name} set to {account.horizon}.', 'success')
    return redirect(url_for('accounts'))


@app.route('/import')
def import_page():
    mappings = TickerMap.query.order_by(TickerMap.description).all()
    # Tickers with spaces are unresolved broker descriptions, not real symbols.
    unmapped_descs = [r[0] for r in db.session.query(Transaction.ticker)
                      .filter(Transaction.ticker.like('% %')).distinct()
                      .order_by(Transaction.ticker).all()]
    unmapped = [{'desc': d, 'guess': _guess_ticker(d)} for d in unmapped_descs]

    # Every resolved ticker in use (clean symbols, excludes raw descriptions + CASH),
    # so a wrong auto-mapped symbol (e.g. a CIBC ".TO/.NE" guess) can be corrected.
    all_tickers = [r[0] for r in db.session.query(Transaction.ticker)
                   .filter(~Transaction.ticker.like('% %'), Transaction.ticker != 'CASH')
                   .distinct().order_by(Transaction.ticker).all()]

    # "Recent Imports" = just the most recent import batch.
    latest = (db.session.query(Transaction.import_batch)
              .filter(Transaction.import_batch.isnot(None))
              .order_by(Transaction.import_batch.desc()).first())
    latest_batch = latest[0] if latest else None
    log = (Transaction.query.filter_by(import_batch=latest_batch)
           .order_by(Transaction.date.desc(), Transaction.id.desc()).all()) if latest_batch else []

    used = _used_account_names()
    accounts = [a.name for a in Account.query.order_by(Account.name).all()
                if a.name in used]
    folder_setting = Setting.query.get('auto_import_folder')
    folder = folder_setting.value if folder_setting else ''
    return render_template('import.html', active='import', log=log, mappings=mappings,
                           unmapped=unmapped, all_tickers=all_tickers, accounts=accounts,
                           folder=folder, latest_batch=latest_batch)


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
    account_override = request.form.get('account', '').strip()
    if account_override == '__new__':
        account_override = request.form.get('account_new', '').strip()
    account_override = account_override or None
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('import_page'))
    try:
        r = parse_upload(file, broker, account_override=account_override)
        # Price the imported tickers now so new holdings don't sit unpriced.
        if r['imported']:
            try:
                from price_service import refresh_prices
                from calculations import get_holdings
                tkrs = sorted({h['ticker'] for h in get_holdings()
                               if h['ticker'] and h['ticker'] != 'CASH' and ' ' not in h['ticker']})
                refresh_prices(tkrs)
            except Exception:
                pass
        msg = f"Imported {r['imported']} transaction(s)"
        if r['skipped']:
            msg += f", skipped {r['skipped']} duplicate(s)"
        if r['accounts']:
            msg += f" · {', '.join(r['accounts'])}"
        if r['date_min'] and r['date_max']:
            msg += f" ({r['date_min']} → {r['date_max']})"
        flash(msg + '.', 'success' if r['imported'] else 'info')
    except Exception as e:
        flash(f'Import error: {e}', 'error')
    return redirect(url_for('import_page'))


@app.route('/import/undo', methods=['POST'])
def import_undo():
    batch = request.form.get('batch', '').strip()
    if batch:
        n = Transaction.query.filter_by(import_batch=batch).delete()
        db.session.commit()
        flash(f'Undid last import — removed {n} transaction(s).', 'info')
    return redirect(url_for('import_page'))


@app.route('/import/folder', methods=['POST'])
def import_folder():
    path = request.form.get('folder', '').strip()
    s = Setting.query.get('auto_import_folder')
    if s:
        s.value = path
    else:
        db.session.add(Setting(key='auto_import_folder', value=path))
    db.session.commit()
    flash('Import folder saved.', 'success')
    return redirect(url_for('import_page'))


@app.route('/import/template')
def import_template():
    from flask import Response
    headers = ('Transaction Date,Settlement Date,Activity Type,Symbol,Description,'
               'Quantity,Price,Commission,Net Amount,Currency,Account Number,Account Type')
    example = '2024-01-15,2024-01-17,Buy,AAPL,Apple Inc,10,185.00,9.99,-1859.99,USD,12345,Non-Reg'
    csv_text = headers + '\n' + example + '\n'
    return Response(csv_text, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=portfolio_import_template.csv'})


@app.route('/import/scan', methods=['POST'])
def import_scan():
    from importers import scan_import_folder
    folder_setting = Setting.query.get('auto_import_folder')
    folder = folder_setting.value if folder_setting else ''
    if not folder:
        flash('Set an import folder first.', 'info')
        return redirect(url_for('import_page'))
    s = scan_import_folder(folder)
    if s['files']:
        flash(f"Scanned folder: imported {s['imported']}, skipped {s['skipped']} across {s['files']} file(s).", 'success')
    else:
        flash('No new files found in the import folder.', 'info')
    if s['errors']:
        flash('Some files could not be imported: ' + '; '.join(s['errors']), 'error')
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
    from calculations import get_dividend_stats, managed_included_in
    scope = request.args.get('scope', 'portfolio')
    # The whole-portfolio view honours the managed-in-stats setting.
    eff_scope = 'self_directed' if (scope == 'portfolio' and not managed_included_in('dividends')) else scope
    stats = get_dividend_stats(eff_scope)
    accounts = [r[0] for r in Transaction.query.filter_by(type='Dividend')
                .with_entities(Transaction.account).distinct().all()]
    accounts.sort()
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('dividends.html', stats=stats, accounts=accounts, scope=scope,
                           last_updated=last_updated, active='dividends')


# ── Watchlist ─────────────────────────────────────────────────────────────────

def _relative_time(iso):
    """Friendly 'just now / 12m ago / 3h ago / 2d ago' from a UTC ISO timestamp."""
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except Exception:
        return None
    secs = (datetime.utcnow() - t).total_seconds()
    if secs < 90:
        return 'just now'
    if secs < 3600:
        return f'{int(secs // 60)}m ago'
    if secs < 86400:
        return f'{int(secs // 3600)}h ago'
    return f'{int(secs // 86400)}d ago'


@app.route('/watchlist')
def watchlist():
    from calculations import get_watchlist_data, get_rebalancer_gaps_all, get_rebalancer_gap_summary
    import ai_service
    data = get_watchlist_data()
    gaps = get_rebalancer_gaps_all()
    gap_summary = get_rebalancer_gap_summary(gaps)
    gap_accts = sorted({g['account'] for g in gaps})
    ai_account = request.args.get('ai_account', '').strip() or (gap_accts[0] if gap_accts else None)

    def _gs(k, d=''):
        s = Setting.query.get(k)
        return s.value if (s and s.value) else d

    ai = {
        'providers': ai_service.providers_available(),
        'default_provider': _gs('ai_provider_default', 'claude'),
        'default_style': _gs('ai_impl_style_default', 'mixed'),
        'accounts': gap_accts, 'account': ai_account,
        'record': None, 'stale': False, 'generated_ago': None, 'stats': None, 'report': None,
        'addable_buys': [], 'watch_ideas': [],
    }
    if ai_account:
        rec = ai_service.load_cached_plan(ai_account)
        if rec:
            ai['record'] = rec
            ai['generated_ago'] = _relative_time(rec.get('generated_at'))
            try:
                ai['stale'] = ai_service.plan_is_stale(rec, ai_account)
            except Exception:
                ai['stale'] = False
            _trades = rec.get('plan', {}).get('trades', [])
            _buys = sum(t['amount_cad'] for t in _trades if t.get('action') == 'Buy')
            _sells = sum(t['amount_cad'] for t in _trades if t.get('action') == 'Sell')
            ai['stats'] = {'buys': round(_buys, 2), 'sells': round(_sells, 2),
                           'net': round(_buys - _sells, 2),
                           'n_new': len(rec.get('plan', {}).get('_verified', []))}
            _wl = {r['ticker'].upper() for r in data['rows']}
            ai['addable_buys'] = sorted({t['ticker'].upper() for t in _trades
                                         if t.get('action') == 'Buy' and not t.get('currently_held')
                                         and t['ticker'].upper() not in _wl})
            _plan = rec.get('plan', {})
            _skip = {s.get('ticker', '').upper(): s.get('reason') for s in _plan.get('_skipped', [])}
            ai['watch_ideas'] = [
                {'ticker': w.get('ticker', ''), 'note': w.get('note', ''),
                 'skipped': ('already on watchlist' if (w.get('ticker') or '').upper() in _wl
                             else _skip.get((w.get('ticker') or '').upper()))}
                for w in _plan.get('new_watchlist', [])]
            ai['report'] = rec.get('report_data')   # cached enrichment (None for pre-phase-2 plans)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('watchlist.html', data=data, gaps=gaps, gap_summary=gap_summary,
                           ai=ai, last_updated=last_updated, active='watchlist')


@app.route('/watchlist/ai/generate', methods=['POST'])
def watchlist_ai_generate():
    """The single billable AI call — only reachable from an explicit, confirmed button."""
    import ai_service
    account = request.form.get('account', '').strip()
    provider = request.form.get('provider', '').strip() or 'claude'
    style = request.form.get('style', '').strip() or None
    if not account:
        flash('Pick an account first.', 'error')
        return redirect(url_for('watchlist'))
    try:
        ai_service.run_and_cache(account, provider, style)
        flash(f'AI plan generated for {account}.', 'success')
    except ai_service.AIConfigError as e:
        flash(str(e), 'error')
    except Exception as e:
        flash(f'AI generation failed: {e}', 'error')
    return redirect(url_for('watchlist', ai_account=account))


@app.route('/watchlist/ai/add', methods=['POST'])
def watchlist_ai_add():
    """Add the cached plan's verified picks to the watchlist (free — no AI call)."""
    import ai_service
    account = request.form.get('account', '').strip()
    rec = ai_service.load_cached_plan(account) if account else None
    if not rec:
        flash('No AI plan to add from — generate one first.', 'error')
        return redirect(url_for('watchlist', ai_account=account))
    plan = rec['plan']
    ai_service.validate_plan(plan, account)  # refresh dedupe against the current watchlist
    added = ai_service.add_picks_to_watchlist(plan)
    if added:
        flash(f'Added {len(added)} pick(s) to watchlist: {", ".join(added)}.', 'success')
    else:
        flash('No new picks to add (all already held or tracked).', 'info')
    return redirect(url_for('watchlist', ai_account=account))


@app.route('/watchlist/ai/add-buys', methods=['POST'])
def watchlist_ai_add_buys():
    """Add the cached plan's new BUY tickers to the watchlist (free — no AI call)."""
    import ai_service
    account = request.form.get('account', '').strip()
    rec = ai_service.load_cached_plan(account) if account else None
    if not rec:
        flash('No AI plan to add from — generate one first.', 'error')
        return redirect(url_for('watchlist', ai_account=account))
    added = ai_service.add_buy_tickers_to_watchlist(rec['plan'])
    if added:
        flash(f'Added {len(added)} buy(s) to watchlist: {", ".join(added)}.', 'success')
    else:
        flash('No new buys to add (all already held or tracked).', 'info')
    return redirect(url_for('watchlist', ai_account=account))


@app.route('/watchlist/ai/clear', methods=['POST'])
def watchlist_ai_clear():
    import ai_service
    account = request.form.get('account', '').strip()
    if account:
        ai_service.clear_cached_plan(account)
        flash('AI plan cleared.', 'info')
    return redirect(url_for('watchlist', ai_account=account))


# ── AI deep-dive report (free render of the cached plan + enrichment) ──────────────

def _load_report_ctx(account):
    """(report_data, template-context) for an account's cached plan, or (None, None).
    Uses the cached report_data when present (new plans); else computes it live so
    plans generated before phase 2 still render."""
    import ai_service, report_service
    from datetime import datetime
    rec = ai_service.load_cached_plan(account) if account else None
    if not rec:
        return None, None
    r = rec.get('report_data')
    if not r:
        try:
            r = report_service.build_report_data(account, rec['plan'])
        except Exception:
            return None, None
    gen = rec.get('generated_at')
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(gen)
        if dt.tzinfo is None:                       # stored as naive UTC (utcnow)
            dt = dt.replace(tzinfo=timezone.utc)
        gd = dt.astimezone().strftime('%b %d, %Y') if gen else ''   # → local date
    except Exception:
        gd = ''
    try:
        report_service.annotate_trade_sectors(r.get('trades') or [])
    except Exception:
        pass
    ctx = {'gen_date': gd, 'provider': rec.get('provider') or r.get('provider'),
           'model': rec.get('model') or r.get('model'), 'style': rec.get('style')}
    return r, ctx


def _find_edge():
    """Path to msedge.exe, or None."""
    import os
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'),
                 os.environ.get('LocalAppData')):
        if not base:
            continue
        p = os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
        if os.path.exists(p):
            return p
    return None


def _html_to_pdf(html):
    """Render an HTML string to PDF bytes via headless Edge. Raises on failure."""
    import os, tempfile, subprocess, shutil
    edge = _find_edge()
    if not edge:
        raise RuntimeError('Microsoft Edge was not found, so the PDF export is unavailable.')
    tmp = tempfile.mkdtemp(prefix='ai_report_')
    try:
        html_path = os.path.join(tmp, 'report.html')
        pdf_path = os.path.join(tmp, 'report.pdf')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        url = 'file:///' + html_path.replace('\\', '/')
        subprocess.run([edge, '--headless', '--disable-gpu', '--no-first-run',
                        '--user-data-dir=' + os.path.join(tmp, 'profile'),
                        '--print-to-pdf=' + pdf_path, url],
                       timeout=90, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(pdf_path):
            raise RuntimeError('Edge produced no PDF (rendering failed).')
        with open(pdf_path, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.route('/watchlist/ai/report')
def watchlist_ai_report():
    account = request.args.get('account', '').strip()
    r, ctx = _load_report_ctx(account)
    if not r:
        flash('No AI plan to report on — generate one first.', 'error')
        return redirect(url_for('watchlist', ai_account=account))
    return render_template('ai_report.html', r=r, **ctx)


@app.route('/watchlist/ai/report.pdf')
def watchlist_ai_report_pdf():
    account = request.args.get('account', '').strip()
    r, ctx = _load_report_ctx(account)
    if not r:
        flash('No AI plan to report on — generate one first.', 'error')
        return redirect(url_for('watchlist', ai_account=account))
    html = render_template('ai_report.html', r=r, **ctx)
    try:
        pdf = _html_to_pdf(html)
    except Exception as e:
        flash(f'PDF export failed: {e}', 'error')
        return redirect(url_for('watchlist_ai_report', account=account))
    slug = account.lower().replace(' ', '_').replace('-', '_') or 'account'
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename=ai_rebalance_{slug}.pdf'})


@app.route('/holdings/region', methods=['POST'])
def set_region_override():
    """Set or clear a manual region override for a ticker (used by the Holdings
    Region column and the Rebalancer Country view). Region '' / 'Auto' clears it."""
    import json
    from models import db, Setting
    from calculations import _ALL_REGIONS
    ticker = request.form.get('ticker', '').strip().upper()
    region = request.form.get('region', '').strip()
    if not ticker:
        return ('missing ticker', 400)
    s = Setting.query.get('region_overrides')
    data = {}
    if s and s.value:
        try:
            data = json.loads(s.value)
        except Exception:
            data = {}
    if ':' in region:
        # Weighted split, e.g. "USA:55, International:33, Emerging:12" (for multi-region funds).
        wd = {}
        for part in region.split(','):
            k, _, v = part.partition(':')
            k = k.strip()
            try:
                v = float(v.strip())
            except Exception:
                continue
            if k in _ALL_REGIONS and v > 0:
                wd[k] = v
        if len(wd) >= 2:
            data[ticker] = wd
        elif len(wd) == 1:
            data[ticker] = next(iter(wd))   # one region → store as plain single
        else:
            data.pop(ticker, None)
    elif region in _ALL_REGIONS:
        data[ticker] = region
    else:
        data.pop(ticker, None)   # Auto / unknown → drop the override
    val = json.dumps(data)
    if s:
        s.value = val
    else:
        db.session.add(Setting(key='region_overrides', value=val))
    db.session.commit()
    return ('', 204)


@app.route('/ticker/<ticker>/review')
def ticker_review(ticker):
    """Quick fundamentals + a business-summary blurb for a candidate, shown
    before it's added to the watchlist."""
    from price_service import get_holdings_metadata, get_cached_price, refresh_prices
    from calculations import _fmt_mktcap, get_holdings
    import yfinance as yf
    ticker = ticker.strip().upper()
    m = get_holdings_metadata([ticker]).get(ticker, {})
    cached = get_cached_price(ticker)
    if not cached:
        refresh_prices([ticker])
        cached = get_cached_price(ticker)
    summary = ''
    try:
        info = yf.Ticker(ticker).info or {}
        summary = (info.get('longBusinessSummary') or info.get('description') or '')[:500]
    except Exception:
        pass
    price = cached.price if cached else None
    dr, dy = m.get('dividend_rate'), m.get('dividend_yield')
    yld = (dr / price * 100) if (dr and price) else (dy if dy else None)
    owned = any(h['ticker'] == ticker for h in get_holdings())
    return jsonify({
        'ticker': ticker, 'name': m.get('long_name') or ticker,
        'asset_type': m.get('asset_type'), 'sector': m.get('sector'),
        'market_cap': _fmt_mktcap(m.get('market_cap')),
        'beta': round(m['beta'], 2) if m.get('beta') is not None else None,
        'yield': round(yld, 2) if yld is not None else None,
        'price': round(price, 2) if price else None,
        'currency': cached.currency if cached else None,
        'summary': summary, 'owned': owned,
        'in_wl': WatchlistItem.query.filter_by(ticker=ticker).first() is not None,
    })


@app.route('/watchlist/add', methods=['POST'])
def watchlist_add():
    from price_service import get_cached_price, refresh_prices, get_holdings_metadata
    from datetime import date
    _aia = request.form.get('ai_account', '').strip()
    dest = url_for('watchlist', ai_account=_aia) if _aia else url_for('watchlist')
    try:
        ticker = request.form['ticker'].strip().upper()
        if not ticker:
            raise ValueError('Ticker is required.')
        if WatchlistItem.query.filter_by(ticker=ticker).first():
            flash(f'{ticker} is already on the watchlist.', 'info')
            return redirect(dest)
        cached = get_cached_price(ticker)
        if not cached:
            refresh_prices([ticker])
            cached = get_cached_price(ticker)
        # Auto-classify: fill any blank fields from cached metadata.
        meta = get_holdings_metadata([ticker]).get(ticker, {})
        target_raw = request.form.get('target_price', '').strip()
        db.session.add(WatchlistItem(
            ticker=ticker,
            company=request.form.get('company', '').strip() or meta.get('long_name') or '',
            sector=request.form.get('sector', '').strip() or meta.get('sector') or '',
            currency=request.form.get('currency', '').strip() or 'CAD',
            target_price=float(target_raw) if target_raw else None,
            target_type=request.form.get('target_type', 'below'),
            added_price=cached.price if cached else None,
            added_date=date.today(),
            notes=request.form.get('notes', '').strip(),
        ))
        db.session.commit()
        flash(f'Added {ticker} to watchlist.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(dest)


@app.route('/watchlist/edit/<int:id>', methods=['POST'])
def watchlist_edit(id):
    item = WatchlistItem.query.get_or_404(id)
    try:
        target_raw = request.form.get('target_price', '').strip()
        item.target_price = float(target_raw) if target_raw else None
        item.target_type = request.form.get('target_type', item.target_type or 'below')
        item.notes = request.form.get('notes', '').strip()
        db.session.commit()
        flash(f'Updated {item.ticker}.', 'success')
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
    account = request.args.get('account', '').strip()
    show = request.args.get('show', '')
    data = get_gic_stats(account_filter=account or None, show_matured=(show == 'all'))
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('gics.html', data=data, last_updated=last_updated, active='gics')


@app.route('/gics/add', methods=['POST'])
def gics_add():
    try:
        start = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        maturity = datetime.strptime(request.form['maturity_date'], '%Y-%m-%d').date()
        account = request.form.get('account', '').strip()
        # Create the account on first use so GICs can go to a new custom account.
        if account and not Account.query.filter_by(name=account).first():
            db.session.add(Account(name=account, type='Non-Reg', cash_balance=0))
        db.session.add(GIC(
            name=request.form.get('name', '').strip(),
            institution=request.form.get('institution', '').strip(),
            account=account,
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
        # AI Rebalancer settings live in their own form (marker `ai_form`) so saving
        # them never blanks the main settings. API keys are only updated when a new
        # value is typed; a per-key "clear" checkbox wipes a stored key.
        if 'ai_form' in request.form:
            for key in ('ai_provider_default', 'ai_impl_style_default',
                        'ai_model_claude', 'ai_model_chatgpt', 'ai_preferences'):
                val = request.form.get(key, '').strip()
                s = Setting.query.get(key)
                if s:
                    s.value = val
                else:
                    db.session.add(Setting(key=key, value=val))
            for prov in ('anthropic_api_key', 'openai_api_key'):
                new_val = request.form.get(prov, '').strip()
                clear = request.form.get('clear_' + prov) == 'on'
                s = Setting.query.get(prov)
                if clear:
                    if s:
                        s.value = ''
                elif new_val:  # empty + not clearing → keep the existing key
                    if s:
                        s.value = new_val
                    else:
                        db.session.add(Setting(key=prov, value=new_val))
            db.session.commit()
            flash('AI settings saved.', 'success')
            return redirect(url_for('settings'))

        keys = [
            'fx_manual', 'fx_manual_rate', 'price_refresh_mins',
            'room_method', 'birth_year', 'tfsa_limit_overrides',
            'room_anchor_year', 'room_anchor_tfsa', 'room_anchor_fhsa',
        ]
        for key in keys:
            val = request.form.get(key, '').strip()
            s = Setting.query.get(key)
            if s:
                s.value = val
            else:
                db.session.add(Setting(key=key, value=val))

        # RDSP stress-test equity-exposure table (safe % per drawdown preset).
        import json
        eq = {}
        for preset in ('Safe', 'Low', 'Target', 'Growth', 'Aggressive', 'Current'):
            v = request.form.get(f'eq_{preset}', '').strip()
            if v != '':
                try:
                    eq[preset] = max(0.0, min(float(v), 100.0))
                except ValueError:
                    pass
        if eq:
            em = Setting.query.get('rdsp_equity_map')
            if em:
                em.value = json.dumps(eq)
            else:
                db.session.add(Setting(key='rdsp_equity_map', value=json.dumps(eq)))

        # Mutual funds to show by ticker instead of their friendly name.
        show_ticker = ','.join(t.strip().upper() for t in request.form.getlist('fund_show_ticker') if t.strip())
        sft = Setting.query.get('fund_show_ticker')
        if sft:
            sft.value = show_ticker
        else:
            db.session.add(Setting(key='fund_show_ticker', value=show_ticker))

        db.session.commit()
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    def gs(key, default=''):
        s = Setting.query.get(key)
        return s.value if s else default

    # Self-triggering reminder: flag when CRA's limit for the current year isn't
    # built in yet and hasn't been overridden, so the room math is just guessing.
    from datetime import date
    from calculations import TFSA_ANNUAL_LIMITS, _parse_tfsa_overrides, _tfsa_limit
    cy = date.today().year
    overrides = _parse_tfsa_overrides(gs('tfsa_limit_overrides', ''))
    tfsa_limit_known = (cy in TFSA_ANNUAL_LIMITS) or (cy in overrides)

    # Mutual-fund display preferences (friendly name vs ticker).
    import json as _json
    _show_set = {t.strip().upper() for t in gs('fund_show_ticker', '').split(',') if t.strip()}
    fund_tickers = []
    for pc in PriceCache.query.all():
        if not pc.meta_json:
            continue
        try:
            _m = _json.loads(pc.meta_json)
        except Exception:
            continue
        if _m.get('asset_type') == 'Mutual Fund' and _m.get('long_name'):
            fund_tickers.append({'ticker': pc.ticker, 'name': _clean_fund_name(_m['long_name']),
                                 'show_ticker': pc.ticker.upper() in _show_set})
    fund_tickers.sort(key=lambda x: x['ticker'])

    from calculations import managed_account_names, managed_fully_separate
    return render_template('settings.html',
                           fund_tickers=fund_tickers,
                           fx_rate=gs('fx_usd_cad', '1.365'),
                           fx_manual=gs('fx_manual', '0'),
                           fx_manual_rate=gs('fx_manual_rate', ''),
                           price_refresh_mins=gs('price_refresh_mins', '5'),
                           room_method=gs('room_method', 'reconstruct'),
                           birth_year=gs('birth_year', ''),
                           tfsa_limit_overrides=gs('tfsa_limit_overrides', ''),
                           current_year=cy,
                           current_tfsa_limit=_tfsa_limit(cy, overrides),
                           tfsa_limit_known=tfsa_limit_known,
                           tfsa_builtin_through=max(TFSA_ANNUAL_LIMITS),
                           room_anchor_year=gs('room_anchor_year', ''),
                           room_anchor_tfsa=gs('room_anchor_tfsa', ''),
                           room_anchor_fhsa=gs('room_anchor_fhsa', ''),
                           rdsp_equity_map=__import__('rdsp_view').equity_safe_map(),
                           ai_provider_default=gs('ai_provider_default', 'claude'),
                           ai_impl_style_default=gs('ai_impl_style_default', 'mixed'),
                           ai_model_claude=gs('ai_model_claude', ''),
                           ai_model_chatgpt=gs('ai_model_chatgpt', ''),
                           ai_preferences=gs('ai_preferences', ''),
                           ai_claude_key_set=bool(gs('anthropic_api_key', '')),
                           ai_chatgpt_key_set=bool(gs('openai_api_key', '')),
                           active='settings')


@app.route('/export/transactions.csv')
def export_transactions():
    import csv, io
    from flask import Response
    cols = ['date', 'type', 'subtype', 'ticker', 'account', 'account_type', 'qty', 'price',
            'currency', 'amount_native', 'amount_cad', 'fees_cad', 'net_cad', 'notes']
    acct_types = {a.name: (a.type or '') for a in Account.query.all()}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for t in Transaction.query.order_by(Transaction.date.asc(), Transaction.id.asc()).all():
        writer.writerow([
            t.date.isoformat() if t.date else '', t.type, t.subtype or '', t.ticker,
            t.account, acct_types.get(t.account, ''), t.qty, t.price, t.currency,
            t.amount_native, t.amount_cad, t.fees_cad, t.net_cad,
            (t.notes or '').replace('\n', ' '),
        ])
    stamp = datetime.now().strftime('%Y%m%d')
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=transactions_{stamp}.csv'})


@app.route('/backup/db')
def backup_db():
    from flask import send_file
    path = resolve_db_path()
    if not path or not os.path.exists(path):
        flash('Could not locate the database file.', 'error')
        return redirect(url_for('settings'))
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    return send_file(path, as_attachment=True, download_name=f'finance_backup_{stamp}.db')


@app.route('/settings/restore', methods=['POST'])
def restore_database():
    import sqlite3, tempfile
    f = request.files.get('backup')
    if not f or not f.filename:
        flash('Choose a backup .db file to restore.', 'error')
        return redirect(url_for('settings'))
    data = f.read()
    if not data.startswith(b'SQLite format 3\x00'):
        flash('That file is not a SQLite database.', 'error')
        return redirect(url_for('settings'))

    # Validate it looks like one of our backups (has a transactions table).
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    try:
        tmp.write(data)
        tmp.close()
        con = sqlite3.connect(tmp.name)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    if 'transactions' not in tables:
        flash("That database doesn't look like a Portfolio Tracker backup.", 'error')
        return redirect(url_for('settings'))

    path = resolve_db_path()
    if not path:
        flash('Could not locate the database file.', 'error')
        return redirect(url_for('settings'))
    try:
        db.session.remove()
        db.engine.dispose()              # release the file handle before overwriting
        with open(path, 'wb') as out:
            out.write(data)
        run_migrations()                 # bring an older backup up to the current schema
        flash('Database restored from backup.', 'success')
    except Exception as e:
        flash(f'Restore failed: {e}', 'error')
    return redirect(url_for('settings'))


def _wipe_all_data():
    """Delete every row from every table (first-run state). Caller commits."""
    for model in (Transaction, GIC, WatchlistItem, TickerMap,
                  PortfolioSnapshot, PriceCache, Account, Setting):
        model.query.delete()


@app.route('/settings/reset', methods=['POST'])
def reset_database():
    # Full wipe back to first-run state. Destructive; guarded by JS confirms.
    _wipe_all_data()
    db.session.commit()
    # Re-seed the FX default so prices/FX keep working after the wipe.
    db.session.add(Setting(key='fx_usd_cad', value='1.365'))
    db.session.commit()
    flash('Database reset — all data deleted. Starting fresh.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/clean-accounts', methods=['POST'])
def clean_accounts():
    # Remove account rows that carry no data — typically left over from tests.
    # An account is "in use" if it has a transaction, holds a GIC, or has a
    # manually-set cash balance; those are kept so they don't lose type/horizon.
    used = _used_account_names()
    gic_accounts = {g.account for g in GIC.query.with_entities(GIC.account).all() if g.account}
    removed = []
    for a in Account.query.order_by(Account.name).all():
        if a.name in used or a.name in gic_accounts or (a.cash_balance or 0):
            continue
        removed.append(a.name)
        db.session.delete(a)
    db.session.commit()
    if removed:
        flash(f'Removed {len(removed)} empty account(s): {", ".join(removed)}.', 'success')
    else:
        flash('No empty accounts to remove.', 'info')
    return redirect(url_for('settings'))


@app.route('/settings/load-sample', methods=['POST'])
def load_sample_data():
    # Replace all data with the demo portfolio. Destructive; guarded by JS confirm.
    from generate_sample_data import build_sample_data
    try:
        _wipe_all_data()
        db.session.commit()
        build_sample_data()  # seeds accounts, transactions, GICs, watchlist, FX
        flash('Sample data loaded — 5 accounts, ~5 years of transactions, GICs and a '
              'watchlist. Prices populate on the next refresh.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not load sample data: {e}', 'error')
    return redirect(url_for('settings'))


# ── Charts ────────────────────────────────────────────────────────────────────

@app.route('/charts')
def charts():
    from charts import catalog_grouped
    # Only offer accounts that actually have transactions — empty/stale accounts
    # shouldn't clutter the scope dropdown.
    from calculations import managed_account_names
    used = _used_account_names()
    accounts = [a.name for a in Account.query.order_by(Account.name).all()
                if a.name in used]
    return render_template('charts.html', active='charts',
                           groups=catalog_grouped(), accounts=accounts,
                           has_managed=bool(managed_account_names()))


# ── RDSP ──────────────────────────────────────────────────────────────────────

def _rdsp_args():
    a = request.args
    return dict(return_label=a.get('return', 'Target'), contribute_until_year=a.get('until', type=int),
                mode=a.get('mode', 'ldap'), wd_start=a.get('wd_start', type=int),
                wd_lumps=a.get('lumps'), wd_target=a.get('target'), wd_to_age=a.get('to_age', type=int),
                draw_label=a.get('draw', 'Target'), bequest=a.get('bequest'), tax_rate=a.get('tax_rate'),
                draw_style=a.get('draw_style', 'flat'), glide_start_age=a.get('g_start'),
                glide_length=a.get('g_len'), glide_target=a.get('g_target'),
                glide_safe_return=a.get('g_safe'), glide_current=a.get('g_current'),
                stress_shape=a.get('s_shape', 'crash'), stress_timing=a.get('s_when'),
                stress_severity=a.get('s_depth'), stress_decade_len=a.get('s_dlen'),
                gl_stock=a.get('gl_stock'), gl_safe=a.get('gl_safe'), gl_flatmix=a.get('gl_flatmix'))


@app.route('/rdsp')
def rdsp_tab():
    from rdsp_view import get_rdsp_view
    return render_template('rdsp.html', active='rdsp', view=get_rdsp_view(**_rdsp_args()))


@app.route('/rdsp/data')
def rdsp_data():
    from rdsp_view import get_rdsp_view
    return jsonify(get_rdsp_view(**_rdsp_args()))


@app.route('/rdsp/glide-lab')
def rdsp_glide_lab():
    # Lazy: the allocation-based Glide Lab is heavier than the rest of the tab, so the
    # section fetches it on demand. `full=1` adds the heavy dial + scenario grid (a
    # deferred second fetch) so the core view stays snappy.
    from rdsp_view import get_rdsp_view
    full = request.args.get('full') == '1'
    view = get_rdsp_view(**_rdsp_args(), include_glide_lab=True, gl_full=full)
    return jsonify(view.get('glide_lab') or {})


@app.route('/rdsp/save', methods=['POST'])
def rdsp_save():
    # Persist the family-income input and any edited future-year plan rows.
    income = request.form.get('family_income', '').strip()
    s = Setting.query.get('rdsp_family_income')
    if s:
        s.value = income
    else:
        db.session.add(Setting(key='rdsp_family_income', value=income))

    def num(name):
        v = request.form.get(name, '').strip()
        try:
            return float(v) if v != '' else None
        except ValueError:
            return None

    for year in request.form.getlist('year', type=int):
        row = RDSPPlanYear.query.get(year)
        if not row:
            row = RDSPPlanYear(year=year)
            db.session.add(row)
        row.contribution = num(f'contribution_{year}') or 0
        row.grant = num(f'grant_{year}')   # blank → None → engine computes
        row.bond = num(f'bond_{year}')
    db.session.commit()
    flash('RDSP plan saved.', 'success')
    return redirect(url_for('rdsp_tab', **{'return': request.form.get('return', 'Target')}))


@app.route('/charts/data/<chart_id>')
def chart_data(chart_id):
    from charts import build_chart
    account = request.args.get('account', '').strip() or None
    return jsonify(build_chart(chart_id, account))


# ── Tax & ACB ─────────────────────────────────────────────────────────────────

@app.route('/tax')
def tax():
    from calculations import get_tax_summary, managed_account_names
    year = request.args.get('year', type=int)
    data = get_tax_summary(year)
    managed = sorted(managed_account_names())
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('tax.html', **data, managed_accounts_excluded=managed,
                           last_updated=last_updated, active='tax')


@app.route('/tax/rates', methods=['POST'])
def tax_rates():
    from models import Setting
    def _save(key, val):
        s = Setting.query.get(key)
        if s:
            s.value = str(val)
        else:
            db.session.add(Setting(key=key, value=str(val)))
    try:
        inclusion = float(request.form.get('inclusion', 50)) / 100
        marginal = float(request.form.get('marginal', 25)) / 100
        _save('tax_inclusion_rate', inclusion)
        _save('tax_marginal_rate', marginal)
        db.session.commit()
        flash('Tax rates updated.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    year = request.form.get('year', type=int)
    return redirect(url_for('tax', year=year) if year else url_for('tax'))


# ── Rebalancer ────────────────────────────────────────────────────────────────

@app.route('/rebalancer')
def rebalancer():
    from calculations import get_rebalancer_data
    account = request.args.get('account', '').strip() or None
    dimension = request.args.get('dimension', 'sector').strip()
    mode = request.args.get('mode', 'cash').strip()
    whole_shares = request.args.get('shares', '').strip() == 'whole'
    deploy_raw = request.args.get('deploy', '').strip()
    try:
        deploy_cash = float(deploy_raw) if deploy_raw else None
    except ValueError:
        deploy_cash = None
    view = request.args.get('view', '').strip()
    # `seed` ("Bucket:pct,Bucket:pct") pre-fills targets without saving — the RDSP
    # glide-path hand-off uses it to seed a Blended-Risk split for review.
    seed = request.args.get('seed', '').strip()
    targets_override = None
    if seed:
        ov = {}
        for part in seed.split(','):
            b, _, p = part.partition(':')
            try:
                if b.strip() and p.strip():
                    ov[b.strip()] = float(p)
            except ValueError:
                pass
        targets_override = ov or None
    data = get_rebalancer_data(account=account, dimension=dimension, mode=mode,
                               deploy_cash=deploy_cash, targets_override=targets_override,
                               whole_shares=whole_shares)
    # Overall mode is the default landing view (like the Accounts overview);
    # picking an account chip switches to that account's rebalancer.
    overall_mode = (view == 'overall') or (account is None)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('rebalancer.html', data=data, overall_mode=overall_mode,
                           last_updated=last_updated, active='rebalancer')


@app.route('/rebalancer/targets', methods=['POST'])
def rebalancer_targets():
    from calculations import save_rebal_targets, REBAL_DIMENSIONS, _known_buckets
    account = request.form.get('account', '').strip()
    dimension = request.form.get('dimension', 'sector').strip()
    if not account or dimension not in REBAL_DIMENSIONS:
        flash('Could not save targets: missing account or dimension.', 'error')
        return redirect(url_for('rebalancer'))
    # Buckets come in as target_<bucket> fields; collect any with a value.
    targets = {}
    for b in _known_buckets(dimension):
        raw = request.form.get('target_' + b, '').strip()
        if raw:
            targets[b] = raw
    save_rebal_targets(account, dimension, targets)
    total = sum(float(v) for v in targets.values()) if targets else 0
    if abs(total - 100) > 0.5 and targets:
        flash(f'Targets saved — note they sum to {total:.0f}%, not 100%.', 'info')
    else:
        flash('Targets saved.', 'success')
    return redirect(url_for('rebalancer', account=account, dimension=dimension,
                            mode=request.form.get('mode', 'cash'),
                            shares=request.form.get('shares', 'frac')))


# ── Performance ───────────────────────────────────────────────────────────────

@app.route('/performance')
def performance():
    from calculations import managed_account_names, managed_included_in
    accounts = [r[0] for r in Transaction.query.with_entities(Transaction.account).distinct().all()]
    accounts.sort()
    has_managed = bool(managed_account_names())
    managed_default = managed_included_in('performance')  # check-bar initial state
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('performance.html', accounts=accounts, has_managed=has_managed,
                           managed_default=managed_default,
                           last_updated=last_updated, active='performance')


@app.route('/performance/data')
def performance_data():
    from calculations import get_performance_series
    scope = request.args.get('scope', 'portfolio')
    return jsonify(get_performance_series(scope))


@app.route('/performance/snapshot', methods=['POST'])
def performance_snapshot():
    from calculations import take_portfolio_snapshot
    taken = take_portfolio_snapshot()
    flash('Portfolio snapshot saved.' if taken else 'Snapshot already taken today.', 'success' if taken else 'info')
    return redirect(url_for('performance'))


def _parse_asof_args():
    from datetime import date
    raw = (request.args.get('date') or '').strip()
    try:
        as_of = datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        as_of = date.today()
    return as_of, (request.args.get('scope') or 'portfolio').strip()


@app.route('/performance/asof')
def performance_asof():
    from calculations import get_snapshot_at
    as_of, scope = _parse_asof_args()
    return jsonify(get_snapshot_at(as_of, scope))


@app.route('/performance/asof.csv')
def performance_asof_csv():
    import csv, io
    from flask import Response
    from calculations import get_snapshot_at
    as_of, scope = _parse_asof_args()
    snap = get_snapshot_at(as_of, scope)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Snapshot as of', snap['as_of']])
    w.writerow(['Scope', snap['scope']])
    w.writerow([])
    w.writerow(['Total value (CAD)', f"{snap['total_value']:.2f}"])
    w.writerow(['Holdings market value (CAD)', f"{snap['holdings_mv']:.2f}"])
    w.writerow(['Cash (CAD)', f"{snap['cash']:.2f}"])
    w.writerow(['GICs (CAD)', f"{snap['gic_value']:.2f}"])
    w.writerow(['Book value (CAD)', f"{snap['book_value']:.2f}"])
    w.writerow(['Unrealized G/L (CAD)', f"{snap['unrealized_gl']:.2f}"])
    w.writerow(['Contributions to date (CAD)', f"{snap['contributions']:.2f}"])
    w.writerow(['Holdings count', snap['num_holdings']])
    if snap.get('unpriced'):
        w.writerow(['Unpriced (no historical price)', ', '.join(snap['unpriced'])])
    w.writerow([])
    w.writerow(['Ticker', 'Currency', 'Qty', 'Price (native)',
                'Market value (CAD)', 'Book (CAD)', 'Unrealized G/L (CAD)'])
    for h in snap['holdings']:
        w.writerow([h['ticker'], h['currency'], f"{h['qty']:g}",
                    '' if h['price'] is None else f"{h['price']:.4f}",
                    f"{h['market_value_cad']:.2f}", f"{h['book_value_cad']:.2f}",
                    '' if h['unrealized_gl'] is None else f"{h['unrealized_gl']:.2f}"])

    scope_slug = (snap['scope'] or 'portfolio').replace(' ', '_').replace('/', '-')
    fname = f"snapshot_{snap['as_of']}_{scope_slug}.csv"
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


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
    from calculations import (get_projections, run_monte_carlo, get_planning_stats,
                              get_holdings, get_dashboard_stats)
    holdings = get_holdings()
    stats = get_dashboard_stats(holdings)
    current_value = stats['total_portfolio']
    monthly_contrib = request.args.get('monthly_contrib', 500.0, type=float)
    years = min(50, max(1, request.args.get('years', 25, type=int)))
    mean_return = request.args.get('mean_return', 7.0, type=float)
    std_dev = request.args.get('std_dev', 15.0, type=float)
    target = request.args.get('target', 1000000.0, type=float)
    inflation = request.args.get('inflation', 2.5, type=float)
    div_growth = request.args.get('div_growth', 5.0, type=float)

    proj = get_projections(current_value, monthly_contrib, years)
    mc = run_monte_carlo(current_value, monthly_contrib, years, mean_return / 100, std_dev / 100)
    planning = get_planning_stats(current_value, monthly_contrib, years, mean_return / 100,
                                  inflation / 100, target, div_growth / 100)
    last_updated = PriceCache.query.order_by(PriceCache.last_updated.desc()).first()
    return render_template('projections.html',
                           proj=proj, mc=mc, planning=planning, current_value=current_value,
                           monthly_contrib=monthly_contrib, years=years,
                           mean_return=mean_return, std_dev=std_dev,
                           target=target, inflation=inflation, div_growth=div_growth,
                           last_updated=last_updated, active='projections')


# Monte Carlo merged into Projections — keep the URL working.
@app.route('/montecarlo')
def montecarlo():
    return redirect(url_for('projections', **request.args))


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
    # Debug off by default; opt in locally with FLASK_DEBUG=1. The Werkzeug
    # debugger allows code execution, so never enable it on an exposed host.
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
