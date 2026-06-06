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
