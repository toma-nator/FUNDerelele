"""RDSP projection engine — pure, testable, money in integer cents.

This module has **no DB or Flask coupling**: every function takes plain numbers
and returns plain numbers/dicts, so the whole thing is unit-testable (see
`tests/test_rdsp.py`). The Flask layer (Phase 1+) converts dollars↔cents at the
boundary and persists plans via the `RDSPPlanYear` model.

Design rules (see memory `rdsp-tab-plan`):
  • All money is **integer cents**. Compounding rounds to the nearest cent each
    year, so a 30-year projection can't accumulate float dust.
  • CRA rules are the source of truth; `RDSP Tracker.xlsx` only *seeds* a plan and
    is reconciled against what these functions compute.
  • Every statutory cap is enforced in exactly one place.

⚠️  INDEXED CONSTANTS: the income thresholds below are indexed yearly. The defaults
are 2024 figures — verify/update against https://www.rdsp.com/calculator/ and the
CRA CDSG/CDSB tables. They're isolated here so updating them is a one-line change.
"""
from __future__ import annotations

# ── Stable statutory limits ────────────────────────────────────────────────────
CONTRIBUTION_LIFETIME_CAP = 200_000_00   # personal contributions only (grants/bonds excluded)
CDSG_LIFETIME_MAX         = 70_000_00
CDSB_LIFETIME_MAX         = 20_000_00
GRANT_BOND_LAST_AGE       = 49           # available through Dec 31 of the year the beneficiary turns 49
CONTRIBUTION_LAST_AGE     = 59           # contributions allowed through the year they turn 59
CARRY_FORWARD_YEARS       = 10           # entitlement earned in year y is usable through y+10
ANNUAL_GRANT_MAX          = 10_500_00    # max grant/year when catching up
ANNUAL_BOND_MAX           = 11_000_00    # max bond/year when catching up
PROGRAM_START_YEAR        = 2008         # CDSG/CDSB did not exist before this
HOLDBACK_YEARS            = 10           # AHA: grants+bonds in the last 10 yrs are repayable on early withdrawal

# ── Income-tested thresholds — ADJUSTED family net income, INDEXED yearly ───────
# 2025 figures from CRA guide RC4460 (Rev. 25), Charts 1 & 2. Update each year.
CDSG_INCOME_THRESHOLD = 114_750_00       # ≤ → high match tier; > → low match tier
CDSB_FULL_INCOME_MAX  = 37_487_00        # ≤ → full $1,000 bond
CDSB_ZERO_INCOME_MAX  = 57_375_00        # ≥ → $0 bond; phase-out between (linear approx of the CDSA formula)
CDSB_FULL             = 1_000_00

# ── CDSG match structure (matchable contribution per rate) ──────────────────────
# High tier: first $500 @ 300%, next $1,000 @ 200%  → up to $3,500 grant on $1,500.
# Low tier : first $1,000 @ 100%                     → up to $1,000 grant on $1,000.
HIGH_TIER = ((500_00, 3), (1_000_00, 2))
LOW_TIER  = ((1_000_00, 1),)

LDAP_AGE_FACTOR = 83                      # annual LDAP max = value / (83 − age)


# ── Money helpers ───────────────────────────────────────────────────────────────
def to_cents(dollars) -> int:
    """Dollars (float/str) → integer cents, rounded."""
    return int(round(float(dollars) * 100))


def to_dollars(cents: int) -> float:
    """Integer cents → dollars (float, 2dp)."""
    return round(cents / 100, 2)


def _grow(value_cents: int, rate: float) -> int:
    """One year of growth, rounded to the nearest cent (keeps the series exact)."""
    return int(round(value_cents * (1.0 + rate)))


# ── CDSG: grant entitlement, catch-up, and matching ─────────────────────────────
def grant_tier(family_income_cents: int):
    """The match tier for a family net income: HIGH_TIER (≤ threshold) or LOW_TIER."""
    return HIGH_TIER if family_income_cents <= CDSG_INCOME_THRESHOLD else LOW_TIER


def new_entitlement_bucket(year: int, family_income_cents: int) -> dict:
    """A year's grant *entitlement* = matchable contribution room at each rate.

    Stored as {'year': y, rate: room_cents, …}. Consumed later (highest rate first)
    when contributions are made; unused room carries forward up to 10 years.
    """
    bucket = {'year': year}
    for room, rate in grant_tier(family_income_cents):
        bucket[rate] = bucket.get(rate, 0) + room
    return bucket


