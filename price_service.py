import threading
import time
import os
import json
from datetime import datetime

import yfinance as yf


# yfinance fund sector keys → display labels (aligned with equity .info sectors)
_FUND_SECTOR_LABELS = {
    'realestate': 'Real Estate',
    'consumer_cyclical': 'Consumer Cyclical',
    'basic_materials': 'Basic Materials',
    'consumer_defensive': 'Consumer Defensive',
    'technology': 'Technology',
    'communication_services': 'Communication Services',
    'financial_services': 'Financial Services',
    'utilities': 'Utilities',
    'industrials': 'Industrials',
    'energy': 'Energy',
    'healthcare': 'Healthcare',
}


def _fetch_one_metadata(ticker):
    """Fetch classification metadata for a single ticker (asset type, sector,
    market cap, and ETF look-through sector/asset-class weightings)."""
    meta = {'asset_type': None, 'sector': None, 'market_cap': None,
            'fund_sectors': None, 'fund_assets': None, 'beta': None, 'long_name': None}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        qt = (info.get('quoteType') or '').upper()
        meta['asset_type'] = 'ETF' if qt == 'ETF' else ('Equity' if qt in ('EQUITY', '') else qt.title())
        meta['sector'] = info.get('sector')
        meta['long_name'] = info.get('longName') or info.get('shortName')
        mc = info.get('marketCap')
        meta['market_cap'] = float(mc) if mc else None
        # Beta (market-relative volatility) for risk targeting; ETFs often only
        # report beta3Year.
        b = info.get('beta') or info.get('beta3Year')
        meta['beta'] = float(b) if b else None
        # Forward annual dividend per share (ticker currency) + yield (%), with
        # fallbacks for ETFs that don't report dividendRate.
        dr = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
        meta['dividend_rate'] = float(dr) if dr else None
        dy = info.get('dividendYield')
        meta['dividend_yield'] = float(dy) if dy else None
        if qt == 'ETF':
            try:
                fd = tk.funds_data
                sw = fd.sector_weightings or {}
                meta['fund_sectors'] = {
                    _FUND_SECTOR_LABELS.get(k, k.replace('_', ' ').title()): float(v)
                    for k, v in sw.items() if v
                }
                ac = fd.asset_classes or {}
                meta['fund_assets'] = {k: float(v) for k, v in ac.items() if v}
            except Exception:
                pass
    except Exception:
        pass
    return meta


def get_holdings_metadata(tickers, force=False):
    """Return {ticker: meta}. Reads from price_cache.meta_json; fetches and
    caches any missing (one-time, since classification rarely changes)."""
    from models import db, PriceCache
    result, to_fetch = {}, []
    for t in set(tickers):
        if not t or t == 'CASH' or ' ' in t:
            continue
        pc = PriceCache.query.get(t)
        if pc and pc.meta_json and not force:
            try:
                m = json.loads(pc.meta_json)
                if 'dividend_rate' in m and 'beta' in m and 'long_name' in m:  # re-fetch caches missing newer fields
                    result[t] = m
                    continue
            except Exception:
                pass
        to_fetch.append(t)

    for t in to_fetch:
        meta = _fetch_one_metadata(t)
        result[t] = meta
        pc = PriceCache.query.get(t)
        if pc:
            pc.meta_json = json.dumps(meta)
        else:
            db.session.add(PriceCache(ticker=t, meta_json=json.dumps(meta)))
    if to_fetch:
        db.session.commit()
    return result


def get_fx_rate():
    from models import Setting
    # Manual override takes priority
    manual = Setting.query.get('fx_manual')
    if manual and manual.value == '1':
        manual_rate = Setting.query.get('fx_manual_rate')
        if manual_rate and manual_rate.value:
            try:
                return float(manual_rate.value)
            except Exception:
                pass
    setting = Setting.query.get('fx_usd_cad')
    if setting:
        try:
            return float(setting.value)
        except Exception:
            pass
    return 1.365


