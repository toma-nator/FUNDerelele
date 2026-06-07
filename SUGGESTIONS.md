# Suggestions for Later

Ideas parked for future implementation. Not committed work — just a backlog.

## Charts tab — deferred charts (🟡/🔴)

The Charts tab (`charts.py` + `templates/charts.html`) ships the 🟢 catalog. These
were parked per the build plan; add a builder in `charts.py` + a `CHART_CATALOG`
entry to enable any of them:

- **⭐ HIGH VALUE — percent-format support in the renderer.** The chart renderer
  currently formats every value as CAD (axis ticks + tooltip). Add an optional
  `unit: 'percent'` (or `'pct'`) field to a chart's payload and have
  `buildConfig()` in `charts.html` switch the axis/tooltip formatter accordingly.
  This is a small, one-time change that **unlocks a whole class of charts for
  near-free**: yield-on-cost & current yield by holding, holding/sector return %,
  allocation drift %, monthly/cumulative TWR %, dividend-growth %, etc. Do this
  first — it's the cheapest high-leverage win in the backlog.

- **Asset Class look-through** (stock/bond/cash) — 🟡 `fund_assets` is cached; see
  the "Asset-class look-through (ETFs)" item below. (Distinct from the shipped
  "By Asset Type" chart, which is equity/ETF/etc.)
- **Geographic Exposure** — 🔴 yfinance gives `country` only for individual
  stocks; ETFs have no country look-through, so this would be mostly "Mixed".
- **Dividend Snowball (DRIP)** — 🟡 already computed in `get_planning_stats`
  (`dividend` block: labels/annual_data/cumulative_data); just needs a builder.
- **Portfolio Value vs USD/CAD** — 🟡 needs a stored historical FX series.
- **FX Impact — Gain/Loss vs Rate** — 🟡 needs FX attribution math.
- **Efficient Frontier (Risk vs Return)** — 🔴 needs the Optimizer (covariance +
  solver); see the "Portfolio Optimizer" item below.
- **Forward 12-Month Dividend Calendar** — 🟡 needs a pay-schedule heuristic; see
  the "Dividends — Income projection" item.
- **Max Drawdown / underwater** — 🟡 see the "Performance — Max drawdown" item.
- **Target vs Actual Allocation** — 🟢 but config-dependent: it needs per-account
  saved Rebalancer targets and an account/dimension selector, so it fits the
  Rebalancer tab better than a generic catalog chart. Deferred pending a way to
  pick the account/dimension within a pane.

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

## Tax — Province-based marginal rate helper

The Tax tab already takes a manual Marginal % (+ Inclusion %) and estimates tax
owed. Optional nicety: a province dropdown + estimated taxable income that looks
up the 2025 **combined federal+provincial** marginal rate and the correct
capital-gains / eligible-dividend effective rates, prefilling the Tax tab inputs.

- **How:** per-province bracket tables (13 jurisdictions) + a lookup; store
  `tax_province` and the income, derive the rate. Dividend tax credit + gross-up
  for the eligible-dividend effective rate.
- **Effort:** medium — mostly the bracket data + a small lookup; UI is a dropdown
  and one number. Deferred (manual rate is sufficient for now).

## Dividends — Default US withholding rate (setting)

A configurable default US dividend withholding rate (15% treaty) on the Settings
tab, applied to the **Dividends** tab's forward-income / net-yield estimates so
they reflect the haircut on US dividends even before `WithholdingTax` rows are
imported. Today withholding is only known from imported transactions, so forward
estimates on US names overstate net income.

- **How:** a `us_withholding_rate` setting (default 15%); in `get_dividend_stats`
  forward-income, multiply the expected dividend of USD-currency holdings by
  `(1 − rate)`. Keep actual received net-of-withholding as-is (it's from real
  rows) — this only affects the *forward* projection.
- **Note:** registered accounts differ (RRSP is treaty-exempt for US dividends;
  TFSA is not) — could refine by account type later.
- **Effort:** small–medium — one setting + a tweak to the forward-income calc.

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

## GICs — fold into net worth (Dashboard / Accounts)

GICs currently live only on the GICs tab (`GIC` is never referenced by the
Dashboard, Accounts, or Performance calcs), so their principal + accrued
interest isn't part of net worth anywhere. A $5k GIC is invisible outside its
own tab.

- **Compute:** add active GICs' `current_value` to the relevant account's total
  in `get_account_summary` / the dashboard net-worth figure (and optionally a
  "GICs" line in the account allocation, like cash). Use the same per-GIC
  current-value math already in `get_gic_stats` (factor it out to share).
- **Scope note:** decide whether GIC value counts as part of the account's cash
  or as a distinct asset class in the allocation breakdowns; matured GICs should
  be excluded (their principal returns to cash).
- **Effort:** medium — cross-tab; touches account summary, dashboard, and
  possibly the allocation breakdowns and Performance valuation.

## Rebalancer — exact (convergent) trade solver