def _prune_expired(buckets: list, year: int) -> list:
    """Drop entitlement older than the 10-year carry-forward window."""
    return [b for b in buckets if year - b['year'] <= CARRY_FORWARD_YEARS]


def grant_on_contribution(buckets: list, year: int, contribution_cents: int,
                          prior_grant_total_cents: int) -> tuple[int, list]:
    """Grant paid on a contribution, consuming entitlement **highest-rate-first,
    oldest-bucket-first**, capped by the annual ($10,500) and lifetime ($70k) maxes.

    `buckets` is the list of un-consumed entitlement (see `new_entitlement_bucket`),
    already including the current year's. Returns (grant_cents, remaining_buckets).
    Pure: it copies the buckets it mutates.
    """
    buckets = [dict(b) for b in _prune_expired(buckets, year)]
    lifetime_room = max(0, CDSG_LIFETIME_MAX - prior_grant_total_cents)
    annual_room = min(ANNUAL_GRANT_MAX, lifetime_room)
    grant = 0
    remaining_contrib = contribution_cents

    for rate in (3, 2, 1):                       # 300% first, then 200%, then 100%
        if remaining_contrib <= 0 or annual_room - grant <= 0:
            break
        for b in sorted((b for b in buckets if b.get(rate, 0) > 0), key=lambda b: b['year']):
            if remaining_contrib <= 0:
                break
            cap_left = annual_room - grant
            if cap_left <= 0:
                break
            matchable = min(b[rate], remaining_contrib)
            payable = matchable * rate
            if payable > cap_left:               # pay up to the ceiling *exactly*…
                payable = cap_left
                matchable = cap_left // rate     # …and leave unmatched entitlement to carry forward
            if matchable <= 0:
                break
            grant += payable
            remaining_contrib -= matchable
            b[rate] -= matchable

    return grant, buckets


# ── CDSB: income-tested bond (no contribution required) ─────────────────────────
def bond_for_year(family_income_cents: int, prior_bond_total_cents: int) -> int:
    """CDSB for a year given family income, capped at the $20k lifetime max.

    Full $1,000 at/below the lower threshold, $0 at/above the upper, linear
    phase-out between (an approximation of the CRA formula — VERIFY).
    """
    if family_income_cents <= CDSB_FULL_INCOME_MAX:
        bond = CDSB_FULL
    elif family_income_cents >= CDSB_ZERO_INCOME_MAX:
        bond = 0
    else:
        span = CDSB_ZERO_INCOME_MAX - CDSB_FULL_INCOME_MAX
        over = family_income_cents - CDSB_FULL_INCOME_MAX
        bond = int(round(CDSB_FULL * (span - over) / span))
    return max(0, min(bond, CDSB_LIFETIME_MAX - prior_bond_total_cents))


# ── Holdback (Assistance Holdback Amount) ───────────────────────────────────────
def holdback_amount(grant_bond_by_year: dict, as_of_year: int) -> int:
    """Assistance Holdback Amount = grants + bonds paid in the **last 10 years**
    (RC4460), less any repayments. `grant_bond_by_year` maps year → grant+bond cents.

    On any DAP, ESDC claws back **3× the payment, capped at the AHA** (proportional
    repayment rule, since 2014), and a DAP can't drop FMV below the AHA. That
    repayment is a Phase-2 decumulation concern; here we just expose the AHA so the
    UI can show "$X repayable until <year>". The user's plan withdraws 10 yrs after
    the last contribution, so the AHA is $0 by then.
    """
    return sum(v for y, v in grant_bond_by_year.items()
               if 0 <= as_of_year - y < HOLDBACK_YEARS)


# ── Decumulation primitives ─────────────────────────────────────────────────────
def ldap_payment(value_cents: int, age: int) -> int:
    """Lifetime Disability Assistance Payment annual max (CRA RC4460 LDAP formula):
    A ÷ (B + 3 − C), where A = FMV, C = age, B = greater of 80 and age. So the
    denominator is (83 − age) until age 80, then a constant 3 (= FMV/3) thereafter.
    (Ignores D = locked-in annuity payments, which this app doesn't model.)
    """
    denom = (LDAP_AGE_FACTOR - age) if age < 80 else 3
    return int(round(value_cents / denom))


