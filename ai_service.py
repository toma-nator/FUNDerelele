"""AI rebalance-plan engine — provider-agnostic.

Turns one account's Rebalancer gaps (+ holdings, cash, watchlist candidates) into a
concrete, web-grounded buy plan with real tickers. Two backends behind one shape:

  • Claude  — `anthropic` SDK, claude-opus-4-8, server-side web_search, structured output
  • ChatGPT — `openai` SDK (Responses API), gpt-5.5, web_search tool, structured output

`build_payload()` assembles the minimized input (no account number); `generate_rebalance_plan()`
dispatches to a backend and returns a single dict matching PLAN_SCHEMA (+ `_provider`/`_model`).
Validation, watchlist add, and caching live in later phases.
"""

import json
from datetime import datetime, date

DEFAULT_CLAUDE_MODEL = 'claude-opus-4-8'
DEFAULT_CHATGPT_MODEL = 'gpt-5.5'

REGISTERED_TYPES = {'TFSA', 'RRSP', 'FHSA', 'RDSP', 'RESP', 'LIRA', 'RRIF', 'LIF'}

# Caps are expressed as a % of the cash being deployed.
SINGLE_STOCK_CAP_PCT = 20      # any one individual stock
VERY_HIGH_RISK_CAP_PCT = 10    # any one Very-High-risk single name
MIN_TRADE_CAD = 250
MAX_NEW_NAMES = 8


class AIError(Exception):
    """A run-time failure talking to the provider (surface the message to the user)."""


class AIConfigError(AIError):
    """Missing/invalid API key or unselectable provider — a setup problem."""


# ── Output schema (shared by both providers) ─────────────────────────────────────
# All properties required + additionalProperties:false so it satisfies both Anthropic
# structured outputs and OpenAI strict mode. Optional values use nullable types.
_ALT = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'ticker': {'type': 'string'},
        'profile': {'type': 'string'},      # e.g. "ETF · lower risk" / "mid-cap stock"
        'drift_note': {'type': 'string'},   # how swapping it shifts other targets ("" if none")
    },
    'required': ['ticker', 'profile', 'drift_note'],
}
_SRC = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'title': {'type': 'string'}, 'url': {'type': 'string'}},
    'required': ['title', 'url'],
}
_REGION_SPLIT = {  # one slice of a multi-region fund's geographic allocation
    'type': 'object', 'additionalProperties': False,
    'properties': {'region': {'type': 'string'}, 'pct': {'type': 'number'}},
    'required': ['region', 'pct'],
}
_TRADE = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'ticker': {'type': 'string'},
        'action': {'type': 'string', 'enum': ['Buy', 'Sell']},
        'sector': {'type': 'string'},
        'risk_bucket': {'type': 'string'},
        'market_cap': {'type': 'string'},
        'region': {'type': 'string'},               # USA / Canada / International / Emerging
        'region_split': {'type': 'array', 'items': _REGION_SPLIT},  # [] unless multi-region fund
        'amount_cad': {'type': 'number'},
        'shares_est': {'type': ['number', 'null']},
        'currently_held': {'type': 'boolean'},
        'is_fund': {'type': 'boolean'},     # ETF/mutual fund vs single security
        'rationale': {'type': 'string'},
        'gaps_addressed': {'type': 'array', 'items': {'type': 'string'}},
        'alternates': {'type': 'array', 'items': _ALT},
        'sources': {'type': 'array', 'items': _SRC},
    },
    'required': ['ticker', 'action', 'sector', 'risk_bucket', 'market_cap', 'region',
                 'region_split', 'amount_cad', 'shares_est', 'currently_held', 'is_fund',
                 'rationale', 'gaps_addressed', 'alternates', 'sources'],
}
_WL = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'ticker': {'type': 'string'},
        'company': {'type': 'string'},
        'note': {'type': 'string'},
    },
    'required': ['ticker', 'company', 'note'],
}
PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'thesis': {'type': 'string'},               # one-liner headline for the report cover
        'summary': {'type': 'string'},
        'trades': {'type': 'array', 'items': _TRADE},
        'new_watchlist': {'type': 'array', 'items': _WL},
        'cap_notes': {'type': 'array', 'items': {'type': 'string'}},
        'leftover_cash': {'type': 'number'},
        'caveats': {'type': 'array', 'items': {'type': 'string'}},
        'risks_remaining': {'type': 'array', 'items': {'type': 'string'}},  # what the plan does NOT fix
    },
    'required': ['thesis', 'summary', 'trades', 'new_watchlist', 'cap_notes',
                 'leftover_cash', 'caveats', 'risks_remaining'],
}