The v2 per-account rebalancer uses a single-pass greedy allocator: it splits
the cash/sell budget across buckets by drift and tops up holdings pro-rata to
their fractional exposure. Because ETFs span several buckets, one pass doesn't
fully converge to the targets (buying a broad ETF to fill "tech" also feeds
other buckets), so the "Projected" column lands short of the targets.

- **Improve:** solve it properly as constrained least-squares / LP — minimise
  Σ(projected_bucket − target)² subject to per-holding trade limits (no negative
  shares; cash budget in cash mode) using each holding's fractional bucket
  vector. Iterate the greedy step to convergence as a lighter alternative.
- **Watch:** keep it dependency-light (no scipy) — an iterative reweighting loop
  in pure Python is probably enough and matches the existing stack.
- **Effort:** medium — engine-only change in `get_rebalancer_data`; UI unchanged.

## Rebalancer — risk targeting by historical volatility

The Risk dimension currently buckets holdings Low/Medium/High by market **beta**
(`price_service` caches `info['beta']`/`beta3Year`, with an asset-type fallback
when beta is missing). Beta is market-relative; an absolute volatility measure is
more robust and covers the ETFs/securities that don't report a beta.

- **Add:** compute annualized **standard deviation of ~1yr daily returns** from
  yfinance price history per ticker, cache it in `price_cache.meta_json`
  alongside `beta`, and offer a "Risk basis: Beta / Volatility" toggle (or a
  blended score). Bucket by tunable thresholds.
- **Bonus:** show the actual beta/volatility number per holding in the account
  view so the bucketing is transparent, and make the bucket thresholds editable.
- **Watch:** history fetches are heavier than the one-shot `.info` call — fetch
  once and cache; refresh lazily.
- **Effort:** medium — `_fetch_one_metadata` history pull + a basis toggle in the
  risk classifier.

## Rebalancer — strategy presets (one-click target templates)

Offer a few **common investment strategies** as preset target templates per
account: pick one and it fills in the target % (across the right dimension),
then the existing engine produces the buy/sell recommendations. Saves setting
targets by hand and gives a starting point.

- **Candidates (start with 1–2):**
  - **Three-fund / classic 60-40** — by asset class: e.g. 60% Stock / 40% Bond
    (or 60/40/0 with a cash sleeve). Maps cleanly to the Asset Class lens.
  - **All-Weather (Ray Dalio)** — ~30% stocks / 55% bonds / 15% gold+commodities,
    by asset class.
  - **Core-satellite** — large % in broad/diversified (low Blended Risk) + a
    capped satellite in higher-risk names; maps to the Blended Risk dimension.
  - **Age-based glide path** — stock/bond split from age (e.g. "110 − age" in
    stocks); needs a user age/setting.
- **How it'd work:** a "Strategy" dropdown on the account view → on select,
  pre-fill the target inputs for the matching dimension (don't save until the
  user confirms), then the normal Save → recompute flow generates trades. Store
  chosen strategy alongside the targets if we want it to persist.
- **Note:** several strategies are asset-class/bond-heavy — pairs well with the
  asset-class look-through already used in the Overall view, and would benefit
  from real bond/commodity holdings being classified correctly.
- **Effort:** medium — a small preset table (strategy → {dimension, targets}) +
  a dropdown that populates the existing target inputs; engine unchanged.

## Watchlist — quick "add transaction" from a row

A one-click action on a watchlist row that jumps to the Add Transaction form
(or a modal) pre-filled with the ticker, currency, and live price, so a watched
name can be turned into a Buy without retyping.

- **How:** link/button on each row → Transactions add form with query params
  pre-filling ticker/currency/price (form reads them on load), or a small inline
  buy modal that POSTs to the existing add-transaction route.
- **Effort:** small — a pre-fill on the existing transaction form + a row action.

## Watchlist — bulk add tickers

A way to add many tickers to the watchlist at once instead of one form
submission each — paste a list (e.g. `AAPL, MSFT, XLV NVDA`) and have them all
created and tracked.

- **How:** a "Bulk add" textarea (comma/space/newline separated) → a new route
  that loops the tickers, dedupes against existing watchlist entries, fetches a
  price + metadata for each (`refresh_prices` + `get_holdings_metadata`), and
  auto-classifies company/sector/currency like the single add already does.
  Report how many were added vs skipped.
- **Bonus:** accept currency per line or guess from the symbol suffix (`.TO`/`.NE`
  → CAD, plain → USD); let the rebalancer-gap "candidates" feed a bulk add of all
  ideas for a bucket at once.
- **Effort:** small–medium — one route + a textarea; reuses the existing add and
  auto-classify logic.

## Import — accept more file types

Broaden the importer beyond TD/CIBC CSV + TD PDF:

- **Excel (.xlsx/.xls):** read with openpyxl in `parse_upload` (xlsx branch),
  convert the sheet to CSV-style rows, feed the existing `_parse_content`. Would
  let the downloadable template be a true `.xlsx` and import directly.
