# Suggestions for Later

Ideas parked for future implementation. Not committed work — just a backlog.

## How to read this backlog

Every suggestion below ends with a **Priority** tag — `Impact · Effort`.

- **Impact** — High / Med / Low: does it serve a core goal, help the dividend-heavy
  friend, or unblock other work?
- **Effort** — tiny / small / medium / large (some "small–medium"), taken from each
  item's own notes.
- **✅ Shipped** (removed from this list): the percent-format chart renderer plus
  three percent charts (Portfolio Growth %, Growth % vs Benchmarks, Monthly
  Return %); the recurring/scheduled-transaction engine; multi-currency cash
  (per-currency CAD/USD balances + the Currency Exchange transaction type); the
  **FUNDerelele logo & branding** (sidebar wordmark, topbar icon button, readable
  price-stamp, static-asset cache-busting); and empty-account hygiene (dropdowns
  hide transaction-less accounts + a Settings "clean up empty accounts" button).

**Prioritize with the 2×2:** High-impact + low-effort = do now; High-impact +
high-effort = plan & schedule; Low-impact + low-effort = rainy-day fill-ins;
Low-impact + high-effort = skip / much later.

## Backlog at a glance (≈119 ideas)

| Category | Count | Notes |
|---|---|---|
| Charts | ~45 | ~27 finance-useful (incl. correlation/diversification heatmap) + ~15 fun/easter-egg (composition-over-time, RDSP widget, top movers) |
| Per-tab feature enhancements | ~35 | Performance (MWR-vs-TWR, what-if replay), Dividends (payout-safety), Rebalancer, Watchlist, Cash Flows, Projections, Tax (asset-location, ✨ tax-loss harvester), Import, GICs, Holdings (manual NAV), FX, RDSP (incl. ⭐ nest-egg floor) |
| AI / smart features | 1 | Portfolio commentary + "Ask your portfolio" (LLM grounded on your data) |
| New tabs (big features) | 11 | Time Horizon, Optimizer, RDSP planner, Net Worth, Calendar, Year-End Tax, Needs-Attention, Wrapped, The Melt, Retirement (RRSP), Market research |
| Fun & delight (non-chart) | 8 | Theme picker, milestones, flavour line, command palette, ticker-tape, scoop-of-day, empty states, achievements |
| UI/UX polish | 6 | Restore-alerts, sidebar hide/reorder tabs, sparkline, chart descriptions/hide/star, daily-swing widget, trend indicator |
| Infrastructure / hardening | 7 | Test suite, yfinance resilience, income tax, README screenshots, budget-app integration (+ read-only API endpoint), robust sample data |
| Account / data model | 3 | Savings account (recurring-interest), curate available account types, view/sort by bank (institution) |
| Canadian rules | 1 | RRSP room |
| Internationalization | 1 | German → Spanish → French |
| Branding | 1 | Remaining: favicon, title prefix, accent flow-through, per-holding logos (core logo shipped) |

_Keep this table and the Priority tags in sync whenever suggestions are added or removed._

## Account-type special rules — coverage tracker

Which account types have type-specific logic beyond the generic "registered = tax-sheltered" flag.
Available types: Non-Reg, TFSA, RRSP, FHSA, RDSP, RESP, LIRA, RRIF (keeping all; possibly add LIF + Savings/HISA).

| Type | Special rules implemented | Depth |
|---|---|---|
| **RDSP** | grants/bonds, 10-yr holdback, LDAP/DAP drawdown, projections, Glide Lab | **full** |
| **TFSA** | contribution-room tracking (annual limits + anchor) | partial |
| **FHSA** | contribution-room anchor | partial (less fleshed than TFSA) |
| Non-Reg / RRSP / RESP / LIRA / RRIF | none yet — only the generic tax-sheltered flag | — |

Keep in sync as rules are added (e.g. RRSP room, RESP/CESG grants, RRIF minimum withdrawals, LIF payout).

## View / sort / filter by bank (institution) — everywhere