# ── Prompt ───────────────────────────────────────────────────────────────────────

_STYLE_GUIDANCE = {
    'etf_heavy': ("IMPLEMENTATION STYLE: ETF-HEAVY (passive). Fill each gap with sector- or "
                  "asset-class-specific ETFs/funds. Introduce an individual stock only where no "
                  "suitable fund exists for that exposure."),
    'mixed': ("IMPLEMENTATION STYLE: MIXED — your most thorough style; use its full depth. Deliberately "
              "blend BOTH instrument types in one plan: broad ETFs for core and hard-to-pick exposures, "
              "AND a genuine set of individual stocks (aim for several — roughly 3–6 single names, not "
              "just one) as satellites that capture the sector, market-cap, region and risk tilts and add "
              "high-conviction upside. A plan that is all-ETF with a single token stock is NOT a mixed "
              "plan — redo it. Research and justify each individual name with web search. This should be "
              "your most detailed, research-heavy plan, with noticeably more single-name work than the "
              "ETF-heavy style."),
    'stock_heavy': ("IMPLEMENTATION STYLE: STOCK-HEAVY (active). Prefer individual securities chosen "
                    "via research; use ETFs only for exposure that can't reasonably be obtained with "
                    "single names."),
}


def _system_prompt(style):
    return f"""You are a professional portfolio analyst assisting a Canadian self-directed investor. \
Turn the account's Rebalancer "gaps" into a concrete, executable BUY plan that consolidates as many \
gaps as possible, then source specific securities that fit.

GOAL — FULL REBALANCE
- Produce a FULL REBALANCE across ALL targeted dimensions (sector, asset class / allocation, blended \
risk, market cap, beta, currency — whichever have targets). BUY under-target gaps and SELL over-target \
holdings, funded by the sells plus available cash. Net cash used (total Buys − total Sells) must NOT \
exceed cash_to_deploy; report leftover_cash = cash_to_deploy + total Sells − total Buys.
- PRIORITISE BY DOLLAR SIZE ACROSS ALL DIMENSIONS, not dimension by dimension: address the single \
largest remaining cash gap first — whatever dimension it sits in — then the next largest, and so on down \
to the smallest, until the cash is deployed. Each security affects several dimensions at once, so after \
every pick re-evaluate which gap is now largest. Do NOT privilege the Sector dimension; sector exposure \
should fall out naturally from the securities you choose.
- When choosing HOW to fill a gap, weight keeping BLENDED RISK and ASSET ALLOCATION on target most \
heavily (these matter most), then the remaining tilts. Prefer one security that closes several large \
gaps at once. Note the single biggest trade-off you had to make in caveats.

POSITION CAPS (as a % of cash_to_deploy)
- ETFs / diversified funds: no cap (they're already diversified).
- Any one individual stock: at most {SINGLE_STOCK_CAP_PCT}%.
- Any one Very-High-risk individual name: at most {VERY_HIGH_RISK_CAP_PCT}%.
- Minimum trade size: ${MIN_TRADE_CAD}. At most {MAX_NEW_NAMES} new names.
- When a cap stops you from filling a gap with your preferred name, record it in cap_notes — state the \
ticker, what you wanted to deploy, the cap, and where you rerouted the rest.

ALLOCATION DATA
- `allocation_targets` gives, per targeted dimension, EVERY bucket's current % vs target % and its dollar \
drift (positive = over target, negative = under target). Read the whole map: buy into the under-target \
buckets and trim the over-target ones. Over-target buckets list their `sell_candidates` — the holdings \
actually sitting in that bucket, i.e. what you can sell to fund the rebalance.
- `current_holdings` includes each position's region, sector, market-cap, risk, yield and forward income. \
Where two holdings are redundant (same region + asset class + market cap), prefer CONSOLIDATING into one \
rather than stacking more of the same exposure.

SELLS
- Size each Sell to bring an over-target bucket toward its target; trim the most over-target / highest-risk \
overweight first, choosing from that bucket's `sell_candidates`. Sells are "Sell" actions on tickers the \
account currently holds, and their proceeds fund the buys. Do not sell a holding that isn't over target.
- A FULL rebalance trims over-target buckets via sells AND deploys the cash — deploying idle cash is NOT a \
substitute for trimming an overweight (e.g. an over-target mega-cap or sector). Protecting specific names \
per `preferences` does not waive this: still trim the OTHER over-target positions so that every targeted \
dimension is brought toward target, not just the ones cash alone can fix.
- Sell when it materially improves the rebalance, not for its own sake — prefer the fewest trades that get \
each dimension to target. You may trim an over-target holding, including a preference-protected name up to \
its stated limit (e.g. VTI up to 15%), when that is what brings a dimension to target; an unprotected \
overweight (e.g. a redundant mega-cap fund) should be trimmed without hesitation.

PREFERENCES
- If `preferences` is present, treat it as the investor's standing instructions (e.g. names to keep, an \
income lean, a cash buffer, per-name limits) and HONOUR it — it overrides the default guidance where they \
conflict. Note in caveats anything you couldn't satisfy.

SECURITIES — MUST BE REAL
- Use real, currently-listed tickers. VERIFY each candidate with web search before recommending it.
- Use correct symbols for the exchange: Canadian listings use a .TO suffix; Canadian Depositary \
Receipts use .NE; US listings are plain.
- Prefer the investor's existing holdings and watchlist candidates where they fit; introduce new names \
only when they close a gap better.

REGISTRATION
- This is a {{account_type}} account: {{tax_note}}

ALTERNATES & SOURCES (per trade)
- For each pick give one alternate filling the SAME role — same primary sector and a comparable risk / \
market-cap contribution — so swapping it keeps the plan balanced. If the only sensible alternate is a \
different instrument type (stock vs ETF) with a materially different profile, still offer it but put the \
trade-off in its drift_note (e.g. "ETF — lower risk, shifts Very High down ~$2k"). Use "" when there's \
no meaningful drift.
- Populate sources with the web pages you used to verify/justify a NEW name (title + url). Existing \
holdings need no sources.

WEB-SEARCH PRIVACY
- Your web-search queries MUST be generic — sector, asset class, risk, market-cap, geography, yield, \
exchange. NEVER include the investor's balances, dollar amounts, account identifiers, or holdings list \
in a search query.

{_STYLE_GUIDANCE.get(style, _STYLE_GUIDANCE['mixed'])}

SELF-CHECK — before returning the plan, verify each of these and fix anything that fails:
- Net cash used (total Buys − total Sells) does not exceed cash_to_deploy, and leftover_cash matches any \
cash buffer the investor asked for.
- No individual stock exceeds its cap; no Very-High single name exceeds its cap.
- EVERY targeted dimension was actually moved toward target — not only the ones cash alone can fix; trim \
over-target buckets where needed.
- No holding was sold that isn't over target.
- The resulting BLENDED-RISK mix lands on its target (the investor's top priority); if not, adjust the \
trades until it does.

OUTPUT
- Return only the structured object. Set is_fund=true for ETFs/mutual funds. shares_est may be null. \
gaps_addressed lists the gaps each trade closes (e.g. "Sector: Healthcare", "Risk: Very High").
- For every trade set `region` to its dominant geographic region — exactly one of "USA", "Canada", \
"International" (developed ex-North-America), or "Emerging". For a multi-region fund (an all-in-one or \
global ETF), ALSO fill `region_split` with the approximate percentage per region (research the fund's \
regional allocation via web search when it isn't obvious from the name); the percentages should sum to \
~100. Leave `region_split` as an empty array for single-region funds and individual stocks.
- Write a substantial, professional `summary` (about 4–7 sentences): the overall strategy and why this \
shape; how you prioritised the dollar gaps; how you balanced the Blended-Risk and Market-Cap tilts; what \
you sold and why; the single most important trade-off you made; and how the plan shifts the account's \
risk posture. Keep it at the PLAN level — leave per-security detail to each trade's `rationale`, don't \
repeat it.
- Write a `thesis`: one or two sentences naming the single biggest problem with the account as it stands \
and what this plan does about it — the headline a reader sees first, above the summary.
- Populate `risks_remaining` with 2–4 honest, plain-language risks this plan does NOT remove (e.g. still \
heavily weighted to equities; interest-rate sensitivity of any new bonds; USD/FX exposure; a single name \
that's still the largest position after trimming). These are distinct from caveats (which are data/method \
disclaimers).
- End caveats with a one-line "Not financial advice." note."""