def level_payment_to_age(value_cents: int, age: int, to_age: int, rate: float,
                         residual_cents: int = 0) -> int:
    """Level annual withdrawal that draws `value` down to `residual_cents` by
    `to_age` (a bequest to leave behind), growing at `rate` between. Annuity
    payment on the value net of the discounted residual. Used by 'model to age N'.
    """
    n = max(1, to_age - age)
    if abs(rate) < 1e-9:
        return int(round((value_cents - residual_cents) / n))
    disc = (1 + rate) ** (-n)
    return int(round((value_cents - residual_cents * disc) * rate / (1 - disc)))


# ── Glide path ──────────────────────────────────────────────────────────────────
def glide_steps(start_year: int, withdrawal_year: int, current_safe_pct: float,
                target_safe_pct: float, n_steps: int = 5) -> list:
    """A gradual, multi-step shift of the *safe* allocation %, from `current` today
    up to `target` by the withdrawal year. Returns [{'year', 'safe_pct'}], evenly
    spaced over the last `n_steps` years before withdrawal (not a single jump).
    """
    n_steps = max(1, n_steps)
    first_step_year = max(start_year, withdrawal_year - n_steps)
    span = withdrawal_year - first_step_year or 1
    out = []
    for i in range(span + 1):
        y = first_step_year + i
        frac = i / span
        out.append({'year': y,
                    'safe_pct': round(current_safe_pct + (target_safe_pct - current_safe_pct) * frac, 2)})
    return out


