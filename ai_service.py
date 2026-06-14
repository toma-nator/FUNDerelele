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
_TRADE = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'ticker': {'type': 'string'},
        'action': {'type': 'string', 'enum': ['Buy', 'Sell']},
        'sector': {'type': 'string'},
        'risk_bucket': {'type': 'string'},
        'market_cap': {'type': 'string'},
        'amount_cad': {'type': 'number'},
        'shares_est': {'type': ['number', 'null']},
        'currently_held': {'type': 'boolean'},
        'is_fund': {'type': 'boolean'},     # ETF/mutual fund vs single security
        'rationale': {'type': 'string'},
        'gaps_addressed': {'type': 'array', 'items': {'type': 'string'}},
        'alternates': {'type': 'array', 'items': _ALT},
        'sources': {'type': 'array', 'items': _SRC},
    },
    'required': ['ticker', 'action', 'sector', 'risk_bucket', 'market_cap', 'amount_cad',
                 'shares_est', 'currently_held', 'is_fund', 'rationale', 'gaps_addressed',
                 'alternates', 'sources'],
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
        'summary': {'type': 'string'},
        'trades': {'type': 'array', 'items': _TRADE},
        'new_watchlist': {'type': 'array', 'items': _WL},
        'cap_notes': {'type': 'array', 'items': {'type': 'string'}},
        'leftover_cash': {'type': 'number'},
        'caveats': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['summary', 'trades', 'new_watchlist', 'cap_notes', 'leftover_cash', 'caveats'],
}


# ── Prompt ───────────────────────────────────────────────────────────────────────

_STYLE_GUIDANCE = {
    'etf_heavy': ("IMPLEMENTATION STYLE: ETF-HEAVY (passive). Fill each gap with sector- or "
                  "asset-class-specific ETFs/funds. Introduce an individual stock only where no "
                  "suitable fund exists for that exposure."),
    'mixed': ("IMPLEMENTATION STYLE: MIXED. Use a diversified ETF as the core of each large gap, "
              "plus a few individual securities as satellites to capture the risk and market-cap tilts."),
    'stock_heavy': ("IMPLEMENTATION STYLE: STOCK-HEAVY (active). Prefer individual securities chosen "
                    "via research; use ETFs only for exposure that can't reasonably be obtained with "
                    "single names."),
}


def _system_prompt(style):
    return f"""You are a professional portfolio analyst assisting a Canadian self-directed investor. \
Turn the account's Rebalancer "gaps" into a concrete, executable BUY plan that consolidates as many \
gaps as possible, then source specific securities that fit.

CASH & SIZING
- Deploy only the cash available (cash_to_deploy). Never exceed it. Account for every dollar and report \
any remainder in leftover_cash.
- Close the primary dollar gaps (the Sector dimension, when present) as precisely as you can, WHILE \
ALSO honouring the secondary tilts (Blended Risk and Market Cap buckets to favour). Prefer single \
securities that satisfy several gaps at once over one-security-per-gap.

POSITION CAPS (as a % of cash_to_deploy)
- ETFs / diversified funds: no cap (they're already diversified).
- Any one individual stock: at most {SINGLE_STOCK_CAP_PCT}%.
- Any one Very-High-risk individual name: at most {VERY_HIGH_RISK_CAP_PCT}%.
- Minimum trade size: ${MIN_TRADE_CAD}. At most {MAX_NEW_NAMES} new names.
- When a cap stops you from filling a gap with your preferred name, record it in cap_notes — state the \
ticker, what you wanted to deploy, the cap, and where you rerouted the rest.

SELLS — ONLY IF REQUIRED
- Deploy the available cash first; this is primarily a cash-deployment plan. Recommend a Sell only when \
(a) a priority gap needs more funding than the cash alone can provide, or (b) a current holding sits in a \
bucket listed in over_target_buckets (it is above its target). Keep sells minimal — trim the most \
over-target / highest-risk overweight first. Sell proceeds fund additional Buys; set \
leftover_cash = cash_to_deploy + total Sells − total Buys (≈ 0 when fully deployed).

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

OUTPUT
- Return only the structured object. Set is_fund=true for ETFs/mutual funds. shares_est may be null. \
gaps_addressed lists the gaps each trade closes (e.g. "Sector: Healthcare", "Risk: Very High"). \
Keep the summary tight and professional. End caveats with a one-line "Not financial advice." note."""


# ── Payload assembly ─────────────────────────────────────────────────────────────

