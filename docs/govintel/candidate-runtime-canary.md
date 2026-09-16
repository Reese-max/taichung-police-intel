# Candidate news collector runtime canary (#22 / #14)

This is a read-only, single-observation diagnostic for S-001/S-019/S-032. It calls the actual `online_collect.collect_source` with `max_details=1`; it never promotes a source, changes canonical state, or deploys.

```sh
python scripts/candidate-runtime-canary.py --output /tmp/candidate-live.json
python -m unittest discover -s tests -p 'test_candidate_runtime_canary.py' -v
```

The reader permits only HTTPS on each configured official host or its www alias. It has no implicit requests retry or environment credentials, allows at most six actual HTTP calls per source including same-host redirects, limits each body to 2 MiB, and uses bounded connect/read timeouts. Redirects to other hosts are rejected rather than silently followed.

Report fields include HTTP call count, last HTTP status, elapsed time, parser counts, manifest hash and an explicit CANDIDATE status. A collector's completeness claim is not independent proof of pagination coverage. Error counts remain null, not zero. No raw body, personal details or derived summary is included.

The workflow runs offline wrapper and real collector fixture tests on PRs. A source-file change pushed to the existing candidate branch additionally runs one live observation. It has contents:read only, no schedule, no secrets, no git push and no deploy. The exact tracked source and result are retained as a replay artifact; source.tar excludes .git and untracked credentials.

Five local wrapper contract tests passed. They use a fake collector boundary and are not represented as a real-government-source observation. Actual live results and the full required CI must be read from the resulting Actions run before any success claim.

A single successful response is not seven days of canary evidence. Production wiring, independently proven pagination, approved-source policy and the observation window remain required before closing #22 or promoting #14 sources.