def fetch_prices_batch(tickers):
    if not tickers:
        return {}

    all_tickers = list(set(tickers) | {'USDCAD=X'})
    results = {}

    try:
        data = yf.Tickers(' '.join(all_tickers))
        for ticker in all_tickers:
            try:
                info = data.tickers[ticker].fast_info
                price = getattr(info, 'last_price', None)
                prev_close = getattr(info, 'previous_close', None)
                currency = getattr(info, 'currency', 'CAD')
                if price:
                    results[ticker] = {
                        'price': float(price),
                        'prev_close': float(prev_close) if prev_close else float(price),
                        'currency': currency,
                    }
            except Exception:
                pass
    except Exception:
        pass

    return results


def refresh_prices(tickers):
    from models import db, PriceCache, Setting
    if not tickers:
        return

    price_data = fetch_prices_batch(tickers)
    now = datetime.utcnow()

    for ticker, data in price_data.items():
        cached = PriceCache.query.get(ticker)
        if cached:
            cached.price = data['price']
            cached.prev_close = data['prev_close']
            cached.currency = data['currency']
            cached.last_updated = now
        else:
            db.session.add(PriceCache(
                ticker=ticker,
                price=data['price'],
                prev_close=data['prev_close'],
                currency=data['currency'],
                last_updated=now,
            ))

    if 'USDCAD=X' in price_data:
        # Only update stored FX if not using manual override
        manual = Setting.query.get('fx_manual')
        if not (manual and manual.value == '1'):
            fx = price_data['USDCAD=X']['price']
            setting = Setting.query.get('fx_usd_cad')
            if setting:
                setting.value = str(fx)
            else:
                db.session.add(Setting(key='fx_usd_cad', value=str(fx)))

    db.session.commit()


def get_cached_price(ticker):
    from models import PriceCache
    return PriceCache.query.get(ticker)


def _check_auto_import(app):
    import json
    from models import Setting, db
    try:
        folder_setting = Setting.query.get('auto_import_folder')
        if not folder_setting or not folder_setting.value:
            return
        folder = folder_setting.value.strip()
        if not os.path.isdir(folder):
            return

        processed_setting = Setting.query.get('auto_import_processed')
        processed = set(json.loads(processed_setting.value)) if processed_setting and processed_setting.value else set()

        new_files = sorted(
            f for f in os.listdir(folder)
            if f.lower().endswith('.csv') and f not in processed
        )

        if not new_files:
            return

        from importers import parse_file_path
        for fname in new_files:
            fpath = os.path.join(folder, fname)
            try:
                count = parse_file_path(fpath)
                processed.add(fname)
                print(f'[auto-import] {fname}: {count} transactions imported')
            except Exception as e:
                print(f'[auto-import] {fname}: {e}')

        ps = Setting.query.get('auto_import_processed')
        if ps:
            ps.value = json.dumps(list(processed))
        else:
            db.session.add(Setting(key='auto_import_processed', value=json.dumps(list(processed))))
        db.session.commit()
    except Exception as e:
        print(f'[auto-import check] {e}')


def start_price_refresh(app):
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return

    def refresh_loop():
        time.sleep(5)
        while True:
            interval = 300  # default 5 min; overridden by the price_refresh_mins setting
            try:
                with app.app_context():
                    from models import Transaction, WatchlistItem, Setting
                    txn_tickers = [r[0] for r in Transaction.query.with_entities(Transaction.ticker).distinct()]
                    watch_tickers = [r[0] for r in WatchlistItem.query.with_entities(WatchlistItem.ticker).distinct() if r[0]]
                    all_tickers = list(set(txn_tickers + watch_tickers))
                    if all_tickers:
                        refresh_prices(all_tickers)
                    _check_auto_import(app)
                    s = Setting.query.get('price_refresh_mins')
                    if s and s.value:
                        try:
                            interval = max(60, int(round(float(s.value) * 60)))  # floor at 1 min
                        except (TypeError, ValueError):
                            pass
            except Exception as e:
                print(f'[price refresh] {e}')
            time.sleep(interval)

    thread = threading.Thread(target=refresh_loop, daemon=True)
    thread.start()
