# Kollam ONCourts: Plan For A More Defensible DiD

## Goal

Estimate the impact of the ONCourts setup in Kollam on case-resolution timing, while separating that effect from statewide Kerala changes that also occur around the same period.

## Why The Current Before/After Is Not Enough

- Kerala itself slows down from the `2023-2024` filing cohorts to the `2025+` filing cohorts.
- ONCourts cases are not obviously like-for-like with all legacy Kollam cases.
- The split between `ONCourts` and `Rest of Kollam` may reflect routing, eligibility, or operational selection rather than pure treatment assignment.
- Filing cohorts also differ in maturity: `2023-2024` cohorts have had much more time to resolve than `2025` cohorts.

## Recommended Design

Use filing-month cohorts as the unit of analysis and estimate an event-study / DiD on matched court groups, not just on the full Kerala aggregate.

### Treatment

- Treated group: cases filed into `Kollam ONCourts` after the rollout date.
- Pre-treatment baseline for the treated group: legacy Kollam cases from the immediately preceding period that would plausibly have gone through the same functional channel.

### Controls

Use multiple control layers, not just one:

- Primary control: matched non-Kollam magistrate court cohorts with similar `CC/ST` mix, filing volumes, and pre-period resolution patterns.
- Secondary control: `Rest of Kollam` to test whether changes are specific to ONCourts versus a district-wide Kollam shift.
- Optional synthetic control / donor-pool benchmark: weighted combination of similar Kerala courts using only pre-period dynamics.

## Sample Restrictions

To get closer to like-for-like:

- Restrict to the same case prefixes: `CC`, `ST`.
- Restrict to a common filing window around rollout.
- Use the same disposal filters already applied in KM modeling.
- Exclude transferred / made-over cases.
- Drop rows with unusable filing dates or inconsistent timing fields.
- Where possible, isolate benches or establishments whose work is comparable before and after rollout.

## Outcomes

Estimate more than one outcome:

- `Resolved within 180 / 365 / 540 days`
- Hazard / survival outcomes from KM or Cox-style models
- Median time to disposal
- Share still pending at fixed horizons

## Identification Checks

Before treating the estimate as causal, run these checks:

- Parallel-trends check on pre-period cohorts using older filing windows.
- Compare case mix across treated and control groups:
  - filing volume
  - case type mix
  - disposal mix
  - represented / unrepresented mix if available
  - court / establishment composition
- Check whether ONCourts intake rules changed the composition of cases entering treatment.
- Test sensitivity to different control pools.

## Estimation Strategy

### Minimal DiD

Estimate:

`Outcome = treated_group + post_rollout + treated_group × post_rollout + fixed effects + controls`

Recommended fixed effects:

- filing month fixed effects
- court-group fixed effects

Recommended controls:

- case type mix
- filing load
- district-level month conditions if available

### Better Version

Use an event-study specification with leads and lags around rollout:

- this shows whether pre-trends are already diverging
- this makes the post-rollout dynamics visible rather than compressing everything into one coefficient

## Practical Build Order

1. Freeze the exact `2023-2024` and `2025` source-of-truth cohorts.
2. Construct a court-level panel by filing month.
3. Define a candidate treated set and 2-3 control pools.
4. Run pre-trend diagnostics first.
5. Only if pre-trends look acceptable, estimate DiD / event-study.
6. Report the descriptive transition charts alongside the causal model, not instead of them.

## Deliverables

- Descriptive transition page:
  already built in `analysis/kollam_did_analysis.html`
- Diagnostics notebook / script:
  pre-trend checks and balance tables
- DiD output:
  event-study chart, coefficient table, robustness variants

## Bottom Line

The current page should be read as transition evidence:

- old Kollam versus ONCourts
- old Kollam versus rest of Kollam
- each benchmarked to Kerala in the same period

It should not yet be presented as a clean causal DiD until the control design and pre-trend checks are in place.
