"""RDSP tab assembly layer: bridges the DB (transactions + RDSPPlanYear) and the
pure `rdsp` engine into a chart/stat payload for the template.

Keeps `rdsp.py` free of any DB/Flask coupling. Money crosses into here as cents
(engine convention) and leaves as **dollars** in the payload (the chart speaks $).
"""
from datetime import date

from models import db, Transaction, Account, Setting, RDSPPlanYear
import rdsp

# Return presets (labels → annual rate). "Current" is computed from the account's
# realized money-weighted return, so it isn't in this fixed table.
RETURN_PRESETS = {'Safe': 0.04, 'Low': 0.05, 'Target': 0.07, 'Growth': 0.09, 'Aggressive': 0.11}
DEFAULT_PRESET = 'Target'
BAND_LOW, BAND_HIGH = 'Safe', 'Aggressive'  # the shaded range cone
HORIZON_AGE = 83                            # project growth out to this age

# Stress test — the safe % each FLAT (no-glide) drawdown preset implies for crash
# exposure (equity = 100 − this). Return level is only a rough proxy for crash risk,
# so this is a hand-calibrated, editable table (Settings → RDSP equity exposure).
EQUITY_SAFE_DEFAULTS = {'Safe': 100, 'Low': 45, 'Target': 10, 'Growth': 5, 'Aggressive': 0, 'Current': 0}

# Glide Lab defaults (allocation-based comparison): stock/safe asset returns and the
# flat plan's fixed retirement safe %.
GL_STOCK_DEFAULT = 7.0
GL_SAFE_DEFAULT = 4.0
GL_FLATMIX_DEFAULT = 40.0


# ── Glide Lab (allocation-based flat-vs-glide de-risk analyzer) ───────────────────
# Pure helpers (no DB/closures) so the comparison math is unit-testable. Both plans
# share the same stock & safe returns and differ ONLY in their allocation (safe %)
# over time: return(y) = safe%(y)·safe_rate + (1 − safe%(y))·equity(y).
def _gl_shock_equity(y, start, shape, severity, decade_len):
    """Equity-sleeve return in year `y` under a shock (None = a normal year)."""
    if shape == 'crash':                                     # a "V": deep 2-yr drop, then recovers
        if y == start:     return -severity / 100.0
        if y == start + 1: return -severity / 200.0          # second down year, then normal returns
    elif shape == 'decade':                                  # an "L": crash-led, then flat — no recovery
        if y == start:     return -severity / 100.0          # the initial crash …
        if start < y < start + decade_len:
            return rdsp.LOST_DECADE_RETURN                   # … then stagnant for the rest of the decade
    return None


def _gl_return_map(years, safe_at, stock_rate, safe_rate, shape='none', start=0,
                   severity=0.0, decade_len=10):
    """Per-year return map for an allocation path: each year the safe % earns
    `safe_rate` and the rest earns `stock_rate` (or the shocked equity in a shock)."""
    out = {}
    for y in years:
        eq = _gl_shock_equity(y, start, shape, severity, decade_len)
        out[y] = rdsp.blended_return(safe_at(y), stock_rate if eq is None else eq, safe_rate)
    return out


def _gl_glide_safe(begin, end, current, target):
    """safe%(y) for a glide: `current` before the window, ramping to `target` across it."""
    steps = {s['year']: s['safe_pct'] for s in rdsp.glide_steps(begin, end, current, target)}
    return lambda y: current if y <= begin else (target if y >= end else steps.get(y, current))


def _gl_flat_safe(wd_start_year, flat_safe):
    """safe%(y) for the flat plan: 100% stocks until withdrawal, then a fixed mix."""
    return lambda y: 0.0 if y < wd_start_year else flat_safe


def glide_lab_breakeven(calm_diff, crash_diff):
    """Crash probability in [0,1] where E[glide − flat] income = 0, given the certain
    calm difference and the (avg) crash difference. None if gliding never breaks even
    in that range. E(p) = (1−p)·calm_diff + p·crash_diff."""
    denom = calm_diff - crash_diff
    if denom == 0:
        return None
    p = calm_diff / denom
    return p if 0.0 <= p <= 1.0 else None


# ── Settings + accounts ─────────────────────────────────────────────────────────
def _setting(key, default=''):
    s = Setting.query.get(key)
    return s.value if (s and s.value not in (None, '')) else default


def equity_safe_map():
    """{drawdown preset → implied safe %} for the stress test's flat plan. User-editable
    via Settings (`rdsp_equity_map` JSON), falling back to the calibrated defaults."""
    import json
    raw = _setting('rdsp_equity_map')
    if raw:
        try:
            user = {k: max(0.0, min(float(v), 100.0)) for k, v in json.loads(raw).items()}
            return {**EQUITY_SAFE_DEFAULTS, **user}
        except (ValueError, TypeError):
            pass
    return dict(EQUITY_SAFE_DEFAULTS)


def rdsp_accounts():
    return [a.name for a in Account.query.all() if (a.type or '').upper() == 'RDSP']


def birth_year():
    try:
        return int(_setting('birth_year') or 0) or None
    except ValueError:
        return None


def family_income_cents():
    v = _setting('rdsp_family_income')
    try:
        return rdsp.to_cents(float(v)) if v else None
    except ValueError:
        return None


# ── Actuals from transactions ────────────────────────────────────────────────────
def actuals_by_year(names):
    """{year: {'contribution','grant','bond'}} in cents, from RDSP Deposit rows."""
    rows = (Transaction.query
            .filter(Transaction.account.in_(names), Transaction.type == 'Deposit')
            .with_entities(Transaction.date, Transaction.subtype, Transaction.net_cad).all())
    out = {}
    for d, subtype, net in rows:
        rec = out.setdefault(d.year, {'contribution': 0, 'grant': 0, 'bond': 0})
        c = rdsp.to_cents(net or 0)
        if subtype == 'RDSP Grant':
            rec['grant'] += c
        elif subtype == 'RDSP Bond':
            rec['bond'] += c
        else:
            rec['contribution'] += c
    return out


