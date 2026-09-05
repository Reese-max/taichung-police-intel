# 50-Persona Audit — Round 1

Date: 2026-09-06
Protocol: `Reese-max/autodev-ng/docs/portfolio-audit/2026-09-06-50-persona-audit.md`

> Fixed 50-persona model simulation plus repository and GitHub Actions runtime evidence; not 50 human participants.

## Round 1 result

Status: **P1 RUNTIME REGRESSION OPEN — NOT CLEAN**

### P1 — scheduled refresh succeeds through evidence generation, then publication verification/build fails

The project documents twice-daily GitHub Actions refresh/deploy as a core capability. Current GitHub Actions gives direct runtime evidence of a regression:

- Latest inspected scheduled run `33969009127` (2026-09-05) concluded `failure`.
- Job `101314085607` successfully completed source refresh, V1 verification, V2 daily-intelligence build, and V2 verification.
- `Run full verification gate and build static site` then failed.
- Publication commit and Pages artifact upload were skipped, and the deploy job was skipped.
- GitHub reports 19 failed scheduled runs in workflow history; the most recent successful scheduled run returned by the API is `33036454707` on 2026-08-27, before current main.

Actionable issue: #9 — `[P1][50-persona audit] Restore scheduled refresh/deploy pipeline after repeated verification failures`.

## Positive evidence

The failure is fail-closed: stale/invalid output was **not** deployed after the gate failed. Official-source collection and the V1/V2 publication verifiers themselves succeeded in the latest failed run. That contains the correctness risk, but it does not restore freshness/availability.

## Fixed-persona impact

C11 policy user/D02 owner/C05 SRE/I05 partial-success/J48 unattended-operation personas receive stale public intelligence even though collection completed successfully.

## Regression gates

1. Identify the exact failing full-gate/build assertion on scheduled generated data.
2. Fix without weakening provenance, secret, data-quality or publication gates.
3. Preserve diagnostic/replay evidence when collection succeeds but publication fails.
4. Surface an operator-visible stale-publication alert.
5. Obtain at least one successful MORNING and one successful EVENING scheduled run on current/recent code.
6. Re-run the same fixed personas and require two consecutive rounds without new P0/P1/P2.

## Runtime status

**Runtime regression confirmed.** This report relies on actual GitHub Actions run/job outcomes, not a simulated claim. The exact console error inside the failed full-verification step was not available through the connector response and remains part of issue #9 diagnosis.