def _over_target_buckets(account):
    """Buckets currently above target across the dimensions that have saved targets, so the
    model can propose trimming an overweight (sells only if required)."""
    from calculations import (REBAL_DIMENSIONS, REBAL_DIM_LABELS, get_rebal_targets,
                              get_rebalancer_data)
    out = []
    for dim in REBAL_DIMENSIONS:
        if not get_rebal_targets(account, dim):
            continue
        d = get_rebalancer_data(account=account, dimension=dim, mode='full')
        for b in d['buckets']:
            if b['drift'] > MIN_TRADE_CAD and b['label'] != 'Cash':
                out.append({'dimension': REBAL_DIM_LABELS.get(dim, dim),
                            'bucket': b['label'], 'over_by_cad': b['drift']})
    return out


def build_payload(account, style='mixed'):
    """Minimized input for the model — no account number is included."""
    from calculations import (get_rebalancer_gaps_all, get_cash_by_account, get_holdings,
                              _blend_bucket, _cap_bucket)
    from price_service import get_holdings_metadata
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
    invested = sum(h['market_value_cad'] for h in holdings) or 1
    current = []
    for h in holdings:
        m = metas.get(h['ticker'], {})
        current.append({
            'ticker': h['ticker'],
            'sector': m.get('sector') or '',
            'market_cap_bucket': _cap_bucket(m.get('market_cap')) or '',
            'risk_bucket': _blend_bucket(h['ticker'], m),
            'value_cad': round(h['market_value_cad'], 2),
            'weight_pct': round(h['market_value_cad'] / invested * 100, 1),
        })

    by_dim, cands = {}, {}
    for g in gaps:
        by_dim.setdefault(g['dimension_label'], []).append({
            'bucket': g['bucket'], 'amount_cad': g['amount_cad'], 'gap_pct': g['gap_pct']})
        for c in g.get('candidates', []):
            if c.get('source') == 'idea':
                continue  # drop hard-coded curated ETFs — let the AI research its own names
            cands.setdefault(c['ticker'], c.get('source', ''))

    return {
        'account': {'type': acct_type, 'registered': registered, 'currency': 'CAD',
                    'tax_note': tax_note},
        'cash_to_deploy': cash,
        'gaps_by_dimension': by_dim,
        'over_target_buckets': _over_target_buckets(account),
        'current_holdings': current,
        'watchlist_candidates': [{'ticker': t, 'source': s} for t, s in sorted(cands.items())],
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
    messages = [{'role': 'user', 'content': user}]

    try:
        resp = None
        for _ in range(6):  # web-search server loop may return pause_turn; resend to continue
            resp = client.messages.create(
                model=model, max_tokens=8000,
                system=system,
                thinking={'type': 'adaptive'},
                tools=[{'type': 'web_search_20260209', 'name': 'web_search'}],
                output_config={'format': {'type': 'json_schema', 'schema': PLAN_SCHEMA}},
                messages=messages,
            )
            if resp.stop_reason == 'refusal':
                raise AIError('Claude declined this request.')
            if resp.stop_reason == 'pause_turn':
                messages = [{'role': 'user', 'content': user},
                            {'role': 'assistant', 'content': resp.content}]
                continue
            break
        text = next((b.text for b in resp.content if b.type == 'text'), None)
        return _finish(_extract_json(text), 'Claude', model)
    except anthropic.AuthenticationError:
        raise AIConfigError('Claude API key is invalid or revoked.')
    except anthropic.APIError as e:
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
    return plan


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
        note = f"AI rebalancer pick · {provider} · {today.isoformat()}"
        if w.get('note'):
            note += f" — {w['note']}"
        db.session.add(WatchlistItem(
            ticker=up,
            company=w.get('company', '') or '',
            currency=(w.get('currency') or 'CAD'),
            added_price=w.get('live_price'),
            added_date=today,
            notes=note,
        ))
        existing.add(up)
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
        'gaps': payload.get('gaps_by_dimension'),
        'over': payload.get('over_target_buckets'),
        'holdings': sorted((h['ticker'], h['value_cad']) for h in payload.get('current_holdings', [])),
        'style': payload.get('constraints', {}).get('implementation_style'),
    }
    blob = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def save_cached_plan(account, plan, payload, style):
    from models import db, Setting
    rec = {
        'plan': plan,
        'generated_at': datetime.utcnow().isoformat(),
        'provider': plan.get('_provider'),
        'model': plan.get('_model'),
        'style': style,
        'fingerprint': compute_fingerprint(payload),
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
    save_cached_plan(account, plan, payload, style)
    return plan