_VBY_CACHE = {'key': None, 'ts': 0.0, 'val': None}


def value_by_year(names):
    """(year → end-of-year value cents, current_value cents) from the live
    performance series (market value + cash). Year-end = last monthly point in the
    year; the final point is today's live value.

    The performance series is a heavy rebuild (~1s, hits yfinance) and the account
    value doesn't change between Glide-Lab tweaks, so cache it briefly — this is what
    keeps the lab's per-input AJAX snappy (the projection math itself is ~instant)."""
    import time as _time
    key = tuple(sorted(names))
    now = _time.time()
    if _VBY_CACHE['key'] == key and now - _VBY_CACHE['ts'] < 60:
        return _VBY_CACHE['val']

    from calculations import get_performance_series
    per_year, current = {}, 0
    for name in names:
        s = get_performance_series(name)
        if not s.get('ok') or not s.get('dates'):
            continue
        mv, cash, dates = s['market_value'], (s.get('cash') or []), s['dates']
        acct_year = {}
        for i, dt in enumerate(dates):
            y = dt.year if hasattr(dt, 'year') else int(str(dt)[:4])
            acct_year[y] = rdsp.to_cents((mv[i] or 0) + (cash[i] if i < len(cash) and cash[i] else 0))
        for y, v in acct_year.items():
            per_year[y] = per_year.get(y, 0) + v
        if mv:
            current += rdsp.to_cents((mv[-1] or 0) + (cash[-1] if cash else 0))
    _VBY_CACHE.update(key=key, ts=now, val=(per_year, current))
    return per_year, current


def current_return(actuals, current_value_cents, through_year):
    """Realized money-weighted return (IRR) over the actual years — the "Current"
    preset. Inflows (contribution+grant+bond) are invested capital; the current
    value is the payout. Returns a rate, or None if it can't be solved."""
    flows = []  # (year, cents): negative = money in, positive = value out
    for y, rec in sorted(actuals.items()):
        inflow = rec['contribution'] + rec['grant'] + rec['bond']
        if inflow:
            flows.append((y, -inflow))
    if not flows or current_value_cents <= 0:
        return None
    base = flows[0][0]
    flows.append((through_year, current_value_cents))

    def npv(r):
        return sum(f / (1 + r) ** (y - base) for y, f in flows)

    lo, hi = -0.90, 1.50
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


# ── Plan (future years) ──────────────────────────────────────────────────────────
def seed_plan_if_empty(after_year):
    """First-run: seed RDSPPlanYear (future years only) from RDSP Tracker.xlsx."""
    import os
    if RDSPPlanYear.query.first() is not None:
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'RDSP Tracker.xlsx')
    if not os.path.exists(path):
        return
    for p in rdsp.parse_excel_plan(path):
        if p['year'] <= after_year:
            continue
        db.session.add(RDSPPlanYear(year=p['year'],
                                    contribution=rdsp.to_dollars(p['contribution']),
                                    grant=rdsp.to_dollars(p['grant']) if p['grant'] else None,
                                    bond=rdsp.to_dollars(p['bond']) if p['bond'] else None))
    db.session.commit()


def plan_rows(after_year):
    return RDSPPlanYear.query.filter(RDSPPlanYear.year > after_year).order_by(RDSPPlanYear.year).all()


# ── View builder ─────────────────────────────────────────────────────────────────
WITHDRAWAL_MODES = ['ldap', 'ldap_dap', 'max', 'to_age']   # to_age only offered when non-PGAP
MANDATORY_LDAP_AGE = 60   # LDAP must begin by Dec 31 of the year the beneficiary turns 60


def _parse_lumps(s):
    """'2046:20000, 2049:30000' → {2046: cents, 2049: cents} (one-off DAP lumps)."""
    out = {}
    for part in (s or '').replace(';', ',').split(','):
        y, _, a = part.partition(':')
        try:
            yr, amt = int(y.strip()), float(a.strip())
            if amt:
                out[yr] = rdsp.to_cents(amt)
        except (ValueError, AttributeError):
            pass
    return out


# ── Glide-path (de-risking drawdown style) ───────────────────────────────────────
GLIDE_FLOOR_AGE      = 50     # earliest age to begin de-risking
GLIDE_DEFAULT_LEN    = 10     # default glide length (years)
GLIDE_FINISH_CAP_AGE = 70     # fully de-risked by, at the latest
GLIDE_TARGET_DEFAULT = 80.0   # default % safe at the end of the glide
FULL_SAFE_RETURN     = 0.04   # "full-safe" return floor (GIC-level)
SAFE_BLEND_BUCKETS = {'Very Low'}   # the genuinely defensive end of the Blended-Risk scale
# (deliberately NOT 'Low' — broad equity ETFs land there but are still equities, so
# counting them would make a de-risking glide trivial.)


def current_safe_pct(names):
    """Today's safe-sleeve % for the RDSP accounts, tied to the Rebalancer's
    **Blended Risk** classification so the glide and the hand-off agree: cash + GICs
    (risk-free) plus holdings sitting in the Very Low risk bucket, ÷ total."""
    from calculations import (get_cash_by_account, get_gic_value_by_account,
                              get_holdings, _bucket_weights)
    from price_service import get_holdings_metadata
    cash = sum(get_cash_by_account().get(n, 0.0) for n in names)
    gic = sum(get_gic_value_by_account().get(n, {}).get('value', 0.0) for n in names)
    holdings = [h for h in get_holdings() if h['account'] in names]
    meta = get_holdings_metadata([h['ticker'] for h in holdings])
    safe_holdings = invested = 0.0
    for h in holdings:
        mv = h['market_value_cad'] or 0
        if mv <= 0:
            continue
        invested += mv
        w = _bucket_weights(h, meta.get(h['ticker'], {}), 'blend')
        safe_holdings += mv * sum(v for k, v in w.items() if k in SAFE_BLEND_BUCKETS)
    total = cash + gic + invested
    return round((cash + gic + safe_holdings) / total * 100, 1) if total > 0 else 0.0


