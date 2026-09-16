# Issue #22 runtime verification — collector core fixes

Verified 2026-09-17 against branch `devin/issue-14-news-collectors`.

## Implemented in this phase

- `collect_source(..., max_details=...)` now accepts the bounded canary option and forwards it only to list-news collectors.
- ROC/Gregorian parsing is disjoint: four-digit Gregorian dates are parsed first; ROC parsing cannot start in the middle of `2026`.
- Invalid calendar dates fail closed instead of raising from `roc_date`.
- A single list page is not automatically marked complete. It is complete only when dated rows are reverse chronological and the observed page reaches strictly before the requested window start; otherwise the window is `PARTIAL` until source-specific pagination is enumerated.

## Runtime evidence

One-shot GitHub Actions run:

- https://github.com/Reese-max/taichung-police-intel/actions/runs/35124092149
- job `patch-and-verify`: **success**
- targeted patch application: success
- `test_news_list_collector.py` regressions: success
- `online_collect.py --self-check`: success
- the one-shot patch workflow/script removed themselves from the resulting branch commit.

Resulting implementation commit: `41faf8f66813a5b21b25e14f3c449b90b59c3d95`.

## Still open in #22

This does **not** yet claim the candidate sources are production-active. The remaining publication-path work must be reconciled with #20's protected-main-safe publication design and the exact active-source freshness contract before S-001/S-019/S-032 can appear as production coverage.

Still required:

1. wire a promoted candidate through source-status → feed → V2 source context → frontend/validator;
2. preserve explicit `CANDIDATE` state before promotion;
3. run bounded live canary / observation window;
4. rebase or otherwise reconcile PR #16 after #20 lands on main.
