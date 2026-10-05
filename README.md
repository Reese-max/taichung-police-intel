# GovIntel AI－公共資訊查詢與個人化追蹤平台

**Current proposal:** GovIntel AI－公共資訊查詢與個人化追蹤平台 (plan v6, 2026-10-01). The former `Taichung Police Public Intelligence` name and its Kiro competition package are historical provenance.

**First-phase task:** help people query public notices, save their chosen area, topic, road or keywords in the same browser, and see supported changes when reopening the site. The deployed baseline remains the five-source Taichung council／government evidence journey. Public query and personal tracking changes require separate acceptance and deployment evidence.

## Current GovIntel state (2026-10-05)

The [2026 GovIntel competition entry](./docs/govintel/competition-2026/README.md) is the canonical judge path. The [current status page](./docs/govintel/competition-2026/CURRENT_STATUS.md) records the complete claim/evidence table and fixed state semantics.

| Current claim | Fixed status | Evidence boundary |
|---|---|---|
| Five-source publication and council evidence journey | `PRODUCTION_ACTIVE` | [active policy](./docs/govintel/source-policy.approved.json), [public data](https://reese-max.github.io/taichung-police-intel/data/source-status.json), [Oct 4 collection run](https://github.com/Reese-max/taichung-police-intel/actions/runs/37169331249). Collection, five public-file hashes and Gateway checks passed; data remains `PARTIAL`. |
| Bounded deployed Query Gateway | `PRODUCTION_ACTIVE` | The dated production verifier checks the published generation; it does not prove semantic answers, full event coverage or v6 query UX acceptance. |
| Public query, browser-only tracking and v6 evaluation workflow | `IMPLEMENTED_NOT_PRODUCTION` | [PR #124](https://github.com/Reese-max/taichung-police-intel/pull/124), [#125](https://github.com/Reese-max/taichung-police-intel/pull/125), [#126](https://github.com/Reese-max/taichung-police-intel/pull/126) are acceptance candidates; a PR or local pass is not deployment. |
| Police／traffic／city／fire source expansion | `CANDIDATE_CANARY` | Four independently observed candidates: S-001／S-032／S-033／S-031. Active policy still contains only the five baseline sources. |
| Located facts, handoff, Review Inbox and PublicEvent fixture | `IMPLEMENTED_NOT_PRODUCTION` | Replayable core; PublicEvent remains `FIXTURE_ONLY`, without production fusion or multi-user approval. |
| D1 fixed-period population query | `IMPLEMENTED_NOT_PRODUCTION` | Official 112Y12M／2023-12 CSV verified: 368 national records, all 29 Taichung districts; local query UI provides the historical sample. It is unpromoted background, not current population or measured task benefit. |
| D2 police-directory query, semantic AI, external notifications and cross-device accounts | `DESIGN_ONLY` | D2 metadata is available but official TGOS resource bytes return 403; coordinates and records remain unverified. Model quality and human benefit have not been measured. |
| Complete publication acceptance and source promotion | `BLOCKED` | Successful dated runs do not replace missing [#20](https://github.com/Reese-max/taichung-police-intel/issues/20) failure/recovery evidence or source promotion reviews. |

**Dated evidence:** [run 37169331249](https://github.com/Reese-max/taichung-police-intel/actions/runs/37169331249), started 2026-10-04 10:22 Asia/Taipei, performed collection and passed publication/hash/Gateway verification. [Run 37212963039](https://github.com/Reese-max/taichung-police-intel/actions/runs/37212963039) also succeeded later that day. S-007 was stale and S-029 failed in the reviewed snapshot; no evidence supports 100–200 new items per day. Read the live receipt's own date before using it. Proposal v6 and the supplement are complete; organizer acceptance and the proposed name change remain unverified.

Release manifest、Pages／Worker／Query 版本核對與 #62 完整驗收狀態見 [Production Closure runbook](./docs/govintel/issue-62-release-closure.md)。本地 `BUILD_ONLY` manifest 與測試 receipts 不代表正式部署；七日 canary、supersession audit 與真人評測仍待完成。

## Quick judge path

1. Read the [current status and claim/evidence table](./docs/govintel/competition-2026/CURRENT_STATUS.md).
2. Follow the [3–5 minute judge path](./docs/govintel/competition-2026/JUDGE_PATH.md) using the current build and `FIXTURE_ONLY` receipts.
3. In the integrated local candidate, open `/public-query/`, `/tracking/` and `/sources/`; query the synthetic road, inspect its revisions, save conditions and reopen the same browser. The query page also exposes D1's fixed 2023-12 district background. The [public demo](https://reese-max.github.io/taichung-police-intel/) remains the dated production baseline until a new deployment is verified.
4. Read the [old-vs-new delta](./docs/govintel/competition-2026/OLD_VS_NEW.md), [evaluation results boundary](./docs/govintel/competition-2026/EVALUATION.md), and [limitations／safety](./docs/govintel/competition-2026/LIMITATIONS_AND_SAFETY.md).

## Historical boundary

The old Kiro submission draft, 2026-08 deadline, prototype demo script／checklist／video, and Kiro usage evidence are preserved in [Historical competition / prior prototype evidence](./docs/govintel/historical/README.md). Root compatibility pointers remain only for existing repository verification; they are not the current judge path.

## Problem and users

People following a road, activity or local issue need to find scattered notices and check whether dates, scope or status changed. A saved query should make later changes easier to inspect without treating repeated notices as independent events. This need remains a hypothesis awaiting consented user tasks; no measured time-saving claim is made.

The public baseline preserves council preparation and evidence navigation. Plan v6 puts public query and personal tracking first; handoff, research exports and multi-user collaboration are later extensions. First-phase tracking stays in the same browser and updates only on opening or refreshing the site. It has no background push or cross-device account. The product uses approved public information and excludes internal duty data, 110 calls, case-level criminal data and operational command.

## What works

- A focused council-preparation brief for a police policy user.
- Five baseline official-source adapters with isolated failure handling.
- Source health kept separate from date-window completeness.
- Intelligence-gap reasons instead of silently turning collection failure into zero results.
- Last-known-good retained when a later source fetch fails.
- Official URLs, collection time, data-as-of time, raw snapshot count, and SHA-256 manifest.
- An evidence drawer with official HLS playback, searchable transcript segments, and word-level timestamp navigation.
- A Traditional Chinese / English toggle covering the primary journey while preserving the official Chinese transcript as labelled navigation text.
- A static-export deployment path that needs no paid database or application server.

The integrated v6 candidate adds public-query, same-browser tracking and a verified D1 historical-background selector. Local implementation and source verification do not establish production deployment, held-out accuracy, semantic-AI benefit or human task time. Current receipts and pending gates are listed in [Verification](./docs/govintel/competition-2026/VERIFICATION.md) and the [source reliability review](./docs/govintel/competition-2026/SOURCE_RELIABILITY_REVIEW_2026-10-05.md).

## Architecture

```text
Official Taichung council and government sources
                |
                v
      Python collector (source-isolated)
                |
                v
health + completeness + gaps + SHA-256 + last-known-good
                |
                v
 apps/web/public/data/source-status.json
                |
                v
   Next.js static export -> GitHub Pages

GitHub Actions: 06:30 and 18:30 Asia/Taipei
```

The repository also contains PostgreSQL migrations for the post-competition durable-history path. The public competition demo deliberately does not require that infrastructure.

V2 可用 [role-profiles.v1.json](./docs/govintel/role-profiles.v1.json) 的版本化公開職能檔切換綜合、議會聯絡與交通／公共安全排序；profile 只產生可重播的 relevance projection，不改寫事件事實、官方證據或來源缺口。每日 brief 同時保存 profile hash 與 ranking policy version，首頁可用 `?profile=council-liaison` 或角色選單切換。

## Setup

Requirements:

- Node.js 20 or newer
- Python 3.11 or newer
- Git for the scheduled publication-state checkpoint and local replay
- Kiro CLI V3 only for reproducing the historical Kiro development evidence (optional)

Install dependencies:

```bash
npm ci --prefix apps/web
python3 -m pip install -r requirements.txt
```

No API key, login, database, or paid service is required to run the checked-in demo.

## Run

Development server:

```bash
npm --prefix apps/web run dev
```

Open `http://localhost:3000`.

Refresh the public-source status and build the same static artifact used by GitHub Pages:

```bash
python online_collect.py --slot evening --demo-output apps/web/public/data/source-status.json --trigger manual
npm --prefix apps/web run build
python -m http.server 8000 --directory apps/web/out
```

Open `http://localhost:8000`. The live refresh performs read-only requests to the listed official public sources.

Read-only Query Gateway (publication slice; optional domain stores):

```bash
python scripts/query-gateway.py --port 8788 --allow-origin http://localhost:3000
```

若要讓 Gateway 使用保存的索引，先建立並以 `--query-store` 傳入；啟動時會
重新核對它與 canonical feed、source status、brief 的 hash，不一致就拒絕服務：

```bash
python scripts/query-store.py build --output runtime/query-store.json
python scripts/query-gateway.py --query-store runtime/query-store.json --port 8788 --allow-origin http://localhost:3000
```

For a persistent local service, opt in to a last-good generation cache outside
the public site directory:

```bash
python scripts/query-gateway.py --last-good-snapshot .runtime/query-generations/gateway.json --port 8788
python scripts/query-gateway-stdio.py --last-good-snapshot .runtime/query-generations/gateway.json
```

Each startup validates one complete publication and atomically saves its exact
canonical bytes and query projection. If the next build or cache write fails,
the service can restart from the previous validated generation. Responses retain
that generation's IDs and hashes, show `STALE_INDEX`, and disable current-answer
validation and bounded no-result claims. `/health` reports `degraded`. A missing,
inconsistently hashed or policy-incompatible cache still refuses startup; the cache never
licenses a source demoted by the current approved policy. A later valid rebuild
replaces the cache and restores the regular response contract.

The cache is trusted, operator-owned local restart evidence, written with mode
0600 and a single-writer OS lock. Its unkeyed hashes detect corruption and
inconsistent artifacts; they do not authenticate a cache against someone who
can rewrite its bytes and hashes. Protect the cache's parent directory as part
of the service configuration. POSIX writes flush the file and its renamed
directory entry before reporting success. Failure before rename preserves the
old bytes; a flush failure after rename retains the readable validated cache
with `STALE_INDEX` instead of claiming a successful durable rebuild.

HTTP/MCP tools remain read-only. Use one cache per configured service and keep
it out of public artifacts. This local Python option does not deploy or change
the Cloudflare Worker or the published canonical source data.

For an MCP client using stdio, run `python scripts/query-gateway-stdio.py`; it
reuses the same read-only JSON-RPC gateway. A reviewed located-facts bundle can
be loaded with `--located-facts-bundle`; only hash-bound
`CONFIRMED_OFFICIAL` facts enter the answer-evidence catalog. Publication rows
also need the `PRIMARY_OFFICIAL` marker on a source whose server-side catalog
role is `PRIMARY_EVENT`/`PRIMARY_REFERENCE`; an HTTPS locator or a self-claimed
role on a discovery, enrichment, or media row never makes it answer evidence.

Set `NEXT_PUBLIC_QUERY_GATEWAY_URL=http://127.0.0.1:8788/query` when starting the Web app to enable **Ask GovIntel**. The default checked-in snapshot exposes five typed operations: `search_evidence`, `get_current_brief`, `get_publication_receipt`, `get_source_health`, and `validate_answer`. A validated PublicEvent store can additionally be supplied with `--public-events` for `search_events`, `get_event`, and `compare_event_versions`; a validated typed statistics store can be supplied with `--statistics` for `query_statistics`. Without those canonical stores, the domain operations stay explicitly `CAPABILITY_NOT_AVAILABLE` and are not advertised by MCP. All operations share the same read-only Gateway, reject arbitrary URL/SQL/path arguments, apply a process-local request cap, and report stale/partial/unknown states instead of converting them to zero events.

Every query response also carries the catalog-derived `query_coverage` projection: policy version/hash, supported capability, required and covered sources, collection completeness, missing or stale sources, coverage limitations, and whether a bounded no-match statement is allowed. `search_evidence` supports strict `canonical_id` lookup in addition to bounded text/source filters; its cursor is bound to the complete filter set and generation. Optional domain-store queries additionally remain `freshness=UNKNOWN` and `domain_store_coverage_status=UNVERIFIED` until a source-health receipt is bound; a healthy index is not treated as complete coverage of the requested world. PublicEvent projections expose `trust_tier` (`VERIFIED`, `DISCOVERY_UNVERIFIED`, `CONFLICT`, or `STALE`) and the envelope counts discovery candidates separately, with `trust_tier_counts`/`conflict_count`/`stale_count` aggregated over the whole matched set.

Current-checkout integration receipt:

```bash
npm ci
npm run verify:current-checkout
```

This builds the static site, starts an ephemeral loopback server, records the actual code SHA, lockfile hash, publication generation/hash and source-policy binding, and verifies HTTP/static fallback, HTTP and STDIO MCP lifecycle/parity, browser interaction, mixed-generation rejection, and sabotage detection. It writes a machine-readable receipt under `runtime-evidence/current-checkout/`. `--mode core` omits the site/browser lane. It is a candidate-version receipt, not production deployment or public-reachability evidence.

Rights/retention defaults are compiled from [retention-rights-policy.v1.json](./docs/govintel/retention-rights-policy.v1.json). Rights remain `UNKNOWN` until reviewed; outward Query Gateway data is metadata/link-only and never a legal permission or full-text archive. `scripts/retention-policy.py --plan ... --output ...` creates a read-only expiry receipt; it never deletes canonical audit linkage.

Local-first handoff state:

```bash
python scripts/handoff-state.py watch --identity S-009:FEED-S-009-example
python scripts/handoff-state.py confirm
python scripts/handoff-state.py export --format markdown --output output/handoff.md
python scripts/handoff-state.py self-check
```

The command uses the durable `state/v2-handoff-state.json`, makes repeated watch adds idempotent, preserves confirmed handoff versions, and reopens only affected watches as `NEEDS_REVIEW` when a source version changes. The static Web page remains canonical read-only: handoff drafts and Review Inbox actions are browser-local `localStorage` overlays only, while canonical state writes still use the local CLI and never store private notes or operational police fields.

The saved [system-health.json](./apps/web/public/data/system-health.json) exposes the publication, query, and discovery lanes plus stage-level outcomes. `STALE` and `UNKNOWN` are intentional evidence states; they are not deployment or public-reachability claims.

Because GitHub Pages is a static export, `/api/health.json` does not freeze a
build-time `ok` result as current health; it returns `UNKNOWN` unless a caller
provides a request-time clock. The browser dashboard performs the current
snapshot-age check locally.

Source contract drift is fail-closed. `scripts/schema_drift.py --live` observes the three HTML candidate lists, council JSON APIs, and data.gov.tw JSON/CSV resources; `--input` remains available for deterministic replay. A missing required field, changed type, failed HTML identity, missing pagination marker, or HTTP error becomes a drift receipt and preserves the last-known-good fingerprint; additive fields are recorded as compatible. Required-field presence for the JSON and data.gov JSON transports is checked per row rather than per field union, and data.gov CSV rows must carry the header's column count, so a field that survives in only some records — or a truncated row — fails closed instead of passing as a short-but-valid window. A JSON API whose declared total or page count exceeds what was observed stays contract-healthy but reports a `PARTIAL` window with the coverage evidence, because the bounded collector only requests one page. A live or replayed run writes `state/schema-drift-state.json` atomically, so the last-known-good fingerprint, its history and resource-id changes survive to the next run — and a resource-id change is still detected after an intervening outage. The fingerprint tracks the observed schema shape rather than record counts or pagination values, so ordinary growth in volume does not by itself read as a contract change. The current receipt is reconciled into the system-health discovery lane and public Review Inbox projection — every inbox row carries that source's last-known-good so the reviewer sees the fingerprint that was replaced — and `scripts/review-inbox.py reconcile` persists local audit state. Running it without an observed input intentionally produces `UNKNOWN`, not a fabricated healthy result.

Schema replay uses the small registry in `scripts/migration_replay.py` (`1→2→3`). `dry-run` emits affected/error counts and hashes; `apply` writes only after all objects pass and saves a migration receipt; `replay` reuses `intel_v2` semantics so deterministic IDs and `FIRST_SEEN` wording remain stable. Watch/handoff inputs are copied unchanged and hash-bound. Query Store remains rebuildable from a pinned canonical generation with `python scripts/query-store.py build --feed ... --status ... --brief ...`.

Review Inbox uses `intel_v2/review.py` and `scripts/review-inbox.py` for deterministic fingerprints, deduplication, claim/decision audit, source-version reopening, and bounded homepage projection. `reconcile --input` also projects detail rechecks, discovery outcomes, PublicEvent conflict/candidate/split states, and stale/partial source rows into one local inbox without auto-promoting or auto-resolving them. Local audit state keeps reviewer/assignment details, while the public health receipt exposes only safe IDs and hashes. The Web overlay can mark an item for local follow-up, resolution, dismissal, or reopening and export that browser-local receipt; it never writes the canonical inbox. The first version is local-first and does not add RBAC or notifications.

The bounded PublicEvent core is runnable with `python scripts/public-event-fusion.py --self-check`. It only fuses explicitly official normalized documents, optionally binds caller-provided labels through the versioned entity registry (`--entity-registry`) and carries its receipt, keeps document versions separate, carries forward partial/LKG events, and requires a confirmed event plus exact geography/period/source before adding `BACKGROUND_ONLY` context. The homepage now includes a clearly marked `FIXTURE_ONLY` read-only replay card for three sources, version differences, background context, and browser-local decision previews; it is not wired to live collectors or a write-capable public UI.

The NPA source matrix is maintained in [npa-source-inventory.v1.json](./docs/govintel/npa-source-inventory.v1.json), with Batch 1 fixture adapters and explicit `PRIMARY_EVENT` / `PRIMARY_REFERENCE` / `ENRICHMENT` / `EXCLUDE_OR_AGGREGATE_ONLY` roles. Run `python scripts/npa-source-inventory.py --self-check` to verify the inventory. Candidate sources are not production or deployment evidence; personal case-level sources remain blocked from the public canonical feed. Details are in [issue-28-npa-source-matrix.md](./docs/govintel/issue-28-npa-source-matrix.md).

Correction feedback is local-first and review-gated in [issue-37-feedback-loop.md](./docs/govintel/issue-37-feedback-loop.md). The V2 Review Inbox can create browser-local, version-bound feedback drafts for an event/entity; export remains explicit and does not write canonical state. `python scripts/feedback.py self-check` covers all nine feedback reasons, dedupe, trace hashes, privacy boundaries, accepted regression links, and the rule that feedback never directly changes production truth.

The catalog-derived source policy has a cross-consumer receipt: `python scripts/verify-source-policy-integration.py --self-check` checks collector, publication, Query Store, Query Gateway, Health and Web bindings, approved candidate promotion, old replay, mixed-policy rejection, and bounded coverage gaps. It does not claim live source coverage or deployment.

Official document conversion is bounded by `intel_v2/located_facts.py` and `scripts/located-facts.py`: approved catalog origin → immutable raw/text hashes → HTML text-range or JSON Pointer locator → `FACT_CANDIDATE` / `NEEDS_REVIEW` fact and evidence projections. A locator/hash mismatch fails closed; the adapter does not promote candidates to verified truth or infer missing dates.
The live HTML/JSON replay receipt is [official-document-receipt.v1.json](./docs/govintel/official-document-receipt.v1.json); it records hashes and review status, not deployment or human approval.
The read-only Taiwan Intel Dashboard discovery consumer is replayable with `python scripts/discovery-adapter.py self-check`; media remains unverified until a server-controlled official match, and it never writes canonical events.

## Public deployment

`.github/workflows/pages.yml` uses GitHub Pages and GitHub Actions:

- a push to `main` restores the durable publication checkpoint, then builds and deploys the reviewed snapshot;
- `30 22 * * *` UTC refreshes the morning slot at 06:30 Asia/Taipei;
- `30 10 * * *` UTC refreshes the evening slot at 18:30 Asia/Taipei;
- the merged workflow persists the generated V1/V2 checkpoint to the dedicated `publication-state` branch, never directly to protected `main`, then deploys the same verified static artifact;
- manual dispatch can refresh either slot.

The `publication-state` checkpoint lifecycle is now part of `main`; deployment
still requires a successful Pages workflow and anonymous HTTPS readback.

After the repository is public, enable Pages with **Source: GitHub Actions**. The current demo and repository URLs are recorded in the [2026 judge path](./docs/govintel/competition-2026/JUDGE_PATH.md); the former submission draft is historical. A workflow file is not deployment evidence; acceptance requires an anonymous HTTPS check.

Repository: `https://github.com/Reese-max/taichung-police-intel`
Demo: `https://reese-max.github.io/taichung-police-intel` (anonymous HTTPS readback is required after every publication). Inspect the public `data/source-status.json` for the current snapshot `generated_at`, each source's `last_checked_at`, and official `data_as_of`; a healthy source can remain `STALE` when its latest official record is old, and that state must not be read as proof that no current event exists.

## Verification

```bash
# Repository shape, Kiro artifacts, workflow, demo-state contract, and secret scan
npm run check:gate0

# Kiro Spec completeness
npm run check:specs

# Web tests, Python contracts, collector self-checks, and migration self-check
npm test

# Full gate including the production static build
npm run check
```

```bash
python scripts/schema_drift.py --self-check
```

```bash
# v6 commute-road reopen replay scored against the gold cases (issue #108)
npm run replay:commute
```

Expected final lines, in this order:

```text
COMMUTE_REOPEN_SELF_CHECK_OK ...
VERIFY_OK mode=full ... secrets=0
```

`npm run replay:commute` replays the synthetic commute-road session (save a tracked
condition, close the station, reopen, deferral, explicit lift, cancel) from one
checkout and scores it with the same `scripts/evaluate-govintel.py` metrics the v1
gold harness uses. Every road name, date, source id and original text in it is
synthetic and describes no real road condition, road safety or official source.
The gold cases were written by this change's own author and reviewed only by that
same author, so they are an authored standard answer, not an independent human
label. Measured results, the separately scored `B_v6` / `C_RULES_ONLY` arms, the
zero-denominator and `NOT_RUN` rules, and what was deliberately left unexecuted are
documented in
[docs/govintel/issue-108-commute-reopen-replay.md](docs/govintel/issue-108-commute-reopen-replay.md).

The static artifact must also contain `out/index.html`, `out/api/health.json`, and `out/api/status.json`.

## Kiro workflow

- `.kiro/steering/` defines product, technology, repository structure, and evidence/safety boundaries.
- `.kiro/specs/` contains requirements, design, tasks, failure states, retry limits, and executable acceptance commands for four vertical slices.
- `.kiro/hooks/` routes save, Spec-ready, and task-finish events to the checked-in deterministic verifier.
- The verifier deliberately fails if required Kiro artifacts or acceptance contracts are missing.

The retained Kiro V3 sessions and their browser review are historical development evidence for the prior prototype, not a current runtime dependency or current competition result. Session IDs, prompts, corrections, Hook truth, model disclosure, credits, and command output are recorded in [Historical Kiro usage evidence](./docs/govintel/historical/competition-2026-08/KIRO_USAGE.md).

## Evidence and safety rules

- Official source content is authoritative; derived transcript text is navigation only.
- A collection failure is never presented as zero matching items.
- `source_health` and `window_completeness` remain separate.
- `FAILED` and `NOT_RUN` cannot overwrite last-known-good.
- Every displayed source links to an HTTPS official page or endpoint.
- Public aggregates are allowed; personal and operational police data are out of scope.
- Missing post-meeting evidence remains an explicit gap, not an AI inference.
- Answer drafts pass `apps/web/lib/answer-evidence-gate.js` before release: every factual claim needs exact official evidence (locator + document version), conflicting official sources surface as `CONFLICT` instead of a merged answer, stale sources cannot back current wording, and media-derived records never verify a claim. The shared gate emits one receipt with the publication hash, indexed evidence count, and validator version for both Web Chat and MCP; the read-only `validate_answer` route binds that receipt to the server-controlled catalog and emits no free-text fallback.
- Gate admission fails closed in all three hosts (`scripts/answer-gate-runner.mjs`, `scripts/query-gateway.py`, `workers/query-gateway/src/index.js`): a refused verdict (`gate_status: BLOCKED`) or a catalog the validator could not fully index is never released as an `answer_evidence` envelope. The gate host exits non-zero (`answer-gate-runner.mjs`) and the HTTP/MCP hosts answer `GATE_FAILED` (503). The receipt reports `indexed_evidence_count`, so evidence rows the validator refuses to index (foreign `schema_version`, missing id/source, unknown evidence type) cannot be dropped silently while the receipt still reads as a full-publication check.
- Structured `STATISTIC` propositions and trusted catalog assertions can use `value: { value: decimal string, period: string, geography: string, unit: string }` (for example, `value: "130"`). A decimal string keeps the source digits exact; JSON numeric values are rejected because parsing can round them. The gate compares all four fields within a subject; a different period, geography, or unit neither supports nor conflicts with the requested statistic. Older scalar statistic assertions remain supported for existing located facts, but do not carry these scope or exact numeric-token guarantees. Optional typed-statistics stores enter the answer catalog only when an official source, HTTPS locator, and canonical `value_text` decimal token are present; the resulting evidence is deliberately stale until a source-health binding proves currentness.

## Historical competition / prior prototype evidence

The former Kiro submission draft, deadline, demo script, checklist, video and
usage record are indexed in [Historical competition / prior prototype evidence](./docs/govintel/historical/README.md). They remain available for provenance, but
must not be read as the current GovIntel release status, current 2026
competition rules, or a completed submission. Root compatibility pointers are
kept only because the existing repository verifier expects those paths.

## Costs and third parties

| Component | License / attribution | Cost and rate-limit boundary | Setup |
|---|---|---|---|
| Next.js 16.3.1, React / React DOM 19.2.8, `pg` 8.23.0 | MIT | No API quota; no paid service required | `npm ci --prefix apps/web` |
| hls.js 1.7.0 | Apache-2.0 | Official HLS host controls media availability | Installed by the same `npm ci` command |
| Beautiful Soup 4.14.3 / jsonschema 4.26.0 | MIT | No external API quota | `python3 -m pip install -r requirements.txt` |
| requests 2.33.0 / psycopg 3.3.4 | Apache-2.0 / LGPL-3.0-only | Collectors make bounded public reads; PostgreSQL is not required by the demo | Same Python install command |
| GitHub Pages / Actions | GitHub service terms; workflow uses GitHub-maintained checkout, setup, Pages, artifact, and deploy actions | Uses the account's included allowance and GitHub plan quotas | Enable Pages with GitHub Actions |
| Kiro CLI V3 | Kiro service terms; core AI development workflow | Account-plan credits; the demo has no Kiro runtime dependency | Builder ID is needed only to reproduce the development sessions |
| OpenAI Codex, OpenHands, and gstack `browse.exe` | Entrant-directed development, repository automation, documentation, and QA assistance | Account/tool-plan dependent; none is required to run or judge the demo | Development-only tools |
| Groq `whisper-large-v3` transcript snapshot | Derived navigation text; official council media remains authoritative | No live Groq call or API key in the default demo; live rerun is provider-quota limited | Checked-in snapshot; optional rerun uses `GROQ_API_KEY` |
| FFmpeg / ffprobe | User-supplied executable; license depends on the selected build | No runtime or judge-path dependency | Needed only for the optional Groq ASR rerun |
| jsDelivr `hls.js@1` | Apache-2.0 library delivered by jsDelivr | CDN availability applies only to the legacy standalone `asr-timestamp-demo.html` | Not used by the primary Next.js demo |
| Official Taichung sources | Publisher-owned public pages, APIs, records, and HLS; linked, not claimed as project-owned | No guaranteed quota; five adapters run twice daily with one bounded retry | No credentials |

Default verification performs no paid operation and no external write. Scheduled refresh writes its generated status and durable publication checkpoint to the dedicated `publication-state` branch, then deploys the verified Pages artifact; it does not push generated data directly to protected `main`.

The five scheduled inputs are `S-004` council agendas, `S-006` questioning-order tables, `S-007` meeting records, `S-009` proposals, and `S-029` city-government council project reports. The evidence path also uses `S-010`, the official Taichung City Council page, minutes, and HLS video. Exact official URLs remain visible in the checked-in status JSON and the UI.

The retained current-workspace Kiro records show Auto as `qdev::auto`: 10.254967 credits for implementation, 1.736521 for architecture review, and 1.014514 for supporting review, totalling 13.006002 credits. Auto did not identify its routed base model, so this project does not invent one. OpenHands-authored commits remain visible in public Git history, and Codex performed entrant-directed independent QA and submission-document preparation. None of these tools is part of the deployed runtime.

## Limitations

- The competition UI demonstrates one complete council-evidence journey, not every police workflow.
- Source freshness can be stale even when the endpoint is healthy; the UI shows both states.
- Some official endpoints provide no usable publication date or only partial date-window coverage.
- The migration registry covers the JSON durable-object/replay contract, and the PostgreSQL DDL migration has passed an isolated ephemeral database gate; persistent database recheck/backfill and production execution remain unverified.
- Transcript quality is a historical baseline and has not received independent human sign-off.
- The English path translates the product journey and source names; the official Chinese transcript remains Chinese and is explicitly labelled as navigation-only evidence.
- The official `S-010` HLS CDN can fail in some Chrome sessions with `ERR_CONTENT_DECODING_FAILED`. A fatal media error or ten-second metadata timeout now preserves the transcript and provenance while showing a prominent link to the official council video. The local 2:43 product-demo MP4 is deliberately not substituted because it does not share the official evidence timeline.
- Current source freshness, scheduled run, and publication limits are time-bound in [CURRENT_STATUS.md](./docs/govintel/competition-2026/CURRENT_STATUS.md); a successful workflow does not erase `PARTIAL`, `STALE`, or `FAILED` source states.
- Human evaluation, entrant details, official eligibility, form submission and institutional adoption remain unverified; see [EVALUATION.md](./docs/govintel/competition-2026/EVALUATION.md) and [LIMITATIONS_AND_SAFETY.md](./docs/govintel/competition-2026/LIMITATIONS_AND_SAFETY.md).

## License and data rights

No repository software license has been selected. Do not assume reuse rights. Official source content remains subject to each publisher's terms; this project preserves provenance and links rather than claiming ownership.
