"""Unit tests for the RDSP engine (rdsp.py).

Runs under pytest *or* standalone: `python tests/test_rdsp.py` executes every
test_* function and prints a summary (handy until pytest is installed).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rdsp
from rdsp import (
    to_cents, to_dollars, grant_tier, new_entitlement_bucket, grant_on_contribution,
    bond_for_year, holdback_amount, ldap_payment, level_payment_to_age, glide_steps,
    project, reconcile_excel, HIGH_TIER, LOW_TIER, ANNUAL_GRANT_MAX, CDSG_LIFETIME_MAX,
    CDSB_LIFETIME_MAX, CONTRIBUTION_LIFETIME_CAP, CDSB_FULL,
    apply_stress, LOST_DECADE_RETURN,
)
from rdsp_view import (
    _gl_shock_equity, _gl_return_map, _gl_glide_safe, _gl_flat_safe, glide_lab_breakeven,
)

D = to_cents  # dollars → cents shorthand


# ── money helpers ───────────────────────────────────────────────────────────────
def test_money_round_trip():
    assert to_cents(1234.56) == 123456
    assert to_dollars(123456) == 1234.56
    assert to_cents(0.1) + to_cents(0.2) == to_cents(0.3)   # no float dust


# ── CDSG matching (single year, no catch-up) ────────────────────────────────────
def _high_bucket(year=2026):
    return [new_entitlement_bucket(year, D(50_000))]   # income well under threshold


def test_grant_tier_split():
    assert grant_tier(D(50_000)) == HIGH_TIER
    assert grant_tier(D(200_000)) == LOW_TIER
    assert grant_tier(rdsp.CDSG_INCOME_THRESHOLD) == HIGH_TIER   # boundary is inclusive


def test_grant_high_tier_amounts():
    # $1,500 → max $3,500 ; $1,000 → $2,500 ; $500 → $1,500 ; $0 → $0
    assert grant_on_contribution(_high_bucket(), 2026, D(1_500), 0)[0] == D(3_500)
    assert grant_on_contribution(_high_bucket(), 2026, D(1_000), 0)[0] == D(2_500)
    assert grant_on_contribution(_high_bucket(), 2026, D(500),   0)[0] == D(1_500)
    assert grant_on_contribution(_high_bucket(), 2026, 0,        0)[0] == 0
    # over-contributing past the matchable room still caps at $3,500
    assert grant_on_contribution(_high_bucket(), 2026, D(5_000), 0)[0] == D(3_500)


def test_grant_low_tier_amounts():
    low = [new_entitlement_bucket(2026, D(200_000))]
    assert grant_on_contribution(low, 2026, D(1_000), 0)[0] == D(1_000)
    assert grant_on_contribution(low, 2026, D(5_000), 0)[0] == D(1_000)


def test_grant_catchup_highest_rate_first():
    # Two years of high-tier entitlement; $3,000 contribution.
    # 300%: $1,000 contrib → $3,000 ; 200%: $2,000 contrib → $4,000 ; total $7,000.
    buckets = [new_entitlement_bucket(2025, D(50_000)), new_entitlement_bucket(2026, D(50_000))]
    grant, _ = grant_on_contribution(buckets, 2026, D(3_000), 0)
    assert grant == D(7_000)


def test_grant_annual_cap():
    buckets = [new_entitlement_bucket(2023 + i, D(50_000)) for i in range(4)]  # 4 yrs entitlement
    grant, _ = grant_on_contribution(buckets, 2026, D(6_000), 0)
    assert grant == ANNUAL_GRANT_MAX            # capped at $10,500/yr


def test_grant_lifetime_cap():
    buckets = [new_entitlement_bucket(2026, D(50_000))]
    grant, _ = grant_on_contribution(buckets, 2026, D(1_500), CDSG_LIFETIME_MAX - D(1_000))
    assert grant == D(1_000)                    # only $1,000 of lifetime room left


def test_grant_entitlement_expires_after_10_years():
    old = [new_entitlement_bucket(2010, D(50_000))]   # earned 16 yrs before
    grant, _ = grant_on_contribution(old, 2026, D(1_500), 0)
    assert grant == 0                            # pruned by the 10-yr window


# ── CDSB ────────────────────────────────────────────────────────────────────────
def test_bond_full_partial_zero():
    assert bond_for_year(D(30_000), 0) == CDSB_FULL          # full $1,000
    assert bond_for_year(D(60_000), 0) == 0                  # above upper threshold
    partial = bond_for_year(D(45_000), 0)
    assert 0 < partial < CDSB_FULL                           # phased out


def test_bond_lifetime_cap():
    assert bond_for_year(D(20_000), CDSB_LIFETIME_MAX - D(50)) == D(50)


# ── Holdback (AHA) ──────────────────────────────────────────────────────────────
def test_holdback_rolling_10_years():
    gb = {2020: D(1_000), 2025: D(500), 2030: D(300)}
    # As of 2030: 2020 is exactly 10 yrs out (excluded); 2025 + 2030 count.
    assert holdback_amount(gb, 2030) == D(800)


# ── Decumulation primitives ─────────────────────────────────────────────────────
def test_ldap_payment():
    assert ldap_payment(D(10_000), 60) == int(round(D(10_000) / 23))   # age<80 → /(83-age)
    assert ldap_payment(D(10_000), 80) == int(round(D(10_000) / 3))    # age≥80 → /3 (B+3-C)
    assert ldap_payment(D(10_000), 90) == int(round(D(10_000) / 3))


def test_level_payment_to_age_zero_rate():
    # Deplete $10,000 from age 60 to 70 at 0% → $1,000/yr.
    assert level_payment_to_age(D(10_000), 60, 70, 0.0) == D(1_000)


# ── Glide path ──────────────────────────────────────────────────────────────────
def test_glide_steps_monotonic_to_target():
    steps = glide_steps(2044, 2054, current_safe_pct=30, target_safe_pct=80)
    assert steps[0]['year'] == 2044 and steps[0]['safe_pct'] == 30
    assert steps[-1]['year'] == 2054 and steps[-1]['safe_pct'] == 80
    assert len(steps) == 11                      # one row per year, inclusive
    pcts = [s['safe_pct'] for s in steps]
    assert pcts == sorted(pcts)                 # never steps backward


def test_blended_return_endpoints_and_midpoint():
    from rdsp import blended_return
    assert blended_return(0, 0.09, 0.04) == 0.09          # all growth
    assert blended_return(100, 0.09, 0.04) == 0.04        # fully safe
    assert abs(blended_return(50, 0.10, 0.04) - 0.07) < 1e-9


def test_apply_stress_crash():
    base = {2043: 0.06, 2044: 0.06, 2045: 0.06}
    s = apply_stress(base, 'crash', 2043, 0.30)
    assert s[2043] == -0.30 and s[2044] == -0.15 and s[2045] == 0.06   # bounce, then base resumes
    assert base[2043] == 0.06                                          # original untouched (copy)


def test_apply_stress_lost_decade():
    base = {y: 0.06 for y in range(2043, 2060)}
    s = apply_stress(base, 'decade', 2045, 0.30)
    assert all(s[y] == LOST_DECADE_RETURN for y in range(2045, 2055))  # 10 flat years
    assert s[2043] == 0.06 and s[2055] == 0.06                         # outside the window unchanged


def test_apply_stress_horizon_clamped():
    base = {2043: 0.06}
    s = apply_stress(base, 'crash', 2043, 0.40)
    assert s == {2043: -0.40}                                          # +1 year not in base → not added


def test_stress_early_crash_depletes_sooner():
    # Same plan; a crash at the start of drawdown depletes sooner than smooth returns.
    base = {y: 0.05 for y in range(2043, 2080)}
    crash = apply_stress(base, 'crash', 2043, 0.40)
    common = dict(plan={}, return_rate=0.05, last_contribution_year=2033, end_year=2080,
                  withdrawal={'start_year': 2043, 'mode': 'max', 'rate': 0.05})
    smooth = project(2043, D(200_000), 1994, return_by_year=base, **common)
    shocked = project(2043, D(200_000), 1994, return_by_year=crash, **common)
    assert shocked['summary']['final_value'] < smooth['summary']['final_value']


def test_project_return_by_year_overrides_flat_rate():
    # A per-year return map drives growth in both phases, overriding the flat rate.
    rby = {2027: 0.10, 2028: 0.0}
    res = project(2027, D(100_000), 1994, plan={}, return_rate=0.05,
                  last_contribution_year=2026, end_year=2028, return_by_year=rby)
    assert res['rows'][0]['value'] == D(110_000)          # used 10%, not the flat 5%
    assert res['rows'][1]['value'] == D(110_000)          # used 0%, value unchanged


# ── project: accumulation ───────────────────────────────────────────────────────
def test_project_accumulation_explicit_plan():
    res = project(2026, 0, 1994,
                  plan={2026: {'contribution': D(1_500), 'grant': D(3_500), 'bond': 0}},
                  return_rate=0.0, last_contribution_year=2026, end_year=2026)
    row = res['rows'][0]
    assert row['value'] == D(5_000)             # 1,500 + 3,500
    assert res['summary']['contrib_total'] == D(1_500)
    assert res['summary']['grant_total'] == D(3_500)
    assert res['summary']['free_money'] == D(3_500)


def test_project_enforces_200k_cap():
    res = project(2026, 0, 1994,
                  plan={2026: {'contribution': D(250_000), 'grant': 0, 'bond': 0}},
                  return_rate=0.0, last_contribution_year=2026, end_year=2026)
    assert res['rows'][0]['contribution'] == CONTRIBUTION_LIFETIME_CAP
    assert res['summary']['contrib_total'] == CONTRIBUTION_LIFETIME_CAP


def test_project_values_are_integer_cents():
    res = project(2026, D(74_620), 1994,
                  plan={y: {'contribution': D(1_500), 'grant': D(3_500), 'bond': 0} for y in range(2027, 2034)},
                  return_rate=0.07, last_contribution_year=2033, end_year=2050)
    assert all(isinstance(r['value'], int) for r in res['rows'])   # never floats


def test_project_computes_grant_from_income():
    # No grant override → engine computes $3,500 on a $1,500 contribution at low income.
    res = project(2026, 0, 1994,
                  plan={2026: {'contribution': D(1_500)}},
                  family_income_cents=D(40_000),
                  return_rate=0.0, last_contribution_year=2026, end_year=2026)
    assert res['rows'][0]['grant'] == D(3_500)


def test_project_no_grant_after_age_49():
    # Beneficiary born 1994 → age 50 in 2044; a contribution then earns no grant.
    res = project(2044, 0, 1994,
                  plan={2044: {'contribution': D(1_500)}},
                  family_income_cents=D(40_000),
                  return_rate=0.0, last_contribution_year=2044, end_year=2044)
    assert res['rows'][0]['grant'] == 0


# ── project: decumulation (LDAP / DAP / PGAP / tax) ─────────────────────────────
def test_project_ldap_only():
    res = project(2043, D(100_000), 1994, plan={}, return_rate=0.0, last_contribution_year=2033,
                  withdrawal={'start_year': 2043, 'mode': 'ldap', 'rate': 0.0}, end_year=2050)
    assert res['rows'][0]['withdrawal'] == int(round(D(100_000) / 34))   # age 49 → FMV/(83-49)
    assert res['summary']['depletes_age'] is None                        # LDAP never fully depletes


def test_project_max_mode_takes_ten_percent():
    res = project(2043, D(100_000), 1994, plan={}, return_rate=0.0, last_contribution_year=2033,
                  withdrawal={'start_year': 2043, 'mode': 'max', 'rate': 0.0}, end_year=2043)
    assert res['rows'][0]['withdrawal'] == D(100_000) // 10              # 10% beats LDAP at age 49


def test_project_pgap_caps_dap():
    # Government money > contributions → PGAP → a big DAP caps at max(10% FMV, LDAP).
    res = project(2040, D(50_000), 1994,
                  plan={2040: {'contribution': D(1_000), 'grant': D(10_000), 'bond': 0}},
                  return_rate=0.0, last_contribution_year=2040,
                  withdrawal={'start_year': 2041, 'mode': 'ldap_dap', 'lumps': {2041: D(99_000)}, 'rate': 0.0},
                  end_year=2041)
    r = res['rows'][-1]
    assert r['pgap'] is True
    assert r['withdrawal'] == D(61_000) // 10                            # capped at 10% of the $61k FMV


def test_project_non_pgap_uncapped():
    # Contributions > government money → non-PGAP → the DAP is not capped.
    res = project(2040, D(50_000), 1994,
                  plan={2040: {'contribution': D(20_000), 'grant': D(1_000), 'bond': 0}},
                  return_rate=0.0, last_contribution_year=2040,
                  withdrawal={'start_year': 2041, 'mode': 'ldap_dap', 'lumps': {2041: D(40_000)}, 'rate': 0.0},
                  end_year=2041)
    r = res['rows'][-1]
    assert r['pgap'] is False
    assert r['withdrawal'] >= D(40_000)                                  # well past any 10% cap


def test_project_to_age_depletes():
    res = project(2043, D(100_000), 1994, plan={}, return_rate=0.0, last_contribution_year=2033,
                  withdrawal={'start_year': 2043, 'mode': 'to_age', 'to_age': 60, 'rate': 0.0},
                  end_year=2060)
    assert res['summary']['depletes_age'] in (59, 60)


def test_project_bequest_floor():
    res = project(2043, D(100_000), 1994, plan={}, return_rate=0.0, last_contribution_year=2033,
                  withdrawal={'start_year': 2043, 'mode': 'to_age', 'to_age': 60,
                              'rate': 0.0, 'bequest': D(20_000)}, end_year=2060)
    assert min(r['value'] for r in res['rows']) >= D(20_000)


def test_project_tax_split():
    # $40k contributions + $60k grant grow to $100k; withdraw $10k once the grant is >10yr old
    # (AHA = 0). Tax-free share = contributions/FMV = 40% → $4,000 tax-free, $6,000 taxable.
    res = project(2040, 0, 1994,
                  plan={2040: {'contribution': D(40_000), 'grant': D(60_000), 'bond': 0}},
                  return_rate=0.0, last_contribution_year=2040,
                  withdrawal={'start_year': 2055, 'mode': 'ldap_dap', 'lumps': {2055: D(10_000)}, 'rate': 0.0},
                  end_year=2055)
    r = res['rows'][-1]
    assert r['non_taxable'] == D(4_000)
    assert r['taxable'] == D(6_000)


def test_project_aha_reported_not_deducted():
    # The holdback is reported (for the warning) but NOT clawed back from the balance.
    res = project(2040, 0, 1994,
                  plan={2040: {'contribution': 0, 'grant': D(1_000), 'bond': 0}},
                  return_rate=0.0, last_contribution_year=2040,
                  withdrawal={'start_year': 2041, 'mode': 'ldap_dap', 'lumps': {2041: D(500)}, 'rate': 0.0},
                  end_year=2041)
    r = res['rows'][-1]
    assert r['holdback'] == D(1_000)                                     # AHA still computed
    assert r['value'] == D(1_000) - r['withdrawal']                     # but no 3× clawback deducted


# ── Excel reconciliation (against the real seed file) ────────────────────────────
def test_reconcile_excel_matches_caps():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'RDSP Tracker.xlsx')
    if not os.path.exists(path):
        print('  (skipped: RDSP Tracker.xlsx not found)')
        return
    rec = reconcile_excel(path)
    assert rec['grant_total'] == CDSG_LIFETIME_MAX        # plan maxes the $70k CDSG cap
    assert rec['contrib_total'] <= CONTRIBUTION_LIFETIME_CAP
    assert rec['warnings'] == []


# ── Glide Lab helpers (allocation-based comparison) ──────────────────────────────
def test_gl_shock_equity_crash_and_decade():
    assert _gl_shock_equity(2050, 2050, 'crash', 50, 10) == -0.50    # full hit
    assert _gl_shock_equity(2051, 2050, 'crash', 50, 10) == -0.25    # partial bounce
    assert _gl_shock_equity(2052, 2050, 'crash', 50, 10) is None     # over
    assert _gl_shock_equity(2050, 2050, 'decade', 30, 7) == -0.30    # crash-led: initial drop
    assert _gl_shock_equity(2051, 2050, 'decade', 30, 7) == LOST_DECADE_RETURN  # then flat, no recovery
    assert _gl_shock_equity(2056, 2050, 'decade', 30, 7) == LOST_DECADE_RETURN
    assert _gl_shock_equity(2057, 2050, 'decade', 30, 7) is None     # past the decade
    assert _gl_shock_equity(2050, 2050, 'none', 50, 10) is None      # no shock


def test_gl_flat_safe_steps_at_withdrawal():
    at = _gl_flat_safe(2043, 40.0)
    assert at(2042) == 0.0       # 100% stocks before withdrawal
    assert at(2043) == 40.0      # one step to the retirement mix at withdrawal
    assert at(2060) == 40.0


def test_gl_glide_safe_ramps():
    at = _gl_glide_safe(2050, 2060, 0.0, 80.0)
    assert at(2049) == 0.0       # before the window
    assert at(2050) == 0.0       # window start = current
    assert at(2060) == 80.0      # window end = target
    assert at(2065) == 80.0      # after = target
    assert 0.0 < at(2055) < 80.0 # ramps in between


def test_gl_return_map_blend():
    # 50% safe @ 4%, stock @ 8% -> 0.5*4 + 0.5*8 = 6%
    m = _gl_return_map([2040], lambda y: 50.0, 0.08, 0.04)
    assert abs(m[2040] - 0.06) < 1e-9
    # crash year: only the equity sleeve takes the hit -> 0.5*4% + 0.5*(-50%)
    m2 = _gl_return_map([2040], lambda y: 50.0, 0.08, 0.04, 'crash', 2040, 50, 10)
    assert abs(m2[2040] - (0.5 * 0.04 + 0.5 * -0.50)) < 1e-9


def test_glide_lab_breakeven():
    assert abs(glide_lab_breakeven(-900, 100) - 0.9) < 1e-9   # cost 900, gain 100 -> 90%
    assert glide_lab_breakeven(-900, -100) is None            # glide loses even in a crash
    assert glide_lab_breakeven(50, 200) is None               # glide wins regardless (no break-even)


# ── standalone runner (no pytest needed) ─────────────────────────────────────────
if __name__ == '__main__':
    tests = sorted((n, f) for n, f in globals().items() if n.startswith('test_') and callable(f))
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS  {name}')
            passed += 1
        except Exception as e:
            print(f'  FAIL  {name}: {type(e).__name__}: {e}')
            failed += 1
    print(f'\n{passed} passed, {failed} failed, {len(tests)} total')
    sys.exit(1 if failed else 0)