- **OFX/QFX** (the bank-standard download many brokers offer) — a generic parser
  that covers more institutions than per-broker CSV.
- **More brokers' CSVs** (Questrade, Wealthsimple, RBC DI, etc.) — add detectors
  + per-broker column maps alongside `_detect_broker`.
- **Effort:** medium — one parser per format; the normalise-to-Transaction step
  is shared.

## Projections — TFSA room projector

Track and project TFSA contribution room (the V6 Excel had this on the
Projections sheet). Given current TFSA value, room remaining, and the annual
limit, project the account value year by year assuming room is filled each year.

- **How:** add a small section to the Projections tab: inputs for current TFSA
  value, room remaining, and annual room added (e.g. $7,000); compound at the
  expected return while adding the yearly room. Could read the actual TFSA
  account value from holdings if an account is flagged as TFSA.
- **Now available:** `calculations.get_contribution_room(account, type)` already
  computes current TFSA/FHSA room (total + this-year) and the RDSP $200k cap from
  transactions (Settings → reconstruct or anchor) — surfaced on the account page.
  The projector can seed its "room remaining" / "annual room" inputs from it.
- **Effort:** small — one calculator + a card; reuses the FV helper in
  `get_planning_stats`.

## Projections — sequence-of-returns risk

Show how the *order* of returns affects outcomes (the V6 Excel had this): a big
crash early vs. late produces very different ending balances even with the same
average return — important once withdrawals/decumulation matter.

- **How:** run two deterministic paths over the horizon — one with a crash in the
  first few years then recovery, one with the crash at the end — and report the
  ending-balance difference ("timing risk cost"). Optionally overlay both on the
  growth chart.
- **Effort:** small–medium — two scripted return sequences through the FV loop +
  a stat card / overlay.

## New tab — Portfolio Optimizer (efficient frontier)

Mean-variance (Markowitz) optimization over the holdings of an account: compute
the efficient frontier and the optimal weights for max-Sharpe and min-variance
portfolios, then diff against current weights (ties naturally into the
Rebalancer). The V6 Excel had this as a `run_optimizer.bat`-driven "Efficient
Frontier" tab reading `EF_POINTS`, `EF_MIN_W`, `EF_MAX_W`, `RISK_FREE_RATE`,
`HISTORY_DAYS` from Settings.

- **Data:** per-holding historical returns from yfinance (e.g. ~1–3yr daily),
  build the covariance matrix + expected returns; we already fetch price history
  elsewhere, so cache it.
- **Compute:** efficient frontier points and optimal weights under min/max
  per-holding weight constraints and a risk-free rate (for Sharpe). Keep it
  dependency-light — numpy is available via yfinance, but **scipy is not**, so
  use a constrained quadratic solve / random-portfolio sampling in pure
  Python+numpy rather than `scipy.optimize`.
- **Where:** a new Advanced tab; show the frontier scatter (risk vs return),
  current portfolio as a point, and the suggested optimal weights vs current as
  a table — with a hand-off to the Rebalancer to act on the deltas.
- **Effort:** medium–large — history fetch + covariance + optimizer + a new tab;
  the math is the bulk of it.

## New tab — RDSP tracker & decumulation planner

A dedicated RDSP planner (the imported account is an RDSP). Beyond the
accumulation projection, model the **withdrawal / decumulation phase**: how the
portfolio draws down over time once payments start.

_Already done (account page):_ grants/bonds total and contribution room remaining
(of the $200k lifetime cap) now show on the RDSP account detail via
`get_contribution_room`. What's still parked is the **grant/bond entitlement +
carry-forward** math and the two-phase accumulate→decumulate / LDAP projection.

- **Accumulation:** project contributions + government grants (CDSG, up to
  $3,500/yr matched) and bonds (CDSB) growing to the withdrawal age, honouring
  the 10-year assistance-holdback rule (grants/bonds repaid if withdrawn early).
- **Decumulation:** model **LDAP** (Lifetime Disability Assistance Payments) —
  the annual withdrawal formula based on account value and life expectancy
  (roughly value ÷ (83 − age + 3)) — and show the **portfolio decreasing over
  time** through the withdrawal years (value, annual payment, depletion age).
- **Risk glide path:** let the assumed return/volatility **shift down over time**
  (de-risk as withdrawals approach and during drawdown), instead of one fixed
  return — ties into the Rebalancer's Blended-Risk dimension.
- **Inputs:** current value, contribution, grant/bond schedule, withdrawal start
  age, expected return (and a glide path), life expectancy.
- **Reference:** the official RDSP calculator at <https://www.rdsp.com/calculator/>
  (saved in memory) for the grant/bond and LDAP rules to mirror.
- **Effort:** large — RDSP-specific rules (grants/bonds, holdback, LDAP) plus a
  two-phase (accumulate → decumulate) projection and a glide-path model.
