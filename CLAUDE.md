# Portfolio Tracker

A personal finance web app that replaces an Excel investment tracker. The user only enters transactions and everything else — holdings, dashboard, accounts, dividends, G/L — auto-calculates from that.

## Stack

- **Backend**: Python 3.13 + Flask + Flask-SQLAlchemy
- **Database**: SQLite (`finance.db`, auto-created on first run)
- **Live prices**: `yfinance` (Yahoo Finance, free, no API key) — auto-refreshes every 5 min in a background thread
- **Frontend**: Vanilla HTML/CSS/JS + Chart.js (CDN). No React, no Tailwind. Custom "Midnight Terminal" dark theme.
- **CSV import**: `importers.py` parses TD Direct Investing and CIBC Investor's Edge exports

## How to run

```
python app.py
```
Or double-click `run.bat`. Open http://localhost:5000.

## Project structure

```
app.py            # Flask routes + app init + template filters
models.py         # SQLAlchemy models (Transaction, PriceCache, Account, Setting, GIC, WatchlistItem)
calculations.py   # Holdings calc from transactions, dashboard stats, account summary
price_service.py  # yfinance fetch + DB cache + background refresh thread
importers.py      # TD / CIBC CSV parsers
templates/
  base.html       # Sidebar nav + topbar layout
  dashboard.html  # Stat cards + donut chart + top holdings
  holdings.html   # Full holdings table with live prices
  transactions.html # Transaction log + add form
  accounts.html   # Per-account cards with mini holdings tables
  import.html     # CSV upload + deletable import log
static/
  css/style.css   # Midnight Terminal theme (CSS variables, no framework)
  js/app.js       # refreshPrices(), flash auto-dismiss
```

## Key architecture decisions

- **Holdings are never stored** — always calculated on the fly from the transactions table using the average cost method (Canadian ACB convention)
- **FX rate** (USD/CAD) is fetched from yfinance (`USDCAD=X`) and cached in the `settings` table
- **Price cache** lives in `price_cache` table; background thread refreshes every 5 min only in the reloader child process (`WERKZEUG_RUN_MAIN=true`) to avoid double-start in debug mode
- Delete redirects respect a `?next=` query param so deleting from the Import page stays on Import

## Accounts seeded on first run

TFSA, RRSP, FHSA, Non-Reg — created automatically if the DB is empty.

## What's built (Phase 1)

- Dashboard, Holdings, Transactions, Accounts, Import (TD + CIBC CSV)

## What's next (Phase 2+)

- Performance history (monthly snapshots)
- Dividends tracker
- GICs tracker
- Rebalancer (target vs actual allocation)
- Watchlist
- Monte Carlo simulation
- Projections / CAGR scenarios
- Tax & ACB (non-registered accounts)
- Settings page (FX rate, allocation targets, contribution room)
- Auto-import via folder watcher

## User context

- Canadian investor — accounts are TFSA, RRSP, FHSA, Non-Reg
- Brokers: TD Direct Investing, CIBC Investor's Edge
- Mix of CAD and USD holdings; all values displayed in CAD
- Theme must stay Midnight Terminal dark
