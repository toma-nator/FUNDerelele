import threading
import time
import os
from datetime import datetime

import yfinance as yf


def get_fx_rate(app_context=None):
    from models import Setting
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


def start_price_refresh(app):
    # In debug+reloader mode, only run in the reloader child process
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return

    def refresh_loop():
        time.sleep(5)  # Short initial delay so DB is ready
        while True:
            try:
                with app.app_context():
                    from models import Transaction
                    tickers = [
                        row[0]
                        for row in Transaction.query.with_entities(Transaction.ticker).distinct()
                    ]
                    if tickers:
                        refresh_prices(tickers)
            except Exception as e:
                print(f'[price refresh] {e}')
            time.sleep(300)  # 5 minutes

    thread = threading.Thread(target=refresh_loop, daemon=True)
    thread.start()