def get_rdsp_view(return_label=DEFAULT_PRESET, contribute_until_year=None,
                  mode='ldap', wd_start=None, wd_lumps=None, wd_target=None, wd_to_age=None,
                  draw_label='Target', bequest=None, tax_rate=None,
                  draw_style='flat', glide_start_age=None, glide_length=None,
                  glide_target=None, glide_safe_return=None, glide_current=None,
                  stress_shape='crash', stress_timing=None, stress_severity=None, stress_decade_len=None,
                  gl_stock=None, gl_safe=None, gl_flatmix=None, include_glide_lab=False, gl_full=False):
    """Assemble the full RDSP payload (dollars) for the template/JSON endpoint."""
    names = rdsp_accounts()
    if not names:
        return {'ok': False, 'reason_kind': 'no_rdsp',
                'reason': 'No account is set to type RDSP yet.'}
    by = birth_year()
    if not by:
        return {'ok': False, 'reason_kind': 'no_birth_year',
                'reason': 'Set your birth year in Settings to project ages, grants and withdrawals.'}

    cy = date.today().year
    seed_plan_if_empty(cy)
    actuals = actuals_by_year(names)
    val_year, current_value = value_by_year(names)
    fam = family_income_cents()

    rows = plan_rows(cy)
    plan = {}
    for r in rows:
        plan[r.year] = {
            'contribution': rdsp.to_cents(r.contribution or 0),
            'grant': rdsp.to_cents(r.grant) if r.grant is not None else None,
            'bond': rdsp.to_cents(r.bond) if r.bond is not None else None,
        }
    plan_years = [r.year for r in rows]
    default_last = max((y for y in plan_years if plan[y]['contribution'] > 0), default=cy)
    last_contribution_year = int(contribute_until_year) if contribute_until_year else default_last

    # Resolve the return rate (incl. the computed "Current").
    if return_label == 'Current':
        rate = current_return(actuals, current_value, cy)
    else:
        rate = RETURN_PRESETS.get(return_label, RETURN_PRESETS[DEFAULT_PRESET])
    if rate is None:
        return_label, rate = DEFAULT_PRESET, RETURN_PRESETS[DEFAULT_PRESET]

    D = rdsp.to_dollars

    # ── Decumulation config ──
    if mode not in WITHDRAWAL_MODES:
        mode = 'ldap'
    draw_rate = RETURN_PRESETS.get(draw_label, RETURN_PRESETS['Target'])

    # Is the plan ever non-PGAP? (personal contributions overtake government money).
    # "deplete by age N" is only legal/offered then.
    total_contrib_plan = sum(t for t in [actuals.get(y, {}).get('contribution', 0) for y in actuals]) \
        + sum(rdsp.to_cents(r.contribution or 0) for r in rows)
    total_gb = sum(actuals.get(y, {}).get('grant', 0) + actuals.get(y, {}).get('bond', 0) for y in actuals) \
        + sum(rdsp.to_cents((r.grant or 0) + (r.bond or 0)) for r in rows)
    ever_non_pgap = total_contrib_plan >= total_gb
    modes = [m for m in WITHDRAWAL_MODES if m != 'to_age' or ever_non_pgap]
    if mode not in modes:
        mode = 'ldap'

    # Withdrawals can start once the holdback clears (last grant/bond + 10) and must
    # begin by age 60. Default to the earliest (holdback-clear), cap at 60.
    # Only count grants/bonds the engine would actually pay — they stop at age 49,
    # so a plan entry past that can't extend the holdback. (Actuals are real → kept.)
    last_gb_year = max([y for y, r in actuals.items() if r.get('grant') or r.get('bond')]
                       + [r.year for r in rows if ((r.grant or 0) or (r.bond or 0))
                          and (r.year - by) <= rdsp.GRANT_BOND_LAST_AGE] + [cy])
    wd_floor = last_gb_year + rdsp.HOLDBACK_YEARS
    wd_max = by + MANDATORY_LDAP_AGE
    if wd_floor > wd_max:
        wd_floor = wd_max
    wd_start_year = min(wd_max, max(wd_floor, int(wd_start))) if wd_start else wd_floor
    wd_to_age = int(wd_to_age) if wd_to_age else 85
    bequest_cents = rdsp.to_cents(bequest) if bequest else 0
    withdrawal = {'start_year': wd_start_year, 'mode': mode, 'rate': draw_rate,
                  'lumps': _parse_lumps(wd_lumps), 'target': rdsp.to_cents(wd_target) if wd_target else 0,
                  'to_age': wd_to_age, 'bequest': bequest_cents}
    # Extend the chart horizon to cover a "deplete to age N" beyond 83.
    horizon_age = max(HORIZON_AGE, wd_to_age) if mode == 'to_age' else HORIZON_AGE
    end_year = by + horizon_age

    # Contributions/grants/bonds already made (≤ this year) — seed the projection so
    # PGAP status and the tax base reflect lifetime totals, not just future years.
    actual_contrib = sum(r.get('contribution', 0) for r in actuals.values())
    actual_grant = sum(r.get('grant', 0) for r in actuals.values())
    actual_bond = sum(r.get('bond', 0) for r in actuals.values())

    # ── Drawdown style: flat (fixed rate) or glide (de-risking ramp) ──
    draw_style = 'glide' if draw_style == 'glide' else 'flat'

    def _int(v, default):
        try:
            return int(v) if v not in (None, '') else default
        except (TypeError, ValueError):
            return default

    def _float(v, default):
        try:
            return float(v) if v not in (None, '') else default
        except (TypeError, ValueError):
            return default

    g_start_age = max(0, _int(glide_start_age, GLIDE_FLOOR_AGE))
    g_length = max(1, _int(glide_length, GLIDE_DEFAULT_LEN))
    g_target = max(0.0, min(_float(glide_target, GLIDE_TARGET_DEFAULT), 100.0))
    g_safe_ret = _float(glide_safe_return, FULL_SAFE_RETURN * 100) / 100
    # Default the glide's starting safe % to ~0 (any cash on hand is treated as
    # temporary / to-be-invested); override in the control to reflect a real safe sleeve.
    g_current = max(0.0, min(_float(glide_current, 0.0), 100.0))

    # Glide Lab asset assumptions (allocation-based comparison; shared by both plans).
    gl_stock_rate = _float(gl_stock, GL_STOCK_DEFAULT) / 100.0
    gl_safe_rate = _float(gl_safe, GL_SAFE_DEFAULT) / 100.0
    gl_flat_safe = max(0.0, min(_float(gl_flatmix, GL_FLATMIX_DEFAULT), 100.0))

    # The glide is its own schedule (not anchored to withdrawal): begin at the
    # start-age (≥ today), run `length` years, but be finished by the cap age.
    glide_begin = max(cy, by + g_start_age)
    glide_end = max(glide_begin, min(by + GLIDE_FINISH_CAP_AGE, glide_begin + g_length))
    glide_step_rows = rdsp.glide_steps(glide_begin, glide_end, g_current, g_target)
    glide_safe_at = {s['year']: s['safe_pct'] for s in glide_step_rows}

    def glide_map(growth_rate):
        """Full per-year return for a glide at `growth_rate`: current allocation
        before the window, target after, ramp between — blended with the full-safe
        return. Built regardless of selected style (the stress test compares both)."""
        out = {}
        for y in range(cy + 1, end_year + 1):
            s = g_current if y <= glide_begin else (g_target if y >= glide_end else glide_safe_at.get(y, g_current))
            out[y] = rdsp.blended_return(s, growth_rate, g_safe_ret)
        return out

    def glide_rby(growth_rate):
        """The glide map only when glide is the selected style (None → flat)."""
        return glide_map(growth_rate) if draw_style == 'glide' else None

    def run(r):
        # P1 uses the plan's grant/bond values (Excel-seeded, reconciled to the caps);
        # income-driven auto-compute of the schedule is Phase 3, so don't pass income
        # into the projection (a blank grant/bond just means $0 here). `r` is the
        # accumulation return; in flat mode the drawdown return is `withdrawal['rate']`,
        # in glide mode the per-year return (return_by_year) drives both phases.
        return rdsp.project(cy + 1, current_value, by, plan=plan, family_income_cents=None,
                            return_rate=r, last_contribution_year=last_contribution_year,
                            end_year=end_year, withdrawal=withdrawal,
                            start_contrib_cents=actual_contrib, start_grant_cents=actual_grant,
                            start_bond_cents=actual_bond, return_by_year=glide_rby(r))

    def project_rby(rby):
        """Run the current plan with an explicit per-year return map (the stress test)."""
        return rdsp.project(cy + 1, current_value, by, plan=plan, family_income_cents=None,
                            return_rate=rate, last_contribution_year=last_contribution_year,
                            end_year=end_year, withdrawal=withdrawal,
                            start_contrib_cents=actual_contrib, start_grant_cents=actual_grant,
                            start_bond_cents=actual_bond, return_by_year=rby)

    proj = run(rate)
    band_lo = run(RETURN_PRESETS[BAND_LOW])
    band_hi = run(RETURN_PRESETS[BAND_HIGH])

    # Marginal tax rate applied to the taxable portion (user-set %, default 20%).
    try:
        tax_pct = float(tax_rate) / 100 if tax_rate not in (None, '') else 0.20
    except ValueError:
        tax_pct = 0.20
    tax_pct = max(0.0, min(tax_pct, 0.60))

    # ── Preset withdrawal comparison: same withdrawal config, each accumulation
    # return → different nest egg ("available lump sum") and longevity. The avg
    # monthly/yearly figures are **after tax** (the spendable income); the total
    # withdrawn stays gross. ──
    compare_rates = list(RETURN_PRESETS.items())
    cur_rate = current_return(actuals, current_value, cy)   # money-weighted return since inception
    if cur_rate is not None:
        compare_rates.append(('Current', cur_rate))
    comparison = []
    for label, prate in compare_rates:
        pr = run(prate)
        at_start = next((x['value'] for x in pr['rows'] if x['year'] == wd_start_year - 1), current_value)
        twd = sum(x['withdrawal'] for x in pr['rows'])
        net = twd - int(round(pr['summary']['taxable_total'] * tax_pct))   # after-tax spendable
        ndraw = sum(1 for x in pr['rows'] if x['phase'] == 'decumulation' and x['withdrawal'] > 0)
        comparison.append({
            'label': label, 'rate': prate,
            'value_at_start': D(at_start),
            'total_withdrawn': D(twd),
            'avg_monthly': D((net // ndraw // 12) if ndraw else 0),
            'avg_yearly': D((net // ndraw) if ndraw else 0),
            'end_value': D(pr['summary']['final_value']),
            'depletes_age': pr['summary']['depletes_age'],
        })

    # ── Build the unified yearly timeline (actual ≤ cy, projected after) ──
    actual_years = sorted(y for y in set(val_year) | set(actuals) if y <= cy)
    timeline = []
    for y in actual_years:
        rec = actuals.get(y, {})
        timeline.append({'year': y, 'phase': 'actual',
                         'contribution': rec.get('contribution', 0), 'grant': rec.get('grant', 0),
                         'bond': rec.get('bond', 0), 'value': val_year.get(y, 0)})
    proj_by_year = {pr['year']: pr for pr in proj['rows']}
    band_lo_by = {pr['year']: pr['value'] for pr in band_lo['rows']}
    band_hi_by = {pr['year']: pr['value'] for pr in band_hi['rows']}
    for pr in proj['rows']:
        timeline.append({'year': pr['year'], 'phase': 'projected',
                         'contribution': pr['contribution'], 'grant': pr['grant'],
                         'bond': pr['bond'], 'value': pr['value']})

    # Growth per year + cumulative composition (contributions / free money / growth).
    prev, cum_c, cum_f, cum_g = 0, 0, 0, 0
    for t in timeline:
        inflow = t['contribution'] + t['grant'] + t['bond']
        t['growth'] = t['value'] - prev - inflow
        prev = t['value']
        cum_c += t['contribution']; cum_f += t['grant'] + t['bond']; cum_g += t['growth']
        t['cum_contrib'], t['cum_free'], t['cum_growth'] = cum_c, cum_f, cum_g
        t['age'] = t['year'] - by

    # ── Milestones ──
    grant_total_all = sum(t['grant'] for t in timeline)
    cum = 0; grant_maxed = None
    for t in timeline:
        cum += t['grant']
        if grant_maxed is None and cum >= rdsp.CDSG_LIFETIME_MAX:
            grant_maxed = t['year']
    last_contrib = max((t['year'] for t in timeline if t['contribution'] > 0), default=cy)
    depletes_age = proj['summary']['depletes_age']
    milestones = {'current': cy, 'grant_maxed': grant_maxed, 'last_contribution': last_contrib,
                  'holdback_start': last_gb_year,   # last grant/bond — the 10-yr AHA clock starts here
                  'holdback_clear': wd_floor,        # = last_gb_year + 10
                  'withdrawal_start': wd_start_year,
                  'depletion': (by + depletes_age) if depletes_age else None}

    # Withdrawal schedule (drawdown rows only) with the taxable split + after-tax received.
    decum = [pr for pr in proj['rows'] if pr['phase'] == 'decumulation']
    schedule = []
    for pr in decum:
        tax_owed = int(round(pr['taxable'] * tax_pct))
        schedule.append({'year': pr['year'], 'age': pr['age'], 'withdrawal': D(pr['withdrawal']),
                         'rejected': D(pr.get('dap_rejected', 0)),
                         'taxable': D(pr['taxable']), 'tax_owed': D(tax_owed),
                         'net': D(pr['withdrawal'] - tax_owed), 'value': D(pr['value'])})
    # PGAP status (for the badge + cap warning) + the year it ever flips to non-PGAP.
    is_pgap = decum[0]['pgap'] if decum else (total_gb > total_contrib_plan)
    non_pgap_from = next((pr['year'] for pr in decum if not pr['pgap']), None)
    total_withdrawn = sum(pr['withdrawal'] for pr in proj['rows'])
    total_tax = int(round(proj['summary']['taxable_total'] * tax_pct))
    value_at_withdrawal = next((pr['value'] for pr in proj['rows'] if pr['year'] == wd_start_year - 1),
                               current_value)

    # Average monthly income, shown **after tax** (the spendable figure) — the total
    # withdrawn stays gross, but the per-month income is what actually lands in pocket.
    wd_rows = [pr for pr in proj['rows'] if pr['phase'] == 'decumulation' and pr['withdrawal'] > 0]
    n_wd = len(wd_rows)
    avg_monthly = ((total_withdrawn - total_tax) // n_wd // 12) if n_wd else 0

    def _net(row):                                    # one row's withdrawal net of tax
        return row['withdrawal'] - int(round(row['taxable'] * tax_pct))
    # Judge the trend on the steady phase, excluding terminal depletion years
    # (where the balance has hit the bequest floor and the payment is just a partial).
    core = [r for r in wd_rows if r['value'] > bequest_cents + 100] or wd_rows
    first_m = (_net(core[0]) // 12) if core else 0
    last_m = (_net(core[-1]) // 12) if core else 0
    if not core or abs(last_m - first_m) <= max(1, first_m) * 0.05:
        trend = 'about constant'
    else:
        trend = 'rises over time' if last_m > first_m else 'declines over time'
    profile = {'avg_monthly': D(avg_monthly), 'first_monthly': D(first_m),
               'last_monthly': D(last_m), 'trend': trend, 'years': n_wd}

    # ── Stats ──
    # Cap chips reflect what's actually been put in/received **to date** (≤ this
    # year, from transactions) — i.e. room used against the lifetime caps. The
    # projected lifetime totals live in the chart/comparison, not here.
    contrib_todate = sum(t['contribution'] for t in timeline if t['year'] <= cy)
    grant_todate = sum(t['grant'] for t in timeline if t['year'] <= cy)
    bond_todate = sum(t['bond'] for t in timeline if t['year'] <= cy)
    contrib_plan = sum(t['contribution'] for t in timeline)   # full plan (to-date + future)
    bond_plan = sum(t['bond'] for t in timeline)
    gb_by_year = {t['year']: t['grant'] + t['bond'] for t in timeline}
    stats = {
        'current_value': current_value,
        'final_value': proj['summary']['final_value'],
        'contrib_total': contrib_todate, 'contrib_plan': contrib_plan, 'contrib_cap': rdsp.CONTRIBUTION_LIFETIME_CAP,
        'grant_total': grant_todate, 'grant_plan': grant_total_all, 'grant_cap': rdsp.CDSG_LIFETIME_MAX,
        'bond_total': bond_todate, 'bond_plan': bond_plan, 'bond_cap': rdsp.CDSB_LIFETIME_MAX,
        'free_money': grant_todate + bond_todate,
        'leverage': round((grant_todate + bond_todate) / contrib_todate, 2) if contrib_todate else 0,
        'holdback_now': rdsp.holdback_amount(gb_by_year, cy),
        'total_withdrawn': total_withdrawn,
        'value_at_withdrawal': value_at_withdrawal,
        'depletes_age': depletes_age,
        'taxable_total': proj['summary']['taxable_total'],
        'non_taxable_total': proj['summary']['non_taxable_total'],
    }

    # ── Warnings ──
    warnings = []
    if contrib_plan >= rdsp.CONTRIBUTION_LIFETIME_CAP:
        warnings.append('Planned contributions reach the $200,000 lifetime cap — later contributions are blocked.')
    # "Leaving grant on the table": a future year whose contribution is below the
    # amount that maxes *that year's* base grant, while grant room remains. (Kept
    # conservative — current-year tiers only — so it never overstates catch-up.)
    if fam is not None:
        tiers = rdsp.grant_tier(fam)
        threshold = sum(room for room, _ in tiers)            # $1,500 high / $1,000 low

        def year_grant(contribution_cents):
            g, c = 0, contribution_cents
            for room, rate in tiers:
                m = min(room, c); g += m * rate; c -= m
                if c <= 0:
                    break
            return g

        g_before = 0
        for t in timeline:
            if t['phase'] == 'projected' and t['age'] <= rdsp.GRANT_BOND_LAST_AGE:
                room_left = rdsp.CDSG_LIFETIME_MAX - g_before
                if room_left > 0 and t['contribution'] < threshold:
                    short = min(year_grant(threshold), room_left) - min(year_grant(t['contribution']), room_left)
                    if short >= 500_00:
                        extra = rdsp.to_dollars(threshold - t['contribution'])
                        warnings.append(f"{t['year']}: contributing ~${extra:,.0f} more could earn "
                                        f"~${rdsp.to_dollars(short):,.0f} additional grant this year.")
            g_before += t['grant']

    # ── Chart payload (dollars) ──
    D = rdsp.to_dollars
    labels = [t['year'] for t in timeline]
    ages = [t['age'] for t in timeline]
    n_actual = sum(1 for t in timeline if t['phase'] == 'actual')
    actual_line = [D(t['value']) if t['phase'] == 'actual' else None for t in timeline]
    # join the dashed projected line to the last actual point
    proj_line = [None] * (n_actual - 1) + [D(t['value']) for t in timeline[max(0, n_actual - 1):]]
    band_low_line = [None if t['phase'] == 'actual' else D(band_lo_by.get(t['year'], 0)) for t in timeline]
    band_high_line = [None if t['phase'] == 'actual' else D(band_hi_by.get(t['year'], 0)) for t in timeline]
    # Consistent y-axis so the value / composition / tax views are the same size.
    # Composition/tax stacks and the main line never exceed the band-high line, so
    # this single max bounds all three views; pad 5% for headroom above the peak.
    y_max = round(max([v for v in (actual_line + proj_line + band_high_line) if v] or [0]) * 1.05)

    # Tax view: grey balance before withdrawals, then the balance split into the
    # tax-free base (remaining contributions) and the taxable remainder during drawdown.
    proj_tfb = {pr['year']: pr['tax_free_base'] for pr in proj['rows']}
    tax_pre = [D(t['value']) if t['year'] < wd_start_year else None for t in timeline]
    tax_free = [D(proj_tfb.get(t['year'], 0)) if t['year'] >= wd_start_year else None for t in timeline]
    tax_able = [D(max(0, t['value'] - proj_tfb.get(t['year'], 0))) if t['year'] >= wd_start_year else None
                for t in timeline]

    # Income view: each drawdown year's withdrawal split into after-tax kept + tax.
    sched_by_year = {s['year']: s for s in schedule}
    income_net = [sched_by_year[t['year']]['net'] if t['year'] in sched_by_year else None for t in timeline]
    income_tax = [sched_by_year[t['year']]['tax_owed'] if t['year'] in sched_by_year else None for t in timeline]

    table = [{'year': r.year, 'contribution': r.contribution or 0,
              'grant': r.grant, 'bond': r.bond} for r in rows]

    # ── Glide-path playbook payload ──
    # Per-year safe/growth split + implied return, plus a Rebalancer seed that sets
    # ONLY the Very Low (safe) target — the Rebalancer spreads the rest across the
    # current buckets. The earliest upcoming year is the actionable hand-off.
    glide_rows = []
    if draw_style == 'glide':
        actionable_year = glide_step_rows[0]['year'] if glide_step_rows else None
        for s in glide_step_rows:
            safe = s['safe_pct']
            grow = round(100 - safe, 1)
            glide_rows.append({
                'year': s['year'], 'age': s['year'] - by,
                'safe_pct': safe, 'growth_pct': grow,
                'ret': round(rdsp.blended_return(safe, rate, g_safe_ret) * 100, 2),
                'seed': f'Very Low:{safe}',
                'is_current': s['year'] == actionable_year,
            })
    glide = {
        'style': draw_style,
        'start_age': g_start_age, 'length': g_length, 'finish_age': glide_end - by,
        'begin_year': glide_begin, 'end_year': glide_end,
        'target': round(g_target, 1), 'current': round(g_current, 1),
        'safe_return': round(g_safe_ret * 100, 2),
        'growth_label': return_label, 'growth_rate': round(rate * 100, 1),
        'floor_age': GLIDE_FLOOR_AGE, 'cap_age': GLIDE_FINISH_CAP_AGE,
        'rebalancer_account': names[0],
        'rows': glide_rows,
    }

    # ── Stress params (shape / severity / timing) ──
    # The heavy flat-vs-glide analysis now lives in the Glide Lab (allocation model);
    # here we just derive the params it (and the main-chart overlay) need.
    s_shape = stress_shape if stress_shape in ('none', 'crash', 'decade') else 'crash'
    s_depth = max(5.0, min(_float(stress_severity, 35.0), 90.0))
    s_decade_len = int(max(3, min(_float(stress_decade_len, 7), 15)))
    STRESS_TIMING_OFFSET = {'early': 0, 'mid': 8, 'late': 16}
    s_when = stress_timing if stress_timing in STRESS_TIMING_OFFSET else ('early' if s_shape == 'none' else 'mid')
    shock_start = wd_start_year + STRESS_TIMING_OFFSET[s_when]
    # Old rate-based main-chart overlay retired — the Glide Lab supplies the allocation one.
    stress_flat_line = stress_glide_line = None
    stress_chart_label = ''
    stress = {'shape': s_shape, 'severity': round(s_depth), 'timing': s_when, 'decade_years': s_decade_len}

    # ── Glide Lab: allocation-based flat-vs-glide comparison (Model C) ──
    # Computed only on request (heavier than the rest of the tab). Both plans share
    # gl_stock_rate / gl_safe_rate and differ only in their safe-% allocation path.
    glide_lab = None
    if include_glide_lab:
        gl_years = list(range(cy + 1, end_year + 1))
        flat_at = _gl_flat_safe(wd_start_year, gl_flat_safe)
        glide_at = _gl_glide_safe(glide_begin, glide_end, g_current, g_target)
        has_shock = s_shape != 'none'
        # The SELECTED shock at the SELECTED timing drives every view/KPI, so shape &
        # timing both update everything (not an averaged, hard-coded crash).
        shock = (s_shape, shock_start) if has_shock else ('none', 0)

        def _lab_map(safe_at, shape, start):
            return _gl_return_map(gl_years, safe_at, gl_stock_rate, gl_safe_rate,
                                  shape, start, s_depth, s_decade_len)

        # Per-request projection cache — many maps repeat (value == shock, overlay ==
        # value, no-shock scenario == calm), so caching keeps the lab snappy.
        _pcache = {}

        def _projc(rby):
            k = tuple(sorted(rby.items()))
            if k not in _pcache:
                _pcache[k] = project_rby(rby)
            return _pcache[k]

        def _incc(rby):
            pr = _projc(rby)
            rows = pr['rows']
            inc = [r['withdrawal'] - int(round(r['taxable'] * tax_pct))
                   for r in rows if r['phase'] == 'decumulation' and r['withdrawal'] > 0]
            n = len(inc)
            net = sum(r['withdrawal'] for r in rows) - int(round(pr['summary']['taxable_total'] * tax_pct))
            drops = [inc[i - 1] - inc[i] for i in range(1, len(inc))]
            return {'avg_monthly': (net // n // 12) if n else 0, 'avg_yearly': (net // n) if n else 0,
                    'total': net, 'ending': pr['summary']['final_value'],
                    'worst': min(inc) if inc else 0, 'drop': max([d for d in drops if d > 0] or [0])}

        def _overlay_line(rby):                                  # aligned to the main projection timeline
            vals = {pr['year']: pr['value'] for pr in _projc(rby)['rows']}
            out = []
            for i, t in enumerate(timeline):
                if i < n_actual - 1:    out.append(None)
                elif i == n_actual - 1: out.append(D(t['value']))
                else:                   out.append(D(vals.get(t['year'], 0)))
            return out

        flat_shock, glide_shock = _lab_map(flat_at, *shock), _lab_map(glide_at, *shock)
        calm_f, calm_g = _incc(_lab_map(flat_at, 'none', 0)), _incc(_lab_map(glide_at, 'none', 0))
        shock_f, shock_g = _incc(flat_shock), _incc(glide_shock)
        calm_diff = calm_g['total'] - calm_f['total']
        crash_diff = shock_g['total'] - shock_f['total']
        be = glide_lab_breakeven(calm_diff, crash_diff) if has_shock else None
        fpath, gpath = _projc(flat_shock)['rows'], _projc(glide_shock)['rows']

        # Crash-year zoom: the one-year hit (both shocks are now crash-led)
        crash_year = None
        if has_shock:
            cy_eq = -s_depth / 100.0
            crash_year = {'age': shock_start - by,
                          'flat': round(rdsp.blended_return(flat_at(shock_start), cy_eq, gl_safe_rate) * 100, 1),
                          'glide': round(rdsp.blended_return(glide_at(shock_start), cy_eq, gl_safe_rate) * 100, 1)}

        # Main-chart overlay: flat/glide value paths aligned to the projection timeline
        overlay = {'flat': _overlay_line(flat_shock), 'glide': _overlay_line(glide_shock),
                   'markers': {'withdrawal': wd_start_year, 'glide_begin': glide_begin,
                               'glide_end': glide_end, 'crash': shock_start if has_shock else None}}

        # ── Summary line + after-shock table (allocation model, no bequest) ──
        def _pct(f, g):
            return round((g - f) / f * 100, 1) if f else 0.0

        def _c0(c):
            return f"${rdsp.to_dollars(c):,.0f}"

        def _ck(c):
            d = rdsp.to_dollars(c)
            return f"${d / 1000:,.0f}k" if abs(d) >= 1000 else _c0(c)

        def _metric(label, key, higher_better=True, cell=None):
            f, g = shock_f[key], shock_g[key]
            better = (g >= f) if higher_better else (g <= f)
            return {'label': label, 'flat': cell(shock_f) if cell else _c0(f),
                    'glide': cell(shock_g) if cell else _c0(g), 'diff_pct': _pct(f, g),
                    'cls': 'text-green' if better else 'text-red'}

        metrics = [
            _metric('After-tax income', 'avg_yearly',
                    cell=lambda d: f"{_c0(d['avg_monthly'])}/mo · {_c0(d['avg_yearly'])}/yr · {_ck(d['total'])} total"),
            _metric('Worst-year income', 'worst'),
            _metric('Biggest 1-yr income drop', 'drop', higher_better=False),
        ]

        # Equivalent flat safe % whose calm income matches the glide's (bisect; higher safe = lower income)
        target_inc = calm_g['avg_yearly']
        lo, hi = 0.0, 100.0
        for _ in range(6):
            mid = (lo + hi) / 2
            inc = _incc(_lab_map(_gl_flat_safe(wd_start_year, mid), 'none', 0))['avg_yearly']
            lo, hi = (mid, hi) if inc > target_inc else (lo, mid)

        # ── Heavy extras (cost-vs-protection dial + scenario grid): only on `gl_full` ──
        dial = scenarios = None
        if gl_full:
            dial = []
            for tgt in range(0, 101, 20):
                gs = _gl_glide_safe(glide_begin, glide_end, g_current, float(tgt))
                cost = _incc(_lab_map(gs, 'none', 0))['total'] - calm_f['total']
                pay = (_incc(_lab_map(gs, *shock))['total'] - shock_f['total']) if has_shock else 0
                dial.append({'target': tgt, 'cost': D(cost), 'payoff': D(pay)})

            def _scen(shape, when):
                if shape == 'none':
                    fi, gi = calm_f['avg_yearly'], calm_g['avg_yearly']
                else:
                    st = wd_start_year + STRESS_TIMING_OFFSET[when]
                    fi = _incc(_lab_map(flat_at, shape, st))['avg_yearly']
                    gi = _incc(_lab_map(glide_at, shape, st))['avg_yearly']
                diff = _pct(fi, gi)
                return {'diff_pct': diff, 'cls': 'text-green' if diff >= 0 else 'text-red'}

            scenarios = [{'label': 'No shock', **_scen('none', None)}]
            for shp, name in [('crash', 'Sharp crash'), ('decade', 'Lost decade')]:
                scenarios += [{'label': f'{name} — {w}', **_scen(shp, w)} for w in ('early', 'mid', 'late')]

        glide_lab = {
            'stock_rate': round(gl_stock_rate * 100, 1), 'safe_rate': round(gl_safe_rate * 100, 1),
            'flat_safe': round(gl_flat_safe, 1), 'glide_target': round(g_target, 1),
            'shape': s_shape, 'severity': round(s_depth), 'timing': s_when,
            'value': {'labels': [r['year'] for r in gpath], 'ages': [r['age'] for r in gpath],
                      'flat': [D(r['value']) for r in fpath], 'glide': [D(r['value']) for r in gpath]},
            'alloc': {'ages': [y - by for y in gl_years], 'years': gl_years,
                      'flat_safe': [flat_at(y) for y in gl_years], 'glide_safe': [glide_at(y) for y in gl_years]},
            'breakeven': {'p': list(range(0, 101, 5)),
                          'ev': [round(D((1 - p / 100) * calm_diff + (p / 100) * crash_diff)) for p in range(0, 101, 5)],
                          'point': round(be * 100) if be is not None else None},
            'dial': dial,
            'crash_year': crash_year,
            'overlay': overlay,
            'summary': {'nocrash_pct': _pct(calm_f['avg_yearly'], calm_g['avg_yearly']),
                        'payoff_pct': _pct(shock_f['avg_yearly'], shock_g['avg_yearly']) if has_shock else None,
                        'breakeven_safe': round((lo + hi) / 2)},
            'metrics': metrics,
            'scenarios': scenarios,
            'markers': {'withdrawal': wd_start_year - by, 'glide_begin': glide_begin - by,
                        'glide_end': glide_end - by, 'crash': (shock_start - by) if has_shock else None},
            # Worst-year after-tax income UNDER THE SHOCK (de-risking's real job is raising it)
            'floor': {'flat': D(shock_f['worst']), 'glide': D(shock_g['worst'])},
            'totals': {'calm_flat': D(calm_f['total']), 'calm_glide': D(calm_g['total']),
                       'crash_flat': D(shock_f['total']), 'crash_glide': D(shock_g['total'])},
        }

    return {
        'ok': True,
        'accounts': names,
        'current_year': cy,
        'birth_year': by,
        'horizon_age': horizon_age,
        'return_label': return_label,
        'return_rate': rate,
        'presets': list(RETURN_PRESETS.keys()) + ['Current'],
        'contribute_until': last_contribution_year,
        'contribute_until_options': sorted({last_contrib} | {by + a for a in range(35, rdsp.CONTRIBUTION_LAST_AGE + 1)
                                                              if by + a > cy}),
        # Decumulation controls + results
        'withdrawal': {
            'mode': mode, 'modes': modes,
            'start': wd_start_year, 'floor': wd_floor, 'max': wd_max,
            'start_options': list(range(wd_floor, wd_max + 1)),
            'lumps': wd_lumps or '', 'target': rdsp.to_dollars(withdrawal['target']) if withdrawal['target'] else '',
            'to_age': wd_to_age,
            'bequest': rdsp.to_dollars(bequest_cents) if bequest_cents else '',
            'draw_label': draw_label, 'draw_rate': draw_rate, 'draw_presets': list(RETURN_PRESETS.keys()),
            'is_pgap': is_pgap, 'non_pgap_from': non_pgap_from,
            'tax_rate': round(tax_pct * 100, 1), 'total_tax': D(total_tax),
        },
        'schedule': schedule,
        'comparison': comparison,
        'profile': profile,
        'glide': glide,
        'stress': stress,
        'glide_lab': glide_lab,
        'chart': {
            'labels': labels, 'ages': ages, 'n_actual': n_actual, 'y_max': y_max,
            'actual': actual_line, 'projected': proj_line,
            'band_low': band_low_line, 'band_high': band_high_line,
            'composition': {
                'contrib': [D(t['cum_contrib']) for t in timeline],
                'free': [D(t['cum_free']) for t in timeline],
                'growth': [D(t['cum_growth']) for t in timeline],
            },
            'tax': {'pre': tax_pre, 'free': tax_free, 'taxable': tax_able},
            'income': {'net': income_net, 'tax': income_tax},
            'stress_flat': stress_flat_line, 'stress_glide': stress_glide_line, 'stress_label': stress_chart_label,
            'milestones': milestones,
            'glide': {'begin': glide_begin, 'end': glide_end} if draw_style == 'glide' else None,
            'last_actual_year': cy,
        },
        'stats': {k: (v if k in ('leverage', 'depletes_age') else D(v)) for k, v in stats.items()},
        'warnings': warnings,
        'table': table,
        'needs_income': fam is None,
        'family_income': rdsp.to_dollars(fam) if fam is not None else '',
    }