# ── Payload assembly ─────────────────────────────────────────────────────────────

def _allocation_targets(account):
    """The FULL per-dimension target map for every dimension with saved targets: each
    bucket's current %, target %, and dollar drift (positive = over, negative = under),
    plus — for over-target buckets — the holdings sitting there (the model's sell
    candidates). Gives the model the complete picture, not just the under-target gaps."""
    from calculations import (REBAL_DIMENSIONS, REBAL_DIM_LABELS, get_rebal_targets,
                              get_rebalancer_data, get_holdings, _bucket_weights)
    from price_service import get_holdings_metadata
    holds = [h for h in get_holdings()
             if h['account'] == account and (h['market_value_cad'] or 0) > 0]
    metas = get_holdings_metadata([h['ticker'] for h in holds]) if holds else {}
    out = {}
    for dim in REBAL_DIMENSIONS:
        if not get_rebal_targets(account, dim):
            continue
        d = get_rebalancer_data(account=account, dimension=dim, mode='full')
        in_bucket = {}
        for h in holds:
            for b, w in _bucket_weights(h, metas.get(h['ticker'], {}), dim).items():
                if w > 0.01:
                    in_bucket.setdefault(b, []).append(h['ticker'])
        rows = []
        for b in d['buckets']:
            if b['target_pct'] <= 0 and abs(b['current_pct']) < 0.05:
                continue   # untargeted and unheld — skip noise
            drift = b['drift']
            row = {'bucket': b['label'], 'current_pct': b['current_pct'],
                   'target_pct': b['target_pct'], 'drift_cad': drift,
                   'status': ('over' if drift > MIN_TRADE_CAD else
                              'under' if drift < -MIN_TRADE_CAD else 'on_target')}
            if drift > MIN_TRADE_CAD and b['label'] != 'Cash':
                row['sell_candidates'] = in_bucket.get(b['label'], [])
            rows.append(row)
        out[REBAL_DIM_LABELS.get(dim, dim)] = rows
    return out