Parked 2026-06-15. Make the **brokerage / institution** (TD, CIBC, …) a first-class
grouping/sort/filter dimension across the whole app — Holdings (filter + sort by
bank), Accounts (group accounts under their bank, with per-bank subtotals),
Dashboard/Charts (a "By Bank" allocation), and anywhere accounts are listed. The
institution is currently only embedded in the account name ("TD Direct Investing -
59WBM0N"); either parse a bank prefix from the name or add an explicit, editable
`institution` field on `Account` (defaulted from the importer, set on the Accounts
page). Lets you total/inspect holdings per brokerage at a glance.

**Priority:** Impact: Med · Effort: medium

## Watchlist alerts — multiple tiers per ticker

The dashboard hero alert + watchlist row highlight currently trigger on a single
target (buy ≤ / sell ≥). Extend to **multiple alert levels per ticker** — e.g. a
normal **Buy** target and a deeper **Extreme Buy** (strong-buy) target, and likewise
tiered sell/take-profit levels. Each tier could have its own colour/label in the
hero strip and watchlist row (e.g. amber "buy", green "extreme buy").

- **Model:** today `WatchlistItem` has one `target_price` + `target_type`. Add
  more target fields (or a small related `alert_levels` table: ticker, kind,
  price, label) so a ticker can carry several thresholds.
- **UI:** the watchlist add/edit form gains rows for each tier; `price_alerts()`
  emits the deepest tier hit; the hero strip / row highlight pick colour by tier.
- **Effort:** small–medium — a model/field add + form rows + a tier check in
  `price_alerts` and the watchlist row classes.

**Priority:** Impact: Med · Effort: small–medium

## Dashboard — "Restore alerts" button (Customize)

A button in the dashboard **Customize** bar that **un-dismisses all watchlist
alerts** — clears the per-ticker dismissals (`dashAlertChips`), the header dismiss
(`dashAlertHeaderSig`), and re-enables the widget (`dashAlertEnabled`) in one click.
Today re-adding the widget clears them, but there's no explicit "show everything I
dismissed" control. _Effort: tiny — one button that clears those localStorage keys
and calls `applyAlerts()`._

**Priority:** Impact: Low · Effort: tiny

## RRSP — contribution rules & room

Add RRSP-specific contribution logic alongside the existing TFSA / FHSA / RDSP room
math in `get_contribution_room`. RRSP room is **earned-income-driven** (18% of prior
year's earned income, up to the annual max), plus carry-forward of unused room and
the deduction-limit-vs-contributed distinction — so it needs a user-entered room
figure (like the TFSA anchor) rather than being reconstructable from transactions
alone. Surface it on the RRSP account detail and the Tax tab (RRSP contributions are
deductible). _Effort: medium — a room input/anchor + a calculator + UI, mirroring the
TFSA/FHSA room pattern._

**Priority:** Impact: Med · Effort: medium

## Branding — remaining polish (core logo shipped)

_Shipped:_ the **FUNDerelele wordmark** in the sidebar (luma-keyed transparent
PNG, brand box height-aligned to the topbar divider), a **topbar cup icon button**
(`.btn-icecream`, inert placeholder for the planned ice-cream-mode toggle — see
memory `icecream-mode-planned`), a readable "prices updated" stamp with a live
dot, and a `static_v()` cache-buster so CSS/JS/image edits reload without a hard
refresh. Source art lives in `static/img/` (`logo.png`, `icon.png`).

Still parked:

- **Favicon + page `<title>` prefix.** A matching favicon and a "FUNDerelele —"
  prefix on each tab's title (the sidebar shows the wordmark, but the browser tab
  still reads "Portfolio Tracker").
- **Accent flow-through.** Pull the logo's cyan accent into more CSS variables so
  the brand colour carries through links, active nav, chart accents, and buttons —
  Midnight Terminal dark stays the base. Optional subtle watermark on empty states.
- **Lighter assets (SVG / compression).** Re-export the logo & icon as SVG, or
  compress the PNGs (~1–2 MB each today), so they load lighter at any size. See
  memory `optimize-brand-pngs`.
- _Free API — holding logos:_ a logo-by-domain service (Clearbit-style, no key) or
  a fundamentals provider's logo endpoint to show a small **company/ETF logo** next
  to each holding on Holdings/Dashboard. Cheap visual polish; cache per ticker,
  fall back to a monogram when none is found.
- **Effort:** small — favicon + title prefix are tiny; accent flow-through and
  holding logos are the bulk.

**Priority:** Impact: Low–Med · Effort: small

## New tab — Time Horizon / Liquidity

Promote the "By Time Horizon" widget into a dedicated tab. The widget gives the
breakdown (cash = Immediate; GICs bucketed by maturity; holdings by account via a
per-account horizon assignment; RDSP = Long; FHSA = Medium; TFSA/Non-Reg =
Flexible/overridable). The tab adds depth:

- **Per-bucket drill-down** — list the accounts / holdings / GICs in each bucket.
- **GIC maturity ladder** — a timeline of upcoming maturities.
- **RDSP holdback countdown** — compute the real years-until-withdrawable from the
  last RDSP contribution date (10-yr rule) instead of a flat "Long". Ties into the
  parked RDSP tracker tab.
- **Liquidity timeline view** — a single ordered Immediate→Long visualization.
- Reuses the per-account horizon assignment (default from account type, overridable
  on the Accounts page) and the widget's bucketing engine.
- **Effort:** medium — new template/route + drill-down + ladder; the bucketing
  logic comes from the widget.

**Priority:** Impact: Med · Effort: medium

## FX Sensitivity — multi-currency

The shipped FX Sensitivity chart (`charts._b_fx_sensitivity`) only models USD/CAD
(the portfolio's USD holdings vs the CAD base). Extend it to other currencies the
holdings actually use: detect the distinct non-CAD currencies from holdings,
compute each one's native exposure, and either let the user pick which currency
pair to stress (a dropdown like the per-pane account filter) or show a small
multi-series chart. Would also pair with a true historical FX overlay (the parked
"Portfolio Value vs USD/CAD" idea).

**Priority:** Impact: Low · Effort: medium

## Sidebar — tab management (hide + reorder)

Let the user **hide tabs they don't use** and reorder the rest, persisted in
localStorage (per browser, like the dashboard/charts layouts). The clean,
no-scroll sidebar is a feature today — the value here is curating it, not adding
chrome.

- **Hide (the main want):** a per-tab show/hide toggle in an "edit nav" mode so
  the sidebar only shows the tabs that matter to this user. Concrete use case:
  **hide Tax & ACB and surface RDSP instead** for the primary user, while a second
  user (the brother) keeps **RDSP hidden**. Effectively a per-browser tab profile.
- **Reorder:** drag `.nav-item`s (or up/down controls) within their section;
  save the order, restore on load. Keep the MAIN/ANALYTICS/ADVANCED/TOOLS grouping
  or allow free reordering — decide during design.
- **Keep it clean:** default to all-visible; hiding/reordering is opt-in via the
  edit mode so the sidebar never grows clutter or needs scrolling.
- **Effort:** small–medium — a hidden/ordered list in localStorage + an edit-nav
  toggle in `base.html` (HTML5 drag-and-drop or simple controls).

**Priority:** Impact: Low–Med · Effort: small–medium

## Dashboard — hero sparkline visual polish

The hero strip's 12-month sparkline (`loadSparkline()` in `dashboard.html`) works
but still looks a bit off. Ideas to make it nicer: trend-color the line green/red
by net change over the window; smooth/normalize the y-scale so flat-ish periods
don't look jagged; a faint baseline or a "vs contributions" ghost line; possibly
a small area-gradient tweak or a wider/taller footprint. Parked for a later pass.

**Priority:** Impact: Low · Effort: small

## Charts tab — deferred charts (🟡/🔴)

The Charts tab (`charts.py` + `templates/charts.html`) ships the 🟢 catalog. These
were parked per the build plan; add a builder in `charts.py` + a `CHART_CATALOG`
entry to enable any of them:

- **Asset Class look-through** (stock/bond/cash) — 🟡 `fund_assets` is cached; see
  the "Asset-class look-through (ETFs)" item below. (Distinct from the shipped
  "By Asset Type" chart, which is equity/ETF/etc.)
- **Geographic Exposure** — 🔴 yfinance gives `country` only for individual
  stocks; ETFs have no country look-through, so this would be mostly "Mixed".
  _Free API:_ **REST Countries** (free, no key) for country **flags/regions** to
  prettify whatever country data exists.
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

**Priority:** Impact: Med _(percent-format renderer + first % charts shipped; the rest vary)_ · Effort: small–medium each

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

**Priority:** Impact: Med · Effort: small

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

**Priority:** Impact: Med · Effort: small–medium

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

**Priority:** Impact: Low · Effort: medium

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

**Priority:** Impact: Med _(dividend-friend feature)_ · Effort: small–medium

## Dividends — Per-ticker payment drill-down

Click a ticker row in the "By Ticker" table to expand its individual dividend
payments (date, gross, withheld, net) — useful for spotting cuts, special
dividends, or reconciling against statements.

- **Compute:** already have every Dividend/WithholdingTax row; just group by
  ticker and render an expandable sub-row (or a small modal) on click.
- **Effort:** small — a hidden detail row toggled in `dividends.html`.

**Priority:** Impact: Med · Effort: small

## Dividends — Income projection & growth

A forward 12-month income calendar (expected payment per month from current
holdings) plus a year-over-year dividend growth rate per ticker. Turns the tab
from "what I received" into "what I'll receive."

- **Data:** forward rate per share is already cached (`dividend_rate`); pair it
  with each holding's pay frequency/schedule (yfinance `dividends` history) to
  place expected payments on a calendar.
- **Growth:** compare each ticker's trailing-year net vs. the prior year.
- **Effort:** medium — needs a payment-schedule heuristic from dividend history.

**Priority:** Impact: High _(dividend-friend feature)_ · Effort: medium

## Performance — Max drawdown

Largest peak-to-trough decline over the selected range, as a stat card (and
optionally a shaded region on the chart). Good risk context next to TWR.

- **Compute:** over the value series, track the running peak; drawdown at each
  point = (value − peak) / peak; max drawdown = the most negative. Compute in
  the Performance JS from the already-loaded series (respects scope/range/cash).
- **Where:** another stat card beside "Annualized TWR", e.g. "Max Drawdown −18%".

**Priority:** Impact: Med · Effort: small

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

**Priority:** Impact: Low–Med · Effort: small–medium

## Cash Flows — Matching / "free money" ratio stat

A headline stat card showing the RDSP efficiency number: free government money
(grant + bond) earned per $1 of self-contribution — e.g. "$1.50 grant+bond per
$1 contributed". Complements the composition doughnut's "% free money" by
framing it as a return on contributions.

- **Compute:** `(grant_total + bond_total) / contribution_total` — both already
  available in `get_cashflow_stats` (`by_subtype`/`free_money`); just add the
  ratio to the return dict and a stat card. Respects the active account filter.
- **Effort:** tiny — one division and one stat card.

**Priority:** Impact: Med _(RDSP)_ · Effort: tiny

## Cash Flows — Cumulative growth line

A cumulative-deposits line chart (running total of contributions over time),
optionally toggling with or sitting beside the annual bars, to show the
account's funding building up rather than just per-year amounts.

- **Compute:** sort deposits ascending and accumulate `net_cad`; could also
  stack cumulative by subtype to show contribution vs. grant vs. bond growth.
- **Where:** a toggle on the Annual Cash Flows chart, or a second small chart.
- **Effort:** small — a running-sum series in `get_cashflow_stats` and a line
  dataset in `cashflows.html`.

**Priority:** Impact: Low · Effort: small

## GICs — fold into Performance series (remaining piece)

_Mostly done:_ active GICs now count toward net worth — `get_gic_value_by_account`
feeds the Dashboard total (`total_gics`), each account's `total_value` in
`get_account_summary` (with principal treated as contributed, so gain = accrued
interest only), and a distinct **GICs** slice in the account allocation
(`get_account_breakdown`). Matured GICs are excluded. The shared `gic_value`
helper holds the current-value math.

**Still parked:** the **Performance time-series** (`get_performance_series`)
doesn't value GICs at each historical month, so the value/TWR line ignores them.
Adding it means valuing each active GIC at every month-end (using `gic_value`
with an `as_of` date) and folding it into the monthly market value — plus
deciding how a GIC maturity (principal returning to cash) flows through the
series. _Effort: medium — month-by-month valuation + maturity handling._

**Priority:** Impact: Med · Effort: medium

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

**Priority:** Impact: Med · Effort: medium

## Rebalancer — Industry dimension (info-only)

Parked 2026-06-15. A finer-grained **Industry** breakdown (below Sector — e.g.
Semiconductors, Banks, Pipelines) as a *read-only allocation lens*, NOT a target
dimension (the user said they won't rebalance by industry). yfinance exposes
`industry` for single stocks; ETFs have no industry look-through, so funds would
fall to "Unclassified" or their fund-level sector. Surface it like the Country
lens — Accounts/Charts "By Industry" breakdown — without adding it to
`REBAL_DIMENSIONS`.

- Done this session for context: removed **Beta** as a target; added **Country/
  Region** (with single + **weighted** overrides); enriched the AI payload (full
  bucket map, sell candidates, per-holding region/yield/income, Preferences,
  cash-budget check). Industry is the remaining geographic/classification idea.

**Priority:** Impact: Low · Effort: small–medium

## Rebalancer — Blended-Risk follow-ups

_Shipped:_ the **Blended Risk** dimension is now **volatility-driven** (PRIIPs/UCITS-style
annualized-stdev bands: <5% Very Low · <11% Low · <18% Moderate · <30% High · ≥30% Very
High) with a single-name **size adjustment** (mega-cap ≥$100B nudged down a band, small/
micro <$2B floored at High) and a per-ticker **override layer** (`RISK_OVERRIDES` in
`calculations.py` — cash/bond/high-yield-junk/leveraged/crypto pinned). Volatility is cached
in `price_cache.meta_json` (`volatility`); each holding's measured vol % + bucket shows in a
"Blended Risk Allocation" card (grouped by basket, collapsible) on the Rebalancer when
targeting by Blended Risk, plus sortable **Vol / Risk** columns on the Holdings tab. (The
separate **Beta** dimension is unchanged.)

Also shipped since:
- **GICs folded into the blend allocation (view-only).** `get_rebalancer_data` counts the
  account's GIC value as Very Low (never tradable) so the risk view + recommendations stop
  over-buying Very Low; GICs show as a "guaranteed" line in the allocation card. Cash shows
  as "to deploy". (The glide's `current_safe_pct` already counted GICs.)
- **Whole-share toggle** on Recommended Trades (rounds trades to whole shares; default off).
- **Cash-mode spill** — deploy-cash now spills into buyable buckets instead of leaving the
  share of an unbuyable target's cash idle.
- **Glide-down seeds Very Low + Low.** The RDSP glide hand-off splits the safe sleeve — kept
  almost entirely in Low (bonds) until withdrawals near, ramping a Very Low cash reserve up to
  ~2 yrs of withdrawals (`VERY_LOW_END_RESERVE`) by the **withdrawal-start year** (not glide
  end, so cash doesn't sit idle). The glide table shows the per-year Very Low / Low split.
- **Cash-floor hand-off.** Each glide row also offers a **Cash →** link that seeds the *Asset
  Type* dimension with `Cash:<reserve%>` — GICs don't count as Cash there, so it surfaces a
  "need this much *liquid* cash" gap even when the Very Low risk bucket is full of (locked)
  GICs. Pairs with the **Risk →** (blend) link.

Still parked:

- **Adjustable reserve-years in Settings.** `VERY_LOW_END_RESERVE` (~3 yrs of withdrawals) is a
  code constant; expose a Settings input so the cash-reserve target is user-tunable. _Effort:
  small — a setting + read path in `rdsp_view`._
- **Exact per-year cash sizing.** Size the cash floor from the projection's actual per-year
  withdrawal amounts instead of a fixed ~3-yr reserve. _Effort: small–medium._

- **Fold GICs into the *Overall* lens too.** The per-account blend view counts GICs; the
  Overall Portfolio → Blended Risk lens doesn't yet. _Effort: small._
- **Settings UI to edit risk overrides + bands.** `RISK_OVERRIDES` and the vol band ceilings
  are code constants today. A Settings editor (per-ticker bucket + tunable band cutoffs,
  stored in `settings`) would let the override list be managed without code edits. _Effort:
  medium — a settings table/UI + a read path in the classifier._
- **Normalize-targets-to-100% button** in the targets editor (proportionally rescale inputs).
  _Effort: tiny — client-side JS._

**Priority:** Impact: Low–Med _(remaining items are conveniences)_ · Effort: small–medium

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

**Priority:** Impact: Med · Effort: medium

## Rebalancer — flat-rate (constant safe %) drawdown support (parked)

The RDSP **glide** drawdown hands off to the Rebalancer (seeded de-risk targets per
year). Make the Rebalancer also support a **flat allocation** held *constant* through
withdrawal — i.e. if the user picks a fixed safe/stock mix instead of gliding, the
Rebalancer should keep that **same safe %** every year of the drawdown (rebalancing
back to it), not ramp it. So both RDSP drawdown styles (glide *and* flat) have a
working Rebalancer hand-off. Not needed now — user may or may not run a flat plan.

**Priority:** Impact: Low–Med · Effort: small–medium

## Watchlist — quick "add transaction" from a row

A one-click action on a watchlist row that jumps to the Add Transaction form
(or a modal) pre-filled with the ticker, currency, and live price, so a watched
name can be turned into a Buy without retyping.

- **How:** link/button on each row → Transactions add form with query params
  pre-filling ticker/currency/price (form reads them on load), or a small inline
  buy modal that POSTs to the existing add-transaction route.
- **Effort:** small — a pre-fill on the existing transaction form + a row action.

**Priority:** Impact: Low–Med · Effort: small

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

**Priority:** Impact: Low · Effort: small–medium

## Import — accept more file types

Broaden the importer beyond TD/CIBC CSV + TD PDF:

- **Excel (.xlsx/.xls):** read with openpyxl in `parse_upload` (xlsx branch),
  convert the sheet to CSV-style rows, feed the existing `_parse_content`. Would
  let the downloadable template be a true `.xlsx` and import directly.
- **OFX/QFX** (the bank-standard download many brokers offer) — a generic parser
  that covers more institutions than per-broker CSV.
- **More brokers' CSVs** (Questrade, Wealthsimple, RBC DI, etc.) — add detectors
  + per-broker column maps alongside `_detect_broker`.
- **Auto-resolve tickers via OpenFIGI** (free, optional key) — map broker
  descriptions / **CUSIP / ISIN** to real symbols automatically, cutting the manual
  ticker-mapping step. Pairs with the existing TickerMap + "Fix a wrong symbol".
- **Effort:** medium — one parser per format; the normalise-to-Transaction step
  is shared.

**Priority:** Impact: Med · Effort: medium

## Import — recurring rules in the import file (self-contained hand-off)

The native CSV importer creates **transactions only** — recurring/scheduled rules
are a separate app setting, so a file handed to a new user (e.g. a friend's fund
account) can't carry their standing PACs. Let the native export/import round-trip
**`RecurringRule`s** too, so one file fully provisions an account incl. its ongoing
dollar-based mutual-fund PAC.

- **How:** a second sheet/section or a marker row (e.g. `type=RecurringBuy` with
  `frequency`/`end_date`/`dollar_based` columns) that `_import_native` turns into a
  `RecurringRule` instead of a `Transaction`; the export writes active rules.
- **Why parked:** transaction import + a one-time rule add already covers it; this is
  convenience for clean multi-user hand-off. Pairs with the dollar-based PAC engine.
- **Effort:** small–medium — extend `_import_native` + the export with a rule block.

**Priority:** Impact: Low–Med · Effort: small–medium

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

**Priority:** Impact: Med · Effort: small

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

**Priority:** Impact: Low · Effort: small–medium

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

**Priority:** Impact: Low–Med · Effort: medium–large

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

**Priority:** Impact: Med–High _(the tracked account is an RDSP)_ · Effort: large

## RDSP — provincial rate + at-source withholding tax

The RDSP tab taxes the **taxable portion** of each withdrawal at a single editable
**Tax rate %** (default 20%) — `net = withdrawal − taxable × rate`. That's a
deliberate simplification and is fine for now. Two refinements parked:

- **Provincial marginal rate selector.** Instead of typing a rate, pick a province
  (+ estimated retirement income) and prefill the combined federal+provincial
  marginal rate. Shares the bracket-table work with the parked **"Tax —
  Province-based marginal rate helper"** item; build once, feed both the Tax tab
  and the RDSP tab's rate field.
- **At-source withholding vs. final tax.** RDSP issuers withhold tax on the taxable
  part **at payment** by lump-sum tiers (~10% ≤ $5k, 20% $5k–$15k, 30% > $15k
  federally, plus provincial), then it trues up to the real marginal rate at filing.
  Could show "withheld now" vs "actual tax / refund at filing" as two numbers in the
  schedule. Low priority — the single-rate net is close enough for planning.

**Priority:** Impact: Low–Med · Effort: small (rate selector) / medium (true withholding)

## RDSP — stress test: multiple / double-dip crashes (parked)

The stress test offers two shock archetypes — **sharp crash** (V-shaped, recovers) and
**lost decade** (prolonged stagnation, now length-adjustable) — which bracket sequence
risk. Parked: **multiple crashes** or a **crash-recover-crash double-dip**. They're
really just a deeper/longer drawdown, which the severity + timing controls already
approximate, so they'd add panel clutter for marginal insight. Revisit only if a
specific "two hits in a decade" scenario is wanted.

**Priority:** Impact: Low · Effort: small

## RDSP — stress test chart lines (shipped: matched flat/glide overlay pair)

The Value chart now draws a **matched pair** of balance paths under the selected
shock — **Glide** (purple dashed) and **Flat** (red dashed) — so the gap between
them shows what de-risking preserves. The crash pair swaps out for the lost-decade
pair when a stagnation is selected, and both react live to the severity /
stagnation-length controls. Drops out entirely on "No shock". Built on
`chart.stress_glide` + `_stress_line` in `rdsp_view`, drawn in the chart
`datasets()`.

Parked refinements:
- Only draw the glide line **when the gap to flat is large** — in a mild crash the
  glide barely dips and the two lines nearly overlap.
- A dedicated **lost-decade visual** — a 10-yr stagnation is a gradual divergence,
  not a dramatic dip, so the flattened line can read as noise vs the crash V.

**Priority:** Impact: Low · Effort: tiny _(shipped; refinements parked)_

## RDSP — stress test "years below needed income" metric (parked)

The sequence-of-returns stress test compares flat vs glide on after-tax income,
worst-year income, biggest 1-yr income drop, and ending value. A useful addition,
parked: a **"years below $X needed income"** row — given a user-entered *minimum
income I need in retirement*, count how many drawdown years each plan falls short
under the shock. Powerful (it frames the glide's steadier floor as "fewer lean
years") but needs one new input (a needed-income control). Build when the stress
test gets more attention.

**Priority:** Impact: Low–Med · Effort: small _(one input + a count per scenario)_

## RDSP — "Return %" chart view (parked)

A 4th cycleable view on the RDSP projection chart (next to Value / Composition /
Income / Tax): a single line of the **per-year expected return %** over the
horizon. In flat-drawdown mode it's a flat line; in **glide** mode it visibly
slopes down through the de-risking window (growth rate → full-safe ~4%), making
the glide's effect on returns tangible at a glance.

- **Data:** trivial — the engine already knows each year's rate (`return_by_year`
  in glide mode, the flat `return_rate`/`draw_rate` otherwise). Add a
  `chart.return_pct` array and a builder branch; uses the existing **percent**
  formatting path (a separate y-axis in %, not the CAD balance scale).
- **Why parked:** nice-to-have visualization of an assumption, less practically
  useful than the Income view (which shipped). Build after the early-crash stress
  test if wanted.

**Priority:** Impact: Low · Effort: small

## RDSP — remove the "Tax" chart view (decided: drop it)

The projection chart's **Tax** view splits the drawdown balance into tax-free
(contributions, green) vs taxable (grants + bonds + growth, orange). It's correct
per RC4460 but reads almost entirely taxable (contributions are ~8% of the balance
at withdrawal), so it's low-signal. With the **Income** view now shipped (and more
useful), the user decided to **drop the Tax view**.

- **Action:** remove the Tax toggle button + the `tax` branch in `datasets()`; the
  per-year tax detail already lives in the withdrawal schedule table and the Total
  tax / After-tax kept stat cards. The `chart.tax` payload can go too (or stay
  harmlessly). Leaves Value / Composition / Income.
- **Effort:** tiny — a few deletions, no logic change.

**Priority:** Impact: Low · Effort: tiny _(user decided to remove)_

## Pre-public hardening — parked items

Scoped during the go-public pass; the quick security/docs/cleanup wins were done
(env secret key + debug, `SameSite=Lax`, MIT LICENSE, local-only README note,
requirements pin, GICs in net worth). These were deliberately deferred:

- **Test suite.** No automated tests exist, and the importers (TD / CIBC /
  native) and ACB/holdings math are intricate — three were changed recently. Add
  `pytest` cases for: CIBC round-trip, native-CSV idempotency, and ACB over a
  buy/sell/split sequence. _Effort: medium; highest leverage once others send PRs._
- **yfinance resilience.** The whole app depends on an unofficial, rate-limited
  API. Add retry/backoff in `price_service` and a visible "prices stale (last
  updated X)" indicator instead of silent $0s when a fetch fails (the cache
  already stores last-good prices — lean on it). _Effort: medium._
  - _Free APIs — official & backup data:_ **Bank of Canada Valet** (free, no key)
    for **official USD/CAD** (more authoritative than yfinance's `USDCAD=X`) and
    **CPI/inflation + rates** — which also lets Projections compute *real* returns
    from real CPI instead of a hardcoded 2%, and gives a benchmark GIC/prime rate.
    **Finnhub / Tiingo / Alpha Vantage** (free tier, key) make good **fallback
    price/FX sources** when Yahoo is rate-limiting. **FRED** (free, key) for US
    inflation/treasury yields if you want US real-return benchmarks.
- **Income tax on the Tax tab.** Today it models only capital gains. For the
  dividend-heavy audience, add an income section: eligible-dividend gross-up +
  dividend tax credit, interest at full marginal, and US withholding as a foreign
  tax credit. _Effort: medium–large; easy to get approximately right, hard to get
  CRA-exact._
- **README screenshots.** A public repo's first impression. Capture the
  Dashboard, Accounts, Charts, and Dividends tabs (with sample data loaded) and
  add a `## Screenshots` section to the README. _Effort: small (needs the app
  running to capture)._

(Broadening the importer to more brokers / file types is already parked above
under "Import — accept more file types".)

**Priority:** Impact: High _(test suite + yfinance resilience are the real wins)_ · Effort: medium _(varies per item; README screenshots are small)_

## Fun & delight — personality for FUNDerelele

Optional flourishes that lean into the name (scoop your funds 🍨) and the Midnight
Terminal vibe. All opt-in and subtle — the clean, uncluttered UI stays the
default. Several pair naturally with the upcoming **logo & styling** pass.

- **Ice-cream theme & accent picker.** Keep Midnight Terminal as the default dark
  base, but add a few swappable accent palettes named playfully — e.g. *Mint
  Chip* (teal), *Blueberry* (indigo), *Neapolitan* (warm). Just remaps the CSS
  accent variables; persist the choice in localStorage like the dashboard layout.
  _Effort: small — a Settings dropdown + a handful of `:root` variable sets. Best
  done alongside the logo/styling work._

- **Net-worth milestone celebrations.** A subtle confetti burst + toast when net
  worth crosses a round threshold ($10k / $50k / $100k …) or a holding doubles
  from cost. Opt-in toggle in Settings so it never gets annoying. _Effort: small —
  a tiny JS confetti + a check against the dashboard total on load._

- **Portfolio "flavour" line.** An auto-generated one-liner on the dashboard hero
  describing your style from the allocation mix — e.g. "🍨 Dividend Connoisseur",
  "Tech Maximalist", "Balanced Scooper", "Maple-Heavy (mostly CAD)". Pure read of
  the existing breakdown data; rotate the wording. _Effort: small — one classifier
  + a hero line._

- **Command palette (Ctrl/⌘+K).** A terminal-flavoured quick switcher to jump
  between tabs and fire actions (add transaction, refresh prices, load sample).
  Fits the Midnight Terminal identity and speeds up power use. _Effort: small–
  medium — a modal + a static action/route index + fuzzy filter._

- **Ticker-tape strip.** An optional thin marquee across the top showing your
  holdings' live day moves (green/red), very "trading terminal". Toggle in
  Settings; reuses cached prices. _Effort: small — a CSS marquee fed by the price
  cache._

- **"Scoop of the day."** A small dashboard widget that spotlights one of your
  holdings each day (rotating) with a fun stat — best/worst day, longest held,
  highest yield. Seeded by the date so it's stable within a day. _Effort: tiny —
  one widget picking from existing holdings data._

- **Themed empty states & flashes.** Lean the copy into the motif: empty tables
  read "No funds scooped yet 🍨", import success flashes get a little personality.
  Tiny, and pairs with the branding pass. _Effort: tiny — copy + an icon here and
  there._

- **Achievements / badges (optional panel).** Lightweight milestones from the
  transaction history — "First $10k", "10 dividend payers", "5-year holder",
  "Diamond hands (held through a −20% drawdown)". Keep it a collapsible panel so
  it never clutters. _Effort: medium — a rules pass over transactions + a panel._

**Priority:** Impact: Low · Effort: small _(mostly tiny–small; Achievements is medium)_

## New tabs — practical

Bigger, genuinely useful tabs (distinct from the parked Time Horizon, Optimizer,
and RDSP-planner tabs).

- **Net Worth (beyond investments).** Turn the investment tracker into a full
  net-worth tracker: let the user add **manual assets** (cash/savings, real
  estate, vehicle, crypto) and **liabilities** (mortgage, loans, credit), then
  show total net worth = investments + GICs + manual assets − liabilities, with a
  trend over time. The single biggest scope expansion — many people want one
  number. _Effort: medium–large — a new model (manual line items) + a tab + fold
  into the dashboard total._
  - _Free API:_ **CoinGecko** (free, no key) for live **crypto** prices so a
    crypto holding tracks automatically instead of being a stale manual figure.

- **Calendar — upcoming events.** One month/agenda view of everything time-bound:
  **ex-dividend & pay dates** and **earnings dates** for your holdings (yfinance
  `Ticker.calendar` / dividend history), **GIC maturities**, and Canadian
  **contribution deadlines** (TFSA/RRSP/FHSA). Great for the dividend-focused
  user — "what's paying me this month." _Effort: medium — a calendar view + a
  per-ticker date fetch (cached)._
  - _Free API:_ **Finnhub** or **Tiingo** (free tier, key) give reliable
    **earnings dates** and **company news** — more dependable than yfinance's
    `calendar`, and a news feed is a natural companion widget.

- **Year-End Tax Package / Reports.** A printable/exportable per-year summary that
  rolls up what the Tax tab computes: realized gains (with ACB), dividends by
  type, US withholding (foreign tax credit), interest, and fees — formatted to
  drop into a tax return or hand to an accountant. _Effort: medium — mostly a
  report view + PDF/CSV export over existing calcs._

- **"Needs Attention" inbox.** A consolidated flags page: watchlist targets hit,
  GICs maturing soon, big allocation drift vs Rebalancer targets, holdings down a
  lot, cash sitting idle, unmapped tickers. One place that answers "is there
  anything I should look at?" _Effort: medium — a rules pass aggregating signals
  the other tabs already compute._

**Priority:** Impact: Med–High _(Net Worth + Needs-Attention are the standouts)_ · Effort: medium–large

## New tab — for fun

- **FUNDerelele Wrapped (year in review).** A "Spotify Wrapped"-style recap of
  your investing year: total contributed, best & worst performer, dividends
  collected, number of trades, busiest month, your portfolio "flavour", and a
  shareable summary card (with an optional **redacted mode** that shows % returns
  but hides dollar amounts). Seasonal/year-end delight that's also genuinely
  reflective. _Effort: medium — a stats roll-up over the year + a styled card;
  reuses dividends/performance/cashflow numbers already computed._

- **🍦 "The Melt" — where your returns drip away.** A themed-but-practical costs &
  leakage tab with a "$X melting away per year" headline and a breakdown of
  everything quietly eroding returns: **fee drips** (commissions, already in
  `fees_cad`), **MER drag** (each ETF's expense ratio × market value, from
  yfinance fund data where available), **withholding melt** (US dividend tax from
  your `WithholdingTax` rows), **cash melt** (idle cash × an assumed inflation
  rate), and **tax drag** (reuses the Tax tab). The ice-cream framing makes a dry
  "total cost of ownership" view fun, and it fills a real gap — nothing today
  shows your annual drag in one place. _Effort: medium — mostly aggregation over
  existing data + an expense-ratio fetch (cached)._

**Priority:** Impact: Low–Med _(The Melt is genuinely useful; Wrapped is delight)_ · Effort: medium

## Charts — more ideas (catalog candidates)

New `CHART_CATALOG` candidates (distinct from the shipped charts and from the
deferred 🟡/🔴 list above). The % ones use the **percent-format renderer**
(shipped) — just set `unit: 'percent'` in the builder. Each is a builder in
`charts.py` + a catalog entry.

**Performance & risk**
- **Rolling 12-month return** (line) — trailing-1yr return at each month-end.
- **Monthly returns heatmap** (year × month grid) — the classic colored grid.
- **Return distribution histogram** — buckets of monthly returns (volatility/skew).
- **Rolling volatility** (line) — annualized stdev over a trailing window.
- **Up vs down months** (doughnut) — % of positive months + best/worst labels.

**Holdings & concentration**
- **Concentration / Pareto curve** (line) — cumulative % as holdings are added.
- **Size vs return scatter** (bubble) — weight (x) vs total return % (y), bubble = MV.
- **Holdings treemap** — boxes sized by MV, green/red by gain/loss (needs a
  Chart.js treemap plugin).
- **Book vs market by holding** (grouped bar) — per-holding cost vs current value.
- **Holding tenure** (hbar) — days held since first buy ("diamond hands").

**Dividends & income**
- **Yield-on-cost vs current yield by holding** (grouped bar). _(% renderer)_
- **Dividend growth rate by holding** (bar) — YoY change per ticker. _(% renderer)_
- **Income by sector** (doughnut) — which sectors pay you most.
- **Withholding tax by year** (bar) — foreign-tax drag over time.

**Behaviour & cash flow**
- **Net buys vs sells over time** (bar) — net buyer/seller each period.
- **Invested % over time** (line) — how deployed vs cash-heavy historically.
- **Trading activity heatmap** — number of trades per month.

**On-theme / fun**
- **Portfolio "flavour" radar** — tilts across growth/value, income, risk, US/CAD,
  large/small; a one-glance fingerprint (pairs with the dashboard flavour line).
  - **Name it after a real ice-cream flavour.** Map the radar profile to an actual
    flavour and label the portfolio with it — e.g. high-risk/tech → *Rocky Road*,
    dividend/stable → *Vanilla Bean*, diversified → *Neapolitan*. Seed the choice
    from the profile (not pure random) so it's meaningful, but pull the flavour
    **names** from a stored list gathered via a free API (Open Food Facts
    `ice-creams` category or TheMealDB), refreshed occasionally. Ties the radar,
    the dashboard flavour line, and the branding together.
- **🍨 "The Melt" doughnut** — drag breakdown (fees / MER / withholding /
  cash-vs-inflation / tax); the chart companion to the Melt tab.
- **🍦 Ice-cream cone stack** — allocation as a stack of "scoops" on a cone:
  biggest holding is the bottom scoop, each smaller holding a smaller scoop
  stacking upward, so the tower visually narrows toward the top (a cone, not a
  uniform bar). Could render as a stacked/funnel bar styled as scoops on a cone.
- **🌳 Net-worth growth rings** — polar-area chart, one ring per year, net worth
  stacking like tree rings.
- **☀️ Ice-cream-weather calendar** _(non-finance easter egg)_ — daily city highs
  from the free Open-Meteo API (no key), marking the "ice cream weather" days.
- **🍧 Flavour log** _(non-finance easter egg)_ — a doughnut of the real ice-cream
  flavours you've eaten (manually logged); zero financial purpose, pure fun.

**Free-API easter-egg charts (mostly no key needed)**
- **🌍 Price of a scoop around the world** — *Frankfurter* / *exchangerate.host*
  (free FX, no key). A fixed scoop price (e.g. CAD $4.50) converted to ~15
  currencies → bar chart. Sneakily reuses the app's FX wheelhouse.
- **📍 Scoops near me** — *OpenStreetMap Overpass API* (free, no key). Query
  `amenity=ice_cream` within X km of your city → count by neighbourhood / distance
  to nearest scoop.
- **🍫 Sugar in your scoop** — *Open Food Facts API* (free, no key). The
  `ice-creams` category → average sugar/fat across popular brands, or products by
  country.
- **🌡️ Melt-o-meter** — *Open-Meteo* (free, no key). Forecast/historical highs →
  a predicted scoop melt rate / ice-cream-days-per-month curve.
- **☀️ Ice-cream daylight** — *Sunrise-Sunset API* (free, no key). Daylight hours
  by month → "prime scooping hours" across the year. Gloriously pointless.
- **🍸 Affogato finder** — *TheCocktailDB* (free) search for ice-cream drinks /
  affogato (coffee + ice cream, on brand) → a little flavour gallery.
- **😂 Scoop of the day joke** — *icanhazdadjoke* (free, no key) for a finance/dad
  joke, or *Advice Slip API* for "advice" paired with a *not financial advice* wink.
- **🔢 Portfolio number fact** — *Numbers API* (free) → a fun trivia fact about your
  exact net-worth number.
- _Glue:_ **ip-api.com / ipapi.co** (free, no key) auto-detects the user's **city**,
  so "Scoops near me", the weather/melt charts, and daylight all work without
  asking for a location.
- **🍨 Flavours of the world** — *TheMealDB* (free) or *Wikidata SPARQL* (free).
  Dessert/ice-cream recipe or named-flavour counts by country → a doughnut of
  global flavour diversity.

_Effort: most are small once a builder pattern exists — one `charts.py` function
over already-computed data + a catalog entry. Heatmaps/treemap/radar need a small
Chart.js plugin or a custom render; the two easter eggs need a tiny store (and the
weather one a cached API call)._

**Priority:** Impact: Low–Med _(finance charts Med; easter eggs Low)_ · Effort: small each _(heatmaps/treemap/radar need a plugin)_

## Dashboard — daily swing widget (is today's move outside your usual range?)

A **dashboard widget** (not the hero — a regular movable widget like the others) that
reads **how the whole portfolio (or a single account) is moving today** at a glance.
Today the day move only shows per holding; there's no single "the portfolio is +1.8%
today" pulse at the account/total level.

- **The real point — abnormal-swing flag:** the value isn't to label a "good day / bad
  day" (that's just one way to read it). It's to show when **today's change is outside
  your usual swing** — i.e. compare today's % move against the portfolio's typical daily
  range (e.g. its recent daily-move stdev) and highlight it when it's unusually large in
  either direction. A normal ±0.5% day looks calm; a −3% day stands out.
- **Compute:** for each holding, day move = (live price − prior close) × shares, summed
  across the scope → a portfolio $ and % day change. yfinance already gives the previous
  close (`previousClose`/`regularMarketPreviousClose`); cache it next to the live price in
  `price_cache` so no extra calls. For the "usual swing," keep a short trailing series of
  daily % moves (or derive stdev from recent history) and express today as a z-score /
  percentile ("biggest move in 3 months").
- **UI:** a small dashboard widget — the day $/% number, coloured green→red, with a
  subtle marker when it's outside the normal band. Optional per-account version on Accounts.
- **Effort:** small–medium — a prior-close field in the price cache + a day-move
  aggregation + a dashboard widget. Reuses the cached-price refresh loop.

**Priority:** Impact: Med · Effort: small–medium

## Dashboard / Holdings — trend ("treading") indicator per ticker (and per account)

A simple **up / down / flat trend** marker for each ticker — which way is it treading?
Sits next to each holding (Holdings rows, dashboard widget) as a little ↑/↓/→ arrow.

- **Open question (decide later):** how to define the trend window is undecided — could
  be vs. a moving average (e.g. price vs 50-day MA), the sign of the last N days' return,
  a short-term slope, or "above/below your average cost." Pick the definition during design.
- **Per account / portfolio:** the same trend marker rolled up to an account or the whole
  portfolio — though over a long enough window this "should be always up haha," so the
  account version is more useful on a **short** window (e.g. last week/month treading
  up or down) than all-time.
- **Effort:** small once the trend rule is chosen — a per-ticker classifier over price
  history (cache it) + an arrow/colour in the row and widget. Ties into the daily-swing
  widget and the parked Rolling-return / Rolling-volatility ideas.

**Priority:** Impact: Med · Effort: small _(once the trend rule is chosen)_

## Performance — "tread" chart (portfolio beta vs. staying even)

A chart that shows the portfolio/account **drift relative to a flat baseline** — i.e.
how much it swings versus just "staying even." Two flavours, pick one or both:

- **Value vs. flat line:** plot account value against a horizontal reference at the
  starting value (or contributions line), shading above green / below red — the visual
  gap is "how far ahead/behind staying even" you are.
- **Beta / sensitivity:** compute the portfolio's **beta** vs a benchmark (S&P/TSX) from
  the performance series — how amplified the swings are vs the market. Per-holding beta
  is already cached (`price_service` `info['beta']`); a portfolio-level beta is the
  weighted blend, and a rolling beta line shows how it changes over time.
- **Where:** the Performance tab (a toggle/overlay on the existing value chart) or a
  Charts-tab catalog entry. Pairs with the parked Max-drawdown and Rolling-volatility ideas.
- **Effort:** medium — value-vs-flat is small (a flat dataset on the existing series);
  the beta version needs a benchmark-return alignment + covariance from the series.

**Priority:** Impact: Med · Effort: medium _(value-vs-flat alone is small)_

## Charts — descriptions + hide / favourite (star) charts

Make the Charts tab (and dashboard widgets) easier to navigate and personalise.

- **Quick chart description:** a one-line "what am I looking at" caption (or an ⓘ
  tooltip/info icon) on each chart, pulled from a `description` field added to each
  `CHART_CATALOG` entry in `charts.py`. Cheap once the field exists.
- **Hide charts:** let the user hide charts they don't care about so the tab only shows
  what they want — a per-chart toggle (eye icon), persisted in localStorage like the
  existing dashboard/chart layouts.
- **Favourite / star charts:** star the ones you check often so they float to the top
  (or feed a "Favourites" row on the dashboard). Persist starred IDs in localStorage.
- **Effort:** small — a `description` field per catalog entry + a tiny show/hide/star
  state in `charts.html` saved to localStorage (mirrors the layout-persistence pattern).

**Priority:** Impact: Med · Effort: small

## Internationalization — German, Spanish, French (in that order)

Add multi-language support so the UI can render in other languages. Priority order:
**German → Spanish → French**.

- **How:** extract the user-facing strings (nav labels, tab headings, table headers,
  button text, flash messages) into a translation layer — e.g. Flask-Babel with `.po`
  catalogs, or a lighter JSON dictionary per locale loaded into the templates. A
  language picker on the Settings page persists the choice (a `language` setting +
  localStorage for client-side strings).
- **Scope notes:** keep ticker symbols, currency codes, and account names untranslated;
  number/date/currency formatting is locale-aware but **all monetary values stay in CAD**
  (the locale only changes grouping/decimal style, not the currency). Chart.js labels
  feed from the same string layer.
- **Effort:** medium–large — mostly the one-time string extraction + a catalog per
  language; German first, then Spanish, then French. The plumbing (picker + lookup) is
  built once and the later languages are just more catalogs.

**Priority:** Impact: Med · Effort: medium–large _(plumbing is one-time; each extra language is a catalog)_

## Accounts — Savings account type with recurring interest

A dedicated **Savings** account type that **auto-pays interest** on its cash balance,
so a high-interest savings account (HISA) tracks itself instead of needing a manual
interest transaction each month.

- **Account type:** add "Savings" to the editable account-type list. Per-account
  settings: an **annual interest rate** and a **payout cadence** (monthly / quarterly /
  annual) and compounding basis.
- **Recurring interest:** on each period, generate an Interest cash transaction =
  balance × (rate ÷ periods/yr) on the account's current cash. Reuses the parked
  **recurring/scheduled transaction** engine (see "Transactions — recurring / scheduled
  manual add") — a savings account is just a recurring interest rule seeded from the
  account's rate + balance.
- **Other recommended settings:** optional **interest tiers** (rate by balance band),
  a **promo rate + expiry**, and treating accrued-but-unpaid interest in the daily
  value. Interest is taxable (non-registered) — flows into the Tax tab's income section.
- **Effort:** medium — a Savings type + rate/cadence fields + an interest generator
  (best built on the recurring-transaction rule engine). Pairs with the GIC-interest
  and recurring-transaction items already parked.

**Priority:** Impact: Med · Effort: medium _(rides on the ⭐ recurring-transaction engine)_

## ⭐ RDSP/Retirement — target nest-egg + minimum-income floor (TOP near-term pick)

A **target nest-egg number** that guarantees a chosen **minimum retirement income**, with
an option to keep that income **safe from all but the most severe crashes**. The income
figure is **editable now** (eventually fed from a separate budgeting app). Plugs straight
into the Glide Lab: instead of "most efficient hedge for the modeled crash", it answers
**"how much must I de-risk to protect $X/yr of income through retirement?"** — turning the
break-even/worst-year-floor machinery into a goal-seek on a needed-income floor.

**Priority:** Impact: High · Effort: medium _(reuses the Glide Lab floor/break-even engine)_

## Retirement tab — generalize the RDSP tab to RRSP (and other retirement accounts)

Let the RDSP tab's accumulation→drawdown projection, glide/flat de-risking, and income/tax
modeling also serve **RRSP** (then RRIF/LIRA/LIF), renaming it a **"Retirement" tab**. RRSP
lacks RDSP's grants/holdback but shares everything else; the Glide Lab applies directly.

**Priority:** Impact: High · Effort: large

## Portfolio composition over time

Show not just **value** over time but how the **mix** evolved — asset type, allocation,
beta, blended risk — as a stacked-area / multi-line view, per account or overall. (Extends
the parked rolling-beta-over-time chart into a full composition history.)

**Priority:** Impact: Med · Effort: medium–large

## Dashboard — combined self-directed + managed widget (listed separately)

A widget that shows **both** self-directed and managed holdings/value in one place but
**clearly separated into two sections** (self-directed first, then a "Managed" subgroup
with its own subtotal) — so you get the full picture at a glance without managed
muddying the self-directed list. Complements Top Holdings (which is self-directed-only)
and the dashboard total click-switch. _Effort: small — a grouped variant of the
holdings widget keyed on `Account.managed`._

**Priority:** Impact: Low–Med · Effort: small

## Read-only API endpoint — investment data source for the budget app

A small **read-only JSON endpoint** (e.g. `GET /api/summary`) exposing this app's
headline investment numbers — total invested value, value by account, cash, day
change, all-time gain — so a **separate budgeting/accounts app** (the user is building
one for income/spending/budgets) can pull the investment figure into its net-worth
view. This app stays investment-only; the endpoint is the clean integration seam
(pairs with the existing point-in-time snapshot + transactions CSV export). Keep it
local-only / token-gated since it's personal data. _Effort: small — one route over
`get_dashboard_stats` / `get_account_summary`._ Enables the "Budget / account-manager
app integration" item below.

**Priority:** Impact: Med · Effort: small

## Tax — managed-account handling + a fuller tax engine

Managed accounts are currently **excluded** from the Tax tab (a fund runs its own
trades/income/tax), with a warning note reminding the user to check **non-registered**
managed accounts separately. Two follow-ups parked:

- **Proper non-registered managed tax.** Rather than excluding them, optionally fold a
  non-registered managed account's realized gains / income into the tax estimate (it's
  still the user's to report) — e.g. a per-account "include in tax" override, or import
  the broker's T-slip / realized-gain summary for managed accounts where we can't see
  the internal trades.
- **Better tax system overall.** Beyond capital gains: eligible-dividend gross-up +
  dividend tax credit, interest at full marginal, US withholding as a foreign tax
  credit, and a year-end tax package (see the parked items). Managed-account handling
  folds into this.

**Priority:** Impact: Med _(don't-miss-a-tax-bill safety)_ · Effort: medium–large

## Tax — asset-location score (tax-efficient placement)

Flag holdings that sit in a tax-inefficient account given Canadian rules, using the
registered/non-reg data already on each account: e.g. **US dividend payers in a TFSA**
(15% withholding is unrecoverable there but exempt in an RRSP), **interest/bonds in a
non-registered** account (taxed at full marginal vs. sheltered), foreign income vs.
the foreign-tax-credit. Output a per-holding "consider moving to X" hint + an overall
asset-location score. Genuinely actionable and Canada-specific. _Effort: medium — a
rules pass over holdings × account type × asset class (look-through already cached)._

**Priority:** Impact: Med · Effort: medium

## Holdings — manual price / NAV override per ticker

Let any holding carry a **user-entered price/NAV** used when yfinance can't value it
(some Canadian mutual funds, private/illiquid holdings, pre-IPO, delisted names like
AVTE). Foundational — it makes *anything* trackable and feeds value everywhere
(holdings, dashboard, snapshot, rebalancer). Show a small "manual price" badge + last-set
date; live price takes over again if the symbol later resolves. _Effort: small–medium —
a `manual_price` field (Setting or PriceCache column) + a fallback in the price read._

**Priority:** Impact: Med · Effort: small–medium

## Performance — money-weighted vs time-weighted, side by side

The tab already computes **TWR** and money-weighted CAD benchmarks; add the portfolio's
own **money-weighted return (IRR/XIRR)** next to its TWR as two stat cards. The gap
tells a real story — TWR = how the holdings did, MWR = how *your contribution timing*
did. _Effort: small — an XIRR over the dated cash flows you already have._

**Priority:** Impact: Med · Effort: small

## Dividends — payout safety / cut detection

A per-holding **safety flag** from the dividend history you already store: detect a
**cut or suspension** (a payment lower than the trailing run, or a missed period) and a
**payout-trend** arrow (growing / flat / shrinking). Surfaces risk for the dividend-heavy
user before it shows up in income. Pairs with the parked per-ticker drill-down.
_Effort: small — a pass over each ticker's Dividend rows._

**Priority:** Impact: Med _(dividend-friend feature)_ · Effort: small

## Tax — tax-loss harvesting finder (cool + actually saves money)

Scan for **non-registered** positions sitting at an unrealized loss that could be sold
to **offset realized gains**, and rank them by loss harvested. The differentiator:
this app **already computes the Canadian superficial-loss flag** (±30-day repurchase
check in `get_tax_summary`), so the finder can flag when selling-then-rebuying would
deny the loss, and suggest a **not-substantially-identical** swap to stay invested.
Show: harvest candidates, $ loss available, tax saved at the marginal rate, and the
30-day window. Professional-advisor-grade, and rare in that it's *cool and pays for
itself*. _Effort: medium — a pass over non-reg holdings + the existing superficial-loss
logic + a marginal-rate calc._

**Priority:** Impact: **High** · Effort: medium _(strong "cool but genuinely useful" pick — build after the ⭐ nest-egg floor)_

## AI / smart — portfolio commentary + "Ask your portfolio"

LLM-powered layer over the structured data (Claude API):
- **Auto commentary** — a monthly/quarterly plain-English recap ("up 3.2%, led by NVDA;
  RDSP grant maxed; CJFGX dragged; cash is sitting idle") generated from the dashboard /
  performance / dividend numbers already computed.
- **Ask your portfolio** — a natural-language query box ("how much did I make on Apple?",
  "what's my dividend income this year?", "which account is most tax-inefficient?")
  answered from the data (tool-call the existing `calculations` functions / a read view).
- **Watch:** keep it grounded — feed real computed numbers, don't let it free-hallucinate;
  redact / local-key the API use since it's personal financial data.
- **Effort:** medium — a prompt + a small tool/function layer exposing the existing stats.

**Priority:** Impact: Med _(novel, very differentiating)_ · Effort: medium

## Performance — "what-if" time machine (decision replay)

Replay the portfolio's path under **alternate choices**: didn't sell AVTE, started the
PAC a year earlier, held instead of trimmed, never bought X. Reuses your transactions +
historical prices (the same `_holdings_acb` + `price_on` machinery as the snapshot) to
re-derive an alternate value series and compare against actual. Fascinating and
instructive ("that sale cost you $X"). _Effort: medium — a transaction-edit overlay +
re-run of the historical valuation._

**Priority:** Impact: Low–Med · Effort: medium

## Charts — true-diversification / correlation heatmap

A correlation matrix of the holdings' returns (from cached price history) + a
**"diversification score"** — surfaces the "you *think* you're diversified, but
everything moves together" reality (e.g. 8 tech-adjacent names). A coloured grid + one
headline number. _Effort: small–medium — pairwise correlation over ~1yr daily returns
(numpy, already available) + a Chart.js matrix/heatmap plugin._

**Priority:** Impact: Med · Effort: small–medium

## Budget / account-manager app integration (much later)

Two-way link with an external budgeting app: pull **automatic transactions** with
**selectable note categories** + extras. User already has a preferred budget app; the main
win is auto-transactions instead of manual entry.

**Priority:** Impact: Med · Effort: large (external integration)

## Robust "generate sample" / demo data

A sample-data generator that exercises **all** features and special accounts/tabs (RDSP,
GICs, glide, every account type, recurring rules, etc.) so the demo shows the whole app.
Replaces/expands the current sample button.

**Priority:** Impact: Low–Med · Effort: medium

## Settings — curate available account types

A Settings option to pick **which account types appear in the dropdown** (hide unused ones).
All types kept in the model; this just trims the picker. Available now: Non-Reg, TFSA, RRSP,
FHSA, RDSP, RESP, LIRA, LIF, RRIF, Savings.

**Priority:** Impact: Low · Effort: small

## Market research — sector growth (tab or widget)

Best-performing market **sectors over 1 / 5 / 10 / 25 years**, plus current growth-by-sector.
Could be a dedicated tab or a dashboard widget/chart. Needs a market/sector index data source.

**Priority:** Impact: Med · Effort: medium–large (needs external data)

## Dashboard/Charts — surface the RDSP projection

Make the **RDSP projection** (and maybe the Glide-Lab flat-vs-glide overlay) available as a
selectable **dashboard widget** and/or **Charts-tab** chart, so it's visible without opening
the RDSP tab.

**Priority:** Impact: Low–Med · Effort: small–medium

## Top movers — biggest gainers/losers

Biggest gainers & losers over **day / week / month / year / all-time**, as a dashboard widget
and/or chart. Needs per-holding historical price deltas.

**Priority:** Impact: Med · Effort: medium _(needs historical deltas)_
