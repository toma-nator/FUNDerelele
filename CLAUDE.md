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
app.py            # Flask routes + app init + lightweight column migrations + template filters
models.py         # SQLAlchemy models: Transaction (+recurring_id), PriceCache (+meta_json),
                  #   Account, Setting, GIC, WatchlistItem, PortfolioSnapshot, TickerMap, RecurringRule
calculations.py   # All derived data: holdings, dashboard, account summary + allocation
                  #   breakdowns, per-currency cash, tax/ACB, dividends, performance series,
                  #   GICs, rebalancer, projections, Monte Carlo, cash flows
price_service.py  # yfinance live prices + FX + classification/dividend metadata cache + bg thread
importers.py      # TD CSV, CIBC CSV, and TD PDF-statement parsers
recurring.py      # recurring/scheduled transactions: RecurringRule engine (generate_due) +
                  #   compute_amounts (shared amount/sign logic with the manual add route)
currency.py       # Currency Exchange (two-legged CAD↔USD cash transfer) + per-account FX reconcile
templates/        # one per tab: dashboard, holdings, transactions, accounts, performance,
                  #   dividends, cashflows, gics, rebalancer, watchlist, tax, projections,
                  #   montecarlo, settings, import (+ base.html layout)
static/
  css/style.css      # Midnight Terminal theme (CSS variables, no framework)
  js/app.js          # refreshPrices(), flash auto-dismiss
  js/chart_render.js # shared Chart.js renderer (CAD or percent via payload `unit`)
SUGGESTIONS.md    # Backlog of parked feature ideas (read before proposing new work)
```

## Key architecture decisions

- **Holdings are never stored** — always calculated on the fly from the transactions table using the average cost method (Canadian ACB convention). `get_holdings(include_closed=False)` excludes the `CASH` pseudo-ticker.
- **FX rate** (USD/CAD) is from yfinance (`USDCAD=X`), cached in `settings`.
- **Price cache** (`price_cache` table) refreshes every 5 min in the reloader child only (`WERKZEUG_RUN_MAIN=true`). The same table's `meta_json` column caches per-ticker **classification + dividend metadata** (asset type, sector, market cap, ETF sector/asset look-through, dividend rate/yield) fetched once from yfinance — used by the Accounts allocation breakdowns and Dividends forward income.
- **Ticker mapping** (`ticker_map` table): broker descriptions → real yfinance symbols; mapping the description on the Import page retro-updates existing transactions. Canadian CDRs use the `.NE` suffix (e.g. `AAPL.NE`); US holdings use plain symbols.
- **Importer correctness** (`importers.py`): handles TD corporate actions — ROC/CXLROC as cash, DISP cash-in-lieu (EXCH stays in-kind), CXLDIV/CXLWHTX02 as reversals, splits as ADD (TD records *new* shares), and withholding tax. Dedup is amount-aware.
- **Performance series** (`get_performance_series`): live, per-scope (portfolio or account), money-weighted CAD benchmarks (S&P 500 / NASDAQ / TSX), time-weighted return. Historical months use yfinance month-end closes; the **latest point uses live prices**. Valuation uses **unadjusted prices** (dividends tracked separately as cash) and scales pre-split months by future split ratios to avoid split jumps.
- **Tax tab** is registration-aware: registered account types (TFSA/RRSP/FHSA/RDSP/…) are tax-sheltered and excluded from taxable totals.
- **Per-currency cash** (`get_cash_by_account_currency`): cash is bucketed by (account, currency) in native dollars — each non-CAD row is converted from its stored `net_cad` back to native at the row's own booked rate (`amount_cad/amount_native`), so USD dividends/sells/buys land in the USD pool. `get_cash_by_account` then rolls the pools up to CAD-equivalent at **live FX** (so USD cash revalues). Accounts shows one cash line per currency present. No new column — derived on read.
- **Currency Exchange** (`currency.py`): a CAD↔USD transfer recorded as two net-zero `CASH` legs (one side must be CAD), so it shifts the per-currency split without touching net worth, contributions, dividends, or the performance series. `reconcile_account_fx` zeroes the negative foreign-cash artifact left by foreign buys funded by an unrecorded conversion (Accounts shows a **Reconcile** button on a negative foreign-cash card).
- **Recurring transactions** (`recurring.py`): a `RecurringRule` template repeats on a cadence; `generate_due()` materializes concrete rows up to today (idempotent, gated to the reloader child on startup + lazily on the Transactions page). `compute_amounts` is shared with the manual add route so they never diverge.
- Delete redirects respect a `?next=` query param.

## Accounts

Accounts are created **only by imports** (`_ensure_account`, default type `Non-Reg`) or by adding a transaction to a new account name — no placeholder accounts are seeded. Account type is editable on the Accounts page; zero-dollar accounts are hidden.

## What's built

- **Dashboard, Holdings, Transactions, Accounts, Import** (TD CSV/PDF + CIBC CSV)
- **Holdings**: sortable columns, currency/account/search filters, active/closed toggle
- **Transactions**: manual add (incl. Reinvest/DRIP, Currency Exchange), **recurring/scheduled** rules with a management list, bulk/filtered delete
- **Accounts**: overview ↔ single-account detail (full-width) with allocation breakdowns (asset type, sector w/ ETF look-through, market cap, currency, holdings-vs-cash, weights), **per-currency cash lines (CAD/USD) + Reconcile**, book value / contributions / grants / all-time gain / dividends / realized G/L
- **Performance**: line + monthly-Δ charts, ranges, scope, benchmarks (money-weighted), TWR + per-year returns, cash/%-return/avg-rate toggles
- **Dividends**: net-of-withholding, account scope, forward income & current yield
- **Charts** (`charts.py` + `chart_render.js`): selectable catalog in a 1/2/3/4-pane layout; renderer formats CAD or **percent** (via payload `unit`)
- **Tax & ACB** (registered-aware), GICs, Rebalancer, Cash Flows, Watchlist, Projections, Monte Carlo, Settings

## What's next

- See `SUGGESTIONS.md` for the parked backlog (e.g. asset-class look-through, max drawdown, target-rate line, dividend drill-down/projection, holdings-only return)
- Auto-import via folder watcher

## User context

- Canadian investor. The imported account is an **RDSP** (`59WBM0N`, type set to RDSP). Brokers: TD Direct Investing, CIBC Investor's Edge.
- Mix of CAD and USD holdings (incl. CAD-hedged CDRs); all values displayed in CAD.
- Has a **dividend-heavy friend** who will also use the app — dividend/withholding/forward-income correctness matters.
- Theme must stay Midnight Terminal dark; values a clean, uncluttered UI.
- See `memory/feedback-working-style.md` for how they like to work (tab-by-tab, review-first, commit after each).
