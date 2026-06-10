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
RETURN_PRESETS = {'GIC': 0.04, 'Low': 0.05, 'Target': 0.07, 'Growth': 0.09, 'Aggressive': 0.11}
DEFAULT_PRESET = 'Target'
BAND_LOW, BAND_HIGH = 'GIC', 'Aggressive'   # the shaded range cone
HORIZON_AGE = 83                            # project growth out to this age


# ── Settings + accounts ─────────────────────────────────────────────────────────
def _setting(key, default=''):
    s = Setting.query.get(key)
    return s.value if (s and s.value not in (None, '')) else default


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


def value_by_year(names):
    """(year → end-of-year value cents, current_value cents) from the live
    performance series (market value + cash). Year-end = last monthly point in the
    year; the final point is today's live value."""
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


def get_rdsp_view(return_label=DEFAULT_PRESET, contribute_until_year=None,
                  mode='ldap', wd_start=None, wd_lumps=None, wd_target=None, wd_to_age=None,
                  draw_label='Low', bequest=None, tax_rate=None):
    """Assemble the full RDSP payload (dollars) for the template/JSON endpoint."""
    names = rdsp_accounts()
    if not names:
        return {'ok': False, 'reason': 'No account is set to type RDSP. Set one on the Accounts page.'}
    by = birth_year()
    if not by:
        return {'ok': False, 'reason': 'Set your birth year in Settings to project ages, grants and withdrawals.'}

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
    draw_rate = RETURN_PRESETS.get(draw_label, RETURN_PRESETS['Low'])

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
    last_gb_year = max([y for y, r in actuals.items() if r.get('grant') or r.get('bond')]
                       + [r.year for r in rows if (r.grant or 0) or (r.bond or 0)] + [cy])
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

    def run(r):
        # P1 uses the plan's grant/bond values (Excel-seeded, reconciled to the caps);
        # income-driven auto-compute of the schedule is Phase 3, so don't pass income
        # into the projection (a blank grant/bond just means $0 here). `r` is the
        # accumulation return; the drawdown return is fixed by `withdrawal['rate']`.
        return rdsp.project(cy + 1, current_value, by, plan=plan, family_income_cents=None,
                            return_rate=r, last_contribution_year=last_contribution_year,
                            end_year=end_year, withdrawal=withdrawal,
                            start_contrib_cents=actual_contrib, start_grant_cents=actual_grant,
                            start_bond_cents=actual_bond)

    proj = run(rate)
    band_lo = run(RETURN_PRESETS[BAND_LOW])
    band_hi = run(RETURN_PRESETS[BAND_HIGH])

    # ── Preset withdrawal comparison: same withdrawal config, each accumulation
    # return → different nest egg ("available lump sum") and longevity. ──
    compare_rates = list(RETURN_PRESETS.items())
    cur_rate = current_return(actuals, current_value, cy)   # money-weighted return since inception
    if cur_rate is not None:
        compare_rates.append(('Current', cur_rate))
    comparison = []
    for label, prate in compare_rates:
        pr = run(prate)
        at_start = next((x['value'] for x in pr['rows'] if x['year'] == wd_start_year - 1), current_value)
        twd = sum(x['withdrawal'] for x in pr['rows'])
        ndraw = sum(1 for x in pr['rows'] if x['phase'] == 'decumulation' and x['withdrawal'] > 0)
        comparison.append({
            'label': label, 'rate': prate,
            'value_at_start': D(at_start),
            'total_withdrawn': D(twd),
            'avg_monthly': D((twd // ndraw // 12) if ndraw else 0),
            'avg_yearly': D((twd // ndraw) if ndraw else 0),
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
                  'holdback_clear': wd_floor,
                  'withdrawal_start': wd_start_year,
                  'depletion': (by + depletes_age) if depletes_age else None}

    # Marginal tax rate applied to the taxable portion (user-set %, default 20%).
    try:
        tax_pct = float(tax_rate) / 100 if tax_rate not in (None, '') else 0.20
    except ValueError:
        tax_pct = 0.20
    tax_pct = max(0.0, min(tax_pct, 0.60))

    # Withdrawal schedule (drawdown rows only) with the taxable split + after-tax received.
    decum = [pr for pr in proj['rows'] if pr['phase'] == 'decumulation']
    schedule = []
    for pr in decum:
        tax_owed = int(round(pr['taxable'] * tax_pct))
        schedule.append({'year': pr['year'], 'age': pr['age'], 'withdrawal': D(pr['withdrawal']),
                         'taxable': D(pr['taxable']), 'tax_owed': D(tax_owed),
                         'net': D(pr['withdrawal'] - tax_owed), 'value': D(pr['value'])})
    # PGAP status (for the badge + cap warning) + the year it ever flips to non-PGAP.
    is_pgap = decum[0]['pgap'] if decum else (total_gb > total_contrib_plan)
    non_pgap_from = next((pr['year'] for pr in decum if not pr['pgap']), None)
    total_withdrawn = sum(pr['withdrawal'] for pr in proj['rows'])
    total_tax = int(round(proj['summary']['taxable_total'] * tax_pct))
    value_at_withdrawal = next((pr['value'] for pr in proj['rows'] if pr['year'] == wd_start_year - 1),
                               current_value)

    # Average monthly withdrawal + whether it's constant or rising over the phase.
    wd_rows = [pr for pr in proj['rows'] if pr['phase'] == 'decumulation' and pr['withdrawal'] > 0]
    n_wd = len(wd_rows)
    avg_monthly = (total_withdrawn // n_wd // 12) if n_wd else 0
    steady = wd_rows   # judge the trend on the recurring stream
    # Judge the trend on the steady phase, excluding terminal depletion years
    # (where the balance has hit the bequest floor and the payment is just a partial).
    core = [r for r in steady if r['value'] > bequest_cents + 100] or steady
    first_m = (core[0]['withdrawal'] // 12) if core else 0
    last_m = (core[-1]['withdrawal'] // 12) if core else 0
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

    table = [{'year': r.year, 'contribution': r.contribution or 0,
              'grant': r.grant, 'bond': r.bond} for r in rows]

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
            'draw_label': draw_label, 'draw_presets': list(RETURN_PRESETS.keys()),
            'is_pgap': is_pgap, 'non_pgap_from': non_pgap_from,
            'tax_rate': round(tax_pct * 100, 1), 'total_tax': D(total_tax),
        },
        'schedule': schedule,
        'comparison': comparison,
        'profile': profile,
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
            'milestones': milestones,
            'last_actual_year': cy,
        },
        'stats': {k: (v if k in ('leverage', 'depletes_age') else D(v)) for k, v in stats.items()},
        'warnings': warnings,
        'table': table,
        'needs_income': fam is None,
        'family_income': rdsp.to_dollars(fam) if fam is not None else '',
    }