# ── Drivers ─────────────────────────────────────────────────────────────────────
def project(start_year: int, start_value_cents: int, birth_year: int, *,
            plan: dict, family_income_cents: int | None = None,
            return_rate: float, last_contribution_year: int,
            entitlement_start_year: int | None = None,
            withdrawal=None, end_year: int | None = None,
            start_contrib_cents: int = 0, start_grant_cents: int = 0,
            start_bond_cents: int = 0) -> dict:
    """Year-by-year RDSP timeline: accumulation, then (optional) decumulation.

    `plan[year]` = {'contribution': cents, 'grant': cents|None, 'bond': cents|None}.
    When grant/bond are None and `family_income_cents` is given, they're computed
    from the rules (CDSG catch-up + CDSB); otherwise the plan's explicit values are
    used (e.g. seeded from the Excel). Contributions are clamped to the $200k cap.

    `withdrawal` (optional) = {
        'start_year', 'rate' (drawdown return),
        'mode': 'ldap' | 'ldap_dap' | 'max' | 'to_age',
        'lumps': {year: cents}, 'target': cents (ldap_dap top-up),
        'to_age': int, 'bequest': cents}
    PGAP (govt grants+bonds > personal contributions) caps the annual withdrawal at
    max(10% FMV, LDAP); non-PGAP is uncapped. The AHA is reported per row (warning)
    but not clawed back. Each withdrawal is split into taxable / non-taxable.

    Returns {'rows': [...], 'summary': {...}}. Each row carries year, age, phase, pgap,
    contribution, grant, bond, growth, withdrawal, taxable, non_taxable, and value.
    """
    end_year = end_year or (start_year + (CONTRIBUTION_LAST_AGE - (start_year - birth_year)) + 40)
    entitlement_start_year = entitlement_start_year or max(PROGRAM_START_YEAR, start_year)

    value = start_value_cents
    # Seed the running totals with contributions/grants/bonds already made before the
    # projection starts (they're baked into start_value) — needed for PGAP status and
    # the tax base, which depend on *lifetime* totals, not just the projected years.
    contrib_total = start_contrib_cents
    grant_total = start_grant_cents
    bond_total = start_bond_cents
    buckets: list = []
    grant_bond_by_year: dict = {}
    to_age_payment = 0          # 'to_age' mode fixes a level payment at withdrawal start
    non_taxable_withdrawn = 0   # running total of the tax-free (contribution) portion paid out
    rows = []

    # Pre-accrue entitlement for eligible years strictly before the projection
    # start (so catch-up room exists on day one).
    for y in range(entitlement_start_year, start_year):
        age = y - birth_year
        if age <= GRANT_BOND_LAST_AGE and family_income_cents is not None:
            buckets.append(new_entitlement_bucket(y, family_income_cents))

    for year in range(start_year, end_year + 1):
        age = year - birth_year
        spec = plan.get(year, {})
        phase = 'accumulation'

        # ── Contributions + matching ──
        contribution = grant = bond = 0
        can_contribute = age <= CONTRIBUTION_LAST_AGE and year <= last_contribution_year
        if can_contribute:
            contribution = int(spec.get('contribution') or 0)
            # never breach the $200k lifetime contribution cap
            contribution = max(0, min(contribution, CONTRIBUTION_LIFETIME_CAP - contrib_total))

            # Grants & bonds are only available through the year the beneficiary
            # turns 49 — even a plan override can't pay one after that.
            if age <= GRANT_BOND_LAST_AGE:
                if family_income_cents is not None:
                    buckets.append(new_entitlement_bucket(year, family_income_cents))

                override_grant = spec.get('grant')
                if override_grant is not None:
                    grant = max(0, min(int(override_grant), CDSG_LIFETIME_MAX - grant_total))
                elif family_income_cents is not None:
                    grant, buckets = grant_on_contribution(buckets, year, contribution, grant_total)

                override_bond = spec.get('bond')
                if override_bond is not None:
                    bond = max(0, min(int(override_bond), CDSB_LIFETIME_MAX - bond_total))
                elif family_income_cents is not None:
                    bond = bond_for_year(family_income_cents, bond_total)

        # ── Withdrawals (LDAP / DAP per the RDSP rules) ──
        withdrawal_amt = taxable = non_taxable = 0
        pgap = False
        if withdrawal and year >= withdrawal['start_year']:
            phase = 'decumulation'
            draw_rate = withdrawal.get('rate', return_rate)
            grown = _grow(value, draw_rate)               # FMV at the start of the year
            bequest = withdrawal.get('bequest', 0)
            ldap_amt = ldap_payment(grown, age)
            # PGAP while government money (grants+bonds) exceeds personal contributions.
            pgap = (grant_total + bond_total) > contrib_total
            # PGAP caps the annual withdrawal at the greater of 10% FMV or LDAP;
            # non-PGAP is uncapped (up to the full balance).
            cap = max(grown // 10, ldap_amt) if pgap else grown

            mode = withdrawal.get('mode', 'ldap')
            if mode == 'max':                              # take the efficient legal max each year
                withdrawal_amt = max(grown // 10, ldap_amt)
            elif mode == 'to_age':                         # deplete to the bequest by age N (non-PGAP)
                if to_age_payment == 0:
                    to_age_payment = level_payment_to_age(
                        value, age, withdrawal.get('to_age', LDAP_AGE_FACTOR), draw_rate, bequest)
                withdrawal_amt = min(to_age_payment, cap)
            elif mode == 'ldap_dap':                       # LDAP plus a DAP (lump and/or top-up to target)
                dap = int((withdrawal.get('lumps') or {}).get(year, 0))
                target = int(withdrawal.get('target') or 0)
                if target:
                    dap = max(dap, target - ldap_amt)
                withdrawal_amt = min(ldap_amt + max(0, dap), cap)
            else:                                          # 'ldap'
                withdrawal_amt = ldap_amt

            withdrawal_amt = max(0, min(withdrawal_amt, grown))
            if bequest:                                    # never drop the balance below the bequest
                withdrawal_amt = max(0, min(withdrawal_amt, grown - bequest))

            # Taxable vs non-taxable split (CRA RC4460): non-taxable = withdrawal × B/C,
            # B = contributions not yet withdrawn, C = FMV − AHA. The rest is taxable.
            aha = holdback_amount(grant_bond_by_year, year)
            B = max(0, contrib_total - non_taxable_withdrawn)
            C = max(1, grown - aha)
            non_taxable = min(withdrawal_amt, int(round(withdrawal_amt * B / C)), B)
            taxable = withdrawal_amt - non_taxable
            non_taxable_withdrawn += non_taxable

            growth = grown - value
            value = grown - withdrawal_amt                 # AHA clawback is warning-only, not deducted
        else:
            # Accumulation: grow, then add the year's inflows.
            grown = _grow(value, return_rate)
            growth = grown - value
            value = grown + contribution + grant + bond

        contrib_total += contribution
        grant_total += grant
        bond_total += bond
        if grant or bond:
            grant_bond_by_year[year] = grant_bond_by_year.get(year, 0) + grant + bond

        rows.append({
            'year': year, 'age': age, 'phase': phase, 'pgap': pgap,
            'contribution': contribution, 'grant': grant, 'bond': bond,
            'growth': growth, 'withdrawal': withdrawal_amt,
            'taxable': taxable, 'non_taxable': non_taxable, 'value': value,
            # Remaining tax-free base = contributions not yet withdrawn; the rest of
            # the balance is taxable when withdrawn. Powers the tax chart view.
            'tax_free_base': max(0, min(value, contrib_total - non_taxable_withdrawn)),
            'holdback': holdback_amount(grant_bond_by_year, year),
            'contrib_total': contrib_total, 'grant_total': grant_total, 'bond_total': bond_total,
        })

        if withdrawal and phase == 'decumulation' and value <= 0:
            break

    depletes_age = next((r['age'] for r in rows if r['phase'] == 'decumulation' and r['value'] <= 0), None)
    summary = {
        'contrib_total': contrib_total, 'grant_total': grant_total, 'bond_total': bond_total,
        'free_money': grant_total + bond_total,
        'contrib_room': max(0, CONTRIBUTION_LIFETIME_CAP - contrib_total),
        'grant_room': max(0, CDSG_LIFETIME_MAX - grant_total),
        'bond_room': max(0, CDSB_LIFETIME_MAX - bond_total),
        'final_value': value,
        'depletes_age': depletes_age,
        'taxable_total': sum(r['taxable'] for r in rows),
        'non_taxable_total': sum(r['non_taxable'] for r in rows),
    }
    return {'rows': rows, 'summary': summary}


def compare_presets(presets: dict, **project_kwargs) -> dict:
    """Run `project` once per return preset → {label: project_result}. Used by the
    UI's preset comparison (same plan, different return assumptions).
    """
    out = {}
    for label, rate in presets.items():
        out[label] = project(return_rate=rate, **project_kwargs)
    return out


# ── Excel seed + reconciliation ─────────────────────────────────────────────────
def parse_excel_plan(path: str) -> list:
    """Read `RDSP Tracker.xlsx` → [{'year','contribution','grant','bond'} …] in cents.

    Expects columns: Year, Contribution, Grant, Bond (the projection/actual columns
    are ignored — the app computes those itself).
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip().lower() if c is not None else '' for c in rows[0]]
    idx = {name: header.index(name) for name in ('year', 'contribution', 'grant', 'bond') if name in header}
    out = []
    for r in rows[1:]:
        y = r[idx['year']] if 'year' in idx else None
        if not isinstance(y, (int, float)):
            continue

        def cell(name):
            v = r[idx[name]] if name in idx and idx[name] < len(r) else None
            return to_cents(v) if isinstance(v, (int, float)) else 0

        out.append({'year': int(y), 'contribution': cell('contribution'),
                    'grant': cell('grant'), 'bond': cell('bond')})
    return out


def reconcile_excel(path: str) -> dict:
    """Sanity-check the seed file against the statutory caps. Returns totals + any
    warnings (e.g. grants summing past $70k) so the UI can flag a bad plan.
    """
    plan = parse_excel_plan(path)
    grant_total = sum(p['grant'] for p in plan)
    bond_total = sum(p['bond'] for p in plan)
    contrib_total = sum(p['contribution'] for p in plan)
    warnings = []
    if grant_total > CDSG_LIFETIME_MAX:
        warnings.append(f'Grants total ${to_dollars(grant_total):,.0f} exceeds the $70,000 CDSG cap.')
    if bond_total > CDSB_LIFETIME_MAX:
        warnings.append(f'Bonds total ${to_dollars(bond_total):,.0f} exceeds the $20,000 CDSB cap.')
    if contrib_total > CONTRIBUTION_LIFETIME_CAP:
        warnings.append(f'Contributions total ${to_dollars(contrib_total):,.0f} exceeds the $200,000 cap.')
    return {'plan': plan, 'contrib_total': contrib_total, 'grant_total': grant_total,
            'bond_total': bond_total, 'warnings': warnings}
