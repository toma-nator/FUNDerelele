from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    ticker = db.Column(db.String(20), nullable=False)
    account = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # Buy, Sell, Dividend
    qty = db.Column(db.Float, nullable=False, default=0)
    price = db.Column(db.Float, nullable=False, default=0)
    currency = db.Column(db.String(5), nullable=False, default='CAD')
    amount_native = db.Column(db.Float, default=0)
    amount_cad = db.Column(db.Float, default=0)
    fees_cad = db.Column(db.Float, default=0)
    net_cad = db.Column(db.Float, default=0)
    notes = db.Column(db.String(300), default='')
    subtype = db.Column(db.String(50), default='')   # e.g. Contribution, RDSP Grant, RDSP Bond
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PriceCache(db.Model):
    __tablename__ = 'price_cache'
    ticker = db.Column(db.String(20), primary_key=True)
    price = db.Column(db.Float)
    prev_close = db.Column(db.Float)
    currency = db.Column(db.String(5))
    last_updated = db.Column(db.DateTime)
    meta_json = db.Column(db.Text)  # cached classification: asset type, sector, market cap, ETF look-through


class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    type = db.Column(db.String(20))
    cash_balance = db.Column(db.Float, default=0)


class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(500))


class GIC(db.Model):
    __tablename__ = 'gics'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    institution = db.Column(db.String(100))
    account = db.Column(db.String(50))
    principal = db.Column(db.Float, default=0)
    rate = db.Column(db.Float, default=0)
    start_date = db.Column(db.Date)
    maturity_date = db.Column(db.Date)
    compounding = db.Column(db.String(20), default='Annual')


class WatchlistItem(db.Model):
    __tablename__ = 'watchlist'
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20))
    company = db.Column(db.String(100))
    sector = db.Column(db.String(50))
    currency = db.Column(db.String(5), default='CAD')
    target_price = db.Column(db.Float)
    target_type = db.Column(db.String(10), default='below')  # 'below' = buy target, 'above' = price objective
    added_price = db.Column(db.Float)
    added_date = db.Column(db.Date)
    notes = db.Column(db.String(300))


class PortfolioSnapshot(db.Model):
    __tablename__ = 'portfolio_snapshots'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    total_book = db.Column(db.Float, default=0)
    total_market = db.Column(db.Float, default=0)
    total_cash = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TickerMap(db.Model):
    __tablename__ = 'ticker_map'
    description = db.Column(db.String(100), primary_key=True)  # cleaned broker description
    ticker = db.Column(db.String(20), nullable=False)           # real yfinance ticker symbol