def build_payload(account, style='mixed'):
    """Minimized input for the model — no account number is included."""
    from calculations import (get_rebalancer_gaps_all, get_cash_by_account, get_holdings,
                              _blend_bucket, _cap_bucket, _region_weights, get_rebal_targets,
                              REBAL_DIMENSIONS, REBAL_DIM_LABELS)
    from price_service import get_holdings_metadata, get_fx_rate
    from models import Account

    gaps = [g for g in get_rebalancer_gaps_all() if g['account'] == account]
    cash = round(max(0.0, get_cash_by_account().get(account, 0.0)), 2)
    acct = Account.query.filter_by(name=account).first()
    acct_type = (acct.type if acct else None) or 'Non-Reg'
    registered = acct_type in REGISTERED_TYPES
    tax_note = ("registered / tax-sheltered — ignore US dividend withholding and capital-gains tax; "
                "optimize for total return." if registered else
                "non-registered / taxable — favour Canadian-eligible dividends, be mindful of the 15% "
                "US dividend withholding, and that sells trigger capital gains.")

    holdings = [h for h in get_holdings()
                if h['account'] == account and (h['market_value_cad'] or 0) > 0]
    metas = get_holdings_metadata([h['ticker'] for h in holdings])
    fx = get_fx_rate()
    invested = sum(h['market_value_cad'] for h in holdings) or 1
    current = []
    for h in holdings:
        m = metas.get(h['ticker'], {})
        rate = m.get('dividend_rate')
        if not rate and m.get('dividend_yield') and h.get('live_price'):
            rate = m['dividend_yield'] / 100.0 * h['live_price']
        fwd = (rate * h['qty'] * (fx if h['currency'] == 'USD' else 1.0)) if rate else 0.0
        yld = (fwd / h['market_value_cad'] * 100) if h['market_value_cad'] else None
        rw = _region_weights(h['ticker'], m)
        reg = max(rw, key=rw.get) if rw else 'Unclassified'
        current.append({
            'ticker': h['ticker'],
            'sector': m.get('sector') or '',
            'market_cap_bucket': _cap_bucket(m.get('market_cap')) or '',
            'risk_bucket': _blend_bucket(h['ticker'], m),
            'region': reg,
            'region_split': ({k: round(v * 100) for k, v in rw.items()} if len(rw) > 1 else None),
            'value_cad': round(h['market_value_cad'], 2),
            'weight_pct': round(h['market_value_cad'] / invested * 100, 1),
            'yield_pct': round(yld, 2) if yld is not None else None,
            'fwd_income_cad': round(fwd, 2) if fwd else None,
        })

    cands = {}
    for g in gaps:
        for c in g.get('candidates', []):
            if c.get('source') == 'idea':
                continue  # drop hard-coded curated ETFs — let the AI research its own names
            cands.setdefault(c['ticker'], c.get('source', ''))

    targets_fp = {REBAL_DIM_LABELS.get(d, d): get_rebal_targets(account, d)
                  for d in REBAL_DIMENSIONS if get_rebal_targets(account, d)}

    return {
        'account': {'type': acct_type, 'registered': registered, 'currency': 'CAD',
                    'tax_note': tax_note},
        'cash_to_deploy': cash,
        'allocation_targets': _allocation_targets(account),
        'current_holdings': current,
        'watchlist_candidates': [{'ticker': t, 'source': s} for t, s in sorted(cands.items())],
        'preferences': (_get_setting('ai_preferences', '') or None),
        '_targets_fp': targets_fp,
        'constraints': {
            'etf_cap_pct': None,
            'single_stock_cap_pct': SINGLE_STOCK_CAP_PCT,
            'very_high_risk_cap_pct': VERY_HIGH_RISK_CAP_PCT,
            'min_trade_cad': MIN_TRADE_CAD,
            'max_new_names': MAX_NEW_NAMES,
            'implementation_style': style,
        },
    }


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _get_setting(key, default=''):
    from models import Setting
    s = Setting.query.get(key)
    return s.value if (s and s.value) else default


