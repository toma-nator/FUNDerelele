# Suggestions for Later

Ideas parked for future implementation. Not committed work — just a backlog.

## Asset-class look-through (ETFs)

Decompose each ETF's market value into its underlying **asset classes**
(stock / bond / cash / preferred / convertible / other), the same way the
**Sector** breakdown already does ETF look-through.

- **Data source:** already available from yfinance — `Ticker(t).funds_data.asset_classes`,
  e.g. VTI → `{stockPosition: 0.9922, bondPosition: 0.0, cashPosition: 0.0062, ...}`,
  MGK → `{stockPosition: 0.9965, ...}`. This is already fetched and cached in
  `price_cache.meta_json` as `fund_assets` (see `price_service._fetch_one_metadata`).
- **Where it'd go:** a new "By Asset Class" breakdown block in the account detail
  view, alongside Asset Type / Sector / Market Cap / Currency.
- **How to compute:** in `calculations.get_account_breakdown`, blend like sectors —
  for ETFs distribute market value across `fund_assets`; for individual equities
  assign 100% to "Stock". Normalize and return as another `to_list(...)` series.
- **Why it's useful:** shows true stock vs. bond vs. cash exposure across the whole
  account (a 100%-equity ETF and a bond ETF would otherwise both just read "ETF"
  under Asset Type).

_Effort: small — the data is already cached; it's one more aggregation dict in
`get_account_breakdown` and one more `breakdown-block` in `accounts.html`._

## Performance — Holdings-only return

A toggle to compute TWR / yearly returns on **holdings market value only**
(excluding the cash balance), treating buys/sells as the flows instead of
deposits. Isolates *investment performance* (price + dividends) from the
**cash drag** — i.e. "how good are my picks" vs. "how much did I keep
deployed." Pairs with the existing total-account TWR (which includes cash).

- **Where:** a toggle next to the cash option on the Performance tab.
- **Compute:** in `computeTWR` / `computeYearly`, use `market_value` as the base
  (instead of `market_value + cash`) and use the net buy/sell cash as the
  per-month flow rather than deposits.

## Dividends — Per-ticker payment drill-down

Click a ticker row in the "By Ticker" table to expand its individual dividend
payments (date, gross, withheld, net) — useful for spotting cuts, special
dividends, or reconciling against statements.

- **Compute:** already have every Dividend/WithholdingTax row; just group by
  ticker and render an expandable sub-row (or a small modal) on click.
- **Effort:** small — a hidden detail row toggled in `dividends.html`.

## Dividends — Income projection & growth

A forward 12-month income calendar (expected payment per month from current
holdings) plus a year-over-year dividend growth rate per ticker. Turns the tab
from "what I received" into "what I'll receive."

- **Data:** forward rate per share is already cached (`dividend_rate`); pair it
  with each holding's pay frequency/schedule (yfinance `dividends` history) to
  place expected payments on a calendar.
- **Growth:** compare each ticker's trailing-year net vs. the prior year.
- **Effort:** medium — needs a payment-schedule heuristic from dividend history.

## Performance — Max drawdown

Largest peak-to-trough decline over the selected range, as a stat card (and
optionally a shaded region on the chart). Good risk context next to TWR.

- **Compute:** over the value series, track the running peak; drawdown at each
  point = (value − peak) / peak; max drawdown = the most negative. Compute in
  the Performance JS from the already-loaded series (respects scope/range/cash).
- **Where:** another stat card beside "Annualized TWR", e.g. "Max Drawdown −18%".

## Performance — Target rate line

A user-set target annual return (e.g. 7%) drawn as a reference line on the
Performance chart, so actual vs. goal is visible at a glance.

- **Setting:** store the target in the `settings` table (e.g. `target_return`),
  editable on the Settings page.
- **Line:** in `%` mode, a straight/compounding line from the range start at the
  target rate; in `$` mode, grow the starting value at the target rate. Pairs
  naturally with the existing "Avg rate" line.
- **Effort:** small-to-medium — a setting + input on Settings, and one more
  dataset in the Performance render.

## Cash Flows — Matching / "free money" ratio stat

A headline stat card showing the RDSP efficiency number: free government money
(grant + bond) earned per $1 of self-contribution — e.g. "$1.50 grant+bond per
$1 contributed". Complements the composition doughnut's "% free money" by
framing it as a return on contributions.

- **Compute:** `(grant_total + bond_total) / contribution_total` — both already
  available in `get_cashflow_stats` (`by_subtype`/`free_money`); just add the
  ratio to the return dict and a stat card. Respects the active account filter.
- **Effort:** tiny — one division and one stat card.

## Cash Flows — Cumulative growth line

A cumulative-deposits line chart (running total of contributions over time),
optionally toggling with or sitting beside the annual bars, to show the
account's funding building up rather than just per-year amounts.

- **Compute:** sort deposits ascending and accumulate `net_cad`; could also
  stack cumulative by subtype to show contribution vs. grant vs. bond growth.
- **Where:** a toggle on the Annual Cash Flows chart, or a second small chart.
- **Effort:** small — a running-sum series in `get_cashflow_stats` and a line
  dataset in `cashflows.html`.