def providers_available():
    """{'claude': bool, 'chatgpt': bool} — which backends have a key configured."""
    return {'claude': bool(_get_setting('anthropic_api_key')),
            'chatgpt': bool(_get_setting('openai_api_key'))}


def _extract_json(text):
    text = (text or '').strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    a, b = text.find('{'), text.rfind('}')
    if a != -1 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except Exception:
            pass
    raise AIError('The AI response was not valid JSON. Try regenerating.')


def _finish(data, provider, model):
    data['_provider'] = provider
    data['_model'] = model
    return data


# ── Claude backend ───────────────────────────────────────────────────────────────

def _claude_plan(payload, model=None):
    import anthropic
    key = _get_setting('anthropic_api_key')
    if not key:
        raise AIConfigError('No Claude API key set. Add one in Settings → AI Rebalancer.')
    model = model or _get_setting('ai_model_claude') or DEFAULT_CLAUDE_MODEL
    client = anthropic.Anthropic(api_key=key, timeout=600.0)

    system = _system_prompt(payload['constraints']['implementation_style'])
    system = system.replace('{account_type}', payload['account']['type']) \
                   .replace('{tax_note}', payload['account']['tax_note'])
    user = ("Here is the account's rebalancing context as JSON. Produce the plan.\n\n"
            + json.dumps(payload, indent=2))
    # Up to two attempts: a transient server-side error (5xx / overloaded / dropped
    # connection) on a multi-minute run is retried once rather than wasting the run.
    for attempt in range(2):
        messages = [{'role': 'user', 'content': user}]
        try:
            resp = None
            for _ in range(8):  # web-search server loop may return pause_turn; resend to continue
                # Stream (required at this max_tokens to avoid HTTP timeouts). The budget
                # is shared by adaptive thinking AND the full structured plan, so it must be
                # generous or the JSON truncates mid-object. ETF-heavy fit in 32k, but Mixed
                # / stock-heavy plans (more individual names → bigger JSON) need more room.
                with client.messages.stream(
                    model=model, max_tokens=64000,
                    system=system,
                    thinking={'type': 'adaptive'},
                    tools=[{'type': 'web_search_20260209', 'name': 'web_search'}],
                    output_config={'format': {'type': 'json_schema', 'schema': PLAN_SCHEMA}},
                    messages=messages,
                ) as stream:
                    resp = stream.get_final_message()
                if resp.stop_reason == 'refusal':
                    raise AIError('Claude declined this request.')
                if resp.stop_reason == 'pause_turn':
                    messages = [{'role': 'user', 'content': user},
                                {'role': 'assistant', 'content': resp.content}]
                    continue
                break
            if resp is not None and resp.stop_reason == 'pause_turn':
                raise AIError('Claude was still researching after several rounds. Try '
                              'regenerating, or simplify the targets.')
            if resp is not None and resp.stop_reason == 'max_tokens':
                raise AIError('The AI response was cut off before finishing (hit the output '
                              'limit). Try regenerating, or simplify the targets.')
            text = next((b.text for b in resp.content if b.type == 'text'), None)
            return _finish(_extract_json(text), 'Claude', model)
        except anthropic.AuthenticationError:
            raise AIConfigError('Claude API key is invalid or revoked.')
        except (anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            if attempt == 0:
                continue   # one retry on a transient server/connection error
            raise AIError(f'Claude API error after retry: {getattr(e, "message", None) or e}')
        except anthropic.APIError as e:
            if getattr(e, 'status_code', None) == 529 and attempt == 0:
                continue   # overloaded — retry once
            raise AIError(f'Claude API error: {getattr(e, "message", None) or e}')


# ── ChatGPT backend (OpenAI Responses API) ───────────────────────────────────────

def _openai_plan(payload, model=None):
    import openai
    key = _get_setting('openai_api_key')
    if not key:
        raise AIConfigError('No ChatGPT API key set. Add one in Settings → AI Rebalancer.')
    model = model or _get_setting('ai_model_chatgpt') or DEFAULT_CHATGPT_MODEL
    client = openai.OpenAI(api_key=key, timeout=600.0)

    system = _system_prompt(payload['constraints']['implementation_style'])
    system = system.replace('{account_type}', payload['account']['type']) \
                   .replace('{tax_note}', payload['account']['tax_note'])
    user = ("Here is the account's rebalancing context as JSON. Produce the plan.\n\n"
            + json.dumps(payload, indent=2))

    try:
        resp = client.responses.create(
            model=model,
            tools=[{'type': 'web_search'}],
            input=[{'role': 'system', 'content': system},
                   {'role': 'user', 'content': user}],
            text={'format': {'type': 'json_schema', 'name': 'rebalance_plan',
                             'schema': PLAN_SCHEMA, 'strict': True}},
        )
        return _finish(_extract_json(resp.output_text), 'ChatGPT', model)
    except openai.AuthenticationError:
        raise AIConfigError('ChatGPT API key is invalid or revoked.')
    except openai.APIError as e:
        raise AIError(f'ChatGPT API error: {getattr(e, "message", None) or e}')


# ── Dispatcher ───────────────────────────────────────────────────────────────────

def generate_rebalance_plan(payload, provider, model=None):
    """Generate a plan dict (matching PLAN_SCHEMA + _provider/_model) via the chosen backend."""
    if provider == 'chatgpt':
        return _openai_plan(payload, model)
    if provider == 'claude':
        return _claude_plan(payload, model)
    raise AIConfigError(f'Unknown AI provider: {provider}')


# ── Validation (yfinance — free) ─────────────────────────────────────────────────

def validate_plan(plan, account):
    """Resolve every suggested ticker against yfinance (free). Annotates the plan with
    `_verified` (new-watchlist names that resolve and aren't already tracked/held),
    `_skipped` (with a reason), and `_invalid_trades` (trade tickers that don't resolve).
    Adds nothing to the watchlist — that's `add_picks_to_watchlist`."""
    from models import WatchlistItem
    from price_service import fetch_prices_batch, get_holdings_metadata
    from calculations import get_holdings

    held = {h['ticker'].upper() for h in get_holdings()}
    on_wl = {w.ticker.upper() for w in WatchlistItem.query.all()}
    wl_tickers = [w['ticker'] for w in plan.get('new_watchlist', [])]
    trade_tickers = [t['ticker'] for t in plan.get('trades', [])]
    candidates = list({*wl_tickers, *trade_tickers})
    prices = fetch_prices_batch(candidates) if candidates else {}
    metas = get_holdings_metadata(candidates) if candidates else {}

    # Enrich each trade with live price (CAD), an estimated share count, and yield.
    from price_service import get_fx_rate
    fx = get_fx_rate()
    for t in plan.get('trades', []):
        pd = prices.get(t['ticker'])
        if pd and pd.get('price'):
            native, cur = pd['price'], pd.get('currency', 'CAD')
            price_cad = native * (fx if cur and cur != 'CAD' else 1.0)
            t['live_price_cad'] = round(price_cad, 2)
            t['shares_calc'] = round(t['amount_cad'] / price_cad, 2) if price_cad else None
            m = metas.get(t['ticker'], {}) or {}
            dr, dy = m.get('dividend_rate'), m.get('dividend_yield')
            yld = (dr / native * 100) if (dr and native) else (dy if dy else None)
            t['yield_pct'] = round(yld, 2) if yld is not None else None
        else:
            t['live_price_cad'] = t['shares_calc'] = t['yield_pct'] = None

    verified, skipped = [], []
    for w in plan.get('new_watchlist', []):
        tk = w['ticker']
        up = tk.upper()
        if up in held:
            skipped.append({**w, 'reason': 'already held'})
        elif up in on_wl:
            skipped.append({**w, 'reason': 'already on watchlist'})
        elif tk in prices:
            verified.append({**w,
                             'live_price': prices[tk]['price'],
                             'currency': prices[tk].get('currency', 'CAD'),
                             'company': (metas.get(tk, {}) or {}).get('long_name') or w.get('company', '')})
        else:
            skipped.append({**w, 'reason': "couldn't verify ticker"})

    plan['_verified'] = verified
    plan['_skipped'] = skipped
    plan['_invalid_trades'] = [t['ticker'] for t in plan.get('trades', [])
                               if t['ticker'] not in prices and t['ticker'].upper() not in held]

    # Cash-budget transparency: net cash used (buys − sells) must not exceed the cash
    # on hand. over_by > 0 means the model overshot the budget (surface as a warning).
    from calculations import get_cash_by_account
    avail = round(max(0.0, get_cash_by_account().get(account, 0.0)), 2)
    buys = sum(t['amount_cad'] for t in plan.get('trades', []) if t.get('action') == 'Buy')
    sells = sum(t['amount_cad'] for t in plan.get('trades', []) if t.get('action') == 'Sell')
    net_used = round(buys - sells, 2)
    plan['_cash_budget'] = {'available': avail, 'net_used': net_used,
                            'over_by': round(max(0.0, net_used - avail), 2)}
    return plan


def _apply_ai_regions(plan):
    """Seed region overrides from the AI's researched region for picks our heuristics
    leave Unclassified — so a fund yfinance can't classify (no region keyword, no
    country) still lands in the right bucket in the report, rebalancer, and charts.
    Never overwrites a user override or a confident heuristic; honours a weighted
    region_split. Clears the per-request cache so the report enrichment sees it."""
    from calculations import _region_of, region_overrides, _ALL_REGIONS
    from price_service import get_holdings_metadata
    from models import db, Setting
    trades = plan.get('trades', [])
    if not trades:
        return
    metas = get_holdings_metadata([t['ticker'] for t in trades])
    existing = region_overrides()
    s = Setting.query.get('region_overrides')
    data = {}
    if s and s.value:
        try:
            data = json.loads(s.value)
        except Exception:
            data = {}
    changed = False
    for t in trades:
        tk = t['ticker'].upper()
        if tk in existing:
            continue   # a user (or earlier) override already wins
        if _region_of(t['ticker'], metas.get(t['ticker'], {})) != 'Unclassified':
            continue   # our heuristic already classifies it — trust that
        split = {p['region']: p['pct'] for p in (t.get('region_split') or [])
                 if p.get('region') in _ALL_REGIONS and (p.get('pct') or 0) > 0}
        if len(split) >= 2:
            data[tk] = split
            changed = True
        elif t.get('region') in _ALL_REGIONS:
            data[tk] = t['region']
            changed = True
    if changed:
        if s:
            s.value = json.dumps(data)
        else:
            db.session.add(Setting(key='region_overrides', value=json.dumps(data)))
        db.session.commit()
        try:
            from flask import g
            if hasattr(g, '_region_ov'):
                del g._region_ov
        except Exception:
            pass


def add_picks_to_watchlist(plan):
    """Add the validated `_verified` new-watchlist names (run validate_plan first) with an
    AI-provenance note. Returns the list of tickers added."""
    from models import db, WatchlistItem
    provider = plan.get('_provider', 'AI')
    today = date.today()
    existing = {w.ticker.upper() for w in WatchlistItem.query.all()}
    added = []
    for w in plan.get('_verified', []):
        up = w['ticker'].upper()
        if up in existing:
            continue
        db.session.add(WatchlistItem(
            ticker=up,
            company=w.get('company', '') or '',
            currency=(w.get('currency') or 'CAD'),
            added_price=w.get('live_price'),
            added_date=today,
            notes=f"{provider} AI Watch Idea",   # full reasoning lives in the plan's notes
        ))
        existing.add(up)
        added.append(up)
    if added:
        db.session.commit()
    return added


def add_buy_tickers_to_watchlist(plan):
    """Add the plan's new BUY tickers (skipping ones currently held) to the watchlist —
    deduped against what's already tracked, auto-classified from metadata. Returns the
    tickers added. Mirrors add_picks_to_watchlist but sources the trade buys, so you can
    watch the recommended names before executing them."""
    from models import db, WatchlistItem
    from price_service import get_holdings_metadata, get_cached_price, refresh_prices
    provider = plan.get('_provider', 'AI')
    today = date.today()
    existing = {w.ticker.upper() for w in WatchlistItem.query.all()}
    tickers, seen = [], set()
    for t in plan.get('trades', []):
        if t.get('action') != 'Buy' or t.get('currently_held'):
            continue
        up = (t.get('ticker') or '').upper()
        if up and up not in existing and up not in seen:
            seen.add(up)
            tickers.append(up)
    if not tickers:
        return []
    metas = get_holdings_metadata(tickers)
    added = []
    for up in tickers:
        m = metas.get(up, {}) or {}
        cached = get_cached_price(up)
        if not cached:
            refresh_prices([up])
            cached = get_cached_price(up)
        db.session.add(WatchlistItem(
            ticker=up,
            company=m.get('long_name') or '',
            sector=m.get('sector') or '',
            currency='CAD' if up.endswith(('.TO', '.NE', '.V')) else 'USD',
            added_price=cached.price if cached else None,
            added_date=today,
            notes=f"{provider} AI Plan Buy",
        ))
        added.append(up)
    if added:
        db.session.commit()
    return added


# ── Per-account caching (settings table) ─────────────────────────────────────────

def _plan_key(account):
    slug = account.lower().replace(' ', '_').replace('-', '_')
    return f'ai_plan_{slug}'


def compute_fingerprint(payload):
    """Stable hash of the inputs that should invalidate a cached plan when they change."""
    import hashlib
    relevant = {
        'cash': payload.get('cash_to_deploy'),
        'targets': payload.get('_targets_fp'),     # saved targets (stable, not price-noisy)
        'holdings': sorted((h['ticker'], h['value_cad']) for h in payload.get('current_holdings', [])),
        'style': payload.get('constraints', {}).get('implementation_style'),
        'prefs': payload.get('preferences'),
    }
    blob = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def save_cached_plan(account, plan, payload, style, report_data=None):
    from models import db, Setting
    rec = {
        'plan': plan,
        'generated_at': datetime.utcnow().isoformat(),
        'provider': plan.get('_provider'),
        'model': plan.get('_model'),
        'style': style,
        'fingerprint': compute_fingerprint(payload),
        'report_data': report_data,   # free yfinance enrichment, computed once at generation
    }
    key = _plan_key(account)
    s = Setting.query.get(key)
    if s:
        s.value = json.dumps(rec)
    else:
        db.session.add(Setting(key=key, value=json.dumps(rec)))
    db.session.commit()


def load_cached_plan(account):
    """The cached record {plan, generated_at, provider, model, style, fingerprint} or None."""
    raw = _get_setting(_plan_key(account))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def clear_cached_plan(account):
    from models import db, Setting
    s = Setting.query.get(_plan_key(account))
    if s:
        db.session.delete(s)
        db.session.commit()


def plan_is_stale(record, account):
    """True if the cached plan's inputs no longer match the account's current state."""
    if not record:
        return False
    payload = build_payload(account, record.get('style', 'mixed'))
    return record.get('fingerprint') != compute_fingerprint(payload)


# ── Orchestration (THE billable call — only invoke on an explicit user action) ──

def run_and_cache(account, provider, style=None, model=None):
    """Build payload → generate (web search, ~$ per run) → validate → cache. Returns the
    plan dict. This is the only function that costs money; never call it on page load."""
    style = style or _get_setting('ai_impl_style_default', 'mixed')
    payload = build_payload(account, style)
    plan = generate_rebalance_plan(payload, provider, model)
    validate_plan(plan, account)
    _apply_ai_regions(plan)   # capture the AI's region for picks we'd otherwise leave Unclassified
    # Free yfinance enrichment for the deep-dive report + the card — computed once
    # here so page views just read the cache. Best-effort: a data hiccup must never
    # discard the paid plan.
    report_data = None
    try:
        import report_service
        report_data = report_service.build_report_data(account, plan)
    except Exception:
        report_data = None
    save_cached_plan(account, plan, payload, style, report_data)
    return plan
