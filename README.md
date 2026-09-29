# GovIntel AI｜跨機關公共事件整合、異動辨識與交班支援平台

**承辦任務：**從公開的跨機關公告找出同一事件的版本與時間異動，核對官方原文，再判斷哪份交班稿需要重核。現行公開服務仍以五個臺中議會／市政來源的備詢簡報與證據導覽為主；新增事件融合與交班流程尚未成為正式發布能力。

**2026 評審入口：**[3–5 分鐘可重播路徑、來源矩陣、版本差異與評測邊界](./docs/govintel/competition-2026/JUDGE_PATH.md)。先看下表，再選[公開網站](https://reese-max.github.io/taichung-police-intel/)或固定版本的本地 fixture；公開頁面不能代替新能力的正式部署證明。

| 狀態（2026-09-29 查核） | 能力與證據 |
|---|---|
| `PRODUCTION_ACTIVE` | [source catalog](./docs/govintel/source-catalog.v2.json) 與 [source policy](./apps/web/public/data/source-policy.json) 指定 S-004／006／007／009／029 為正式五來源。匿名[公開 source status](https://reese-max.github.io/taichung-police-intel/data/source-status.json) 於本次查核為 HTTP 200，但 `generated_at=2026-09-24T23:18:12+08:00`；這是已發布舊資料，不是今日蒐集成功。 |
| `IMPLEMENTED_NOT_PRODUCTION` | [Query Gateway](./docs/govintel/issue-30-runtime-boundaries.md)、[官方文件 locator](./docs/govintel/issue-48-official-document-replay.md)、[PublicEvent fixture](./docs/govintel/issue-24-public-event-fusion.md)、[本地交班版本](./docs/govintel/issue-23-handoff-flow.md) 有程式與重播測試；正式多來源事件／交班上線及真人驗收仍缺。 |
| `CANDIDATE_CANARY` | S-001／019／031／032／033 有[限量觀測紀錄](./docs/govintel/issue-22-publication-wiring.md)；[PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16) 仍開啟，來源未升格。 |
| `DESIGN_ONLY` | [Twinkle＋官方直連策略](./docs/govintel/TWINKLE_HYBRID_SOURCES.md)與[最新資訊複查規劃](./docs/govintel/issue-21-detail-recheck.md)不等於 live client、完整正文覆蓋或正式服務。 |
| `BLOCKED` | [#20 排程發布驗收](https://github.com/Reese-max/taichung-police-intel/issues/20)仍開啟。受保護 main 的直推缺陷已於 [PR #26](https://github.com/Reese-max/taichung-police-intel/pull/26) 合併修復；但本版最新[自然排程 36511458406](https://github.com/Reese-max/taichung-police-intel/actions/runs/36511458406)驗證失敗、跳過部署。仍缺成功 EVENING、新晨晚收據及 artifact-upload 失敗演練。 |

**版本／資料時間：**本表核對 `main@bd9adc647a2a91b54487d96869bd2d56eacea26e`；該 commit 的[主幹 verify](https://github.com/Reese-max/taichung-police-intel/actions/runs/36487310563)成功且執行 22 步，與失敗的自然排程是不同驗證。倉庫內的 [source-status snapshot](./apps/web/public/data/source-status.json) 是 `2026-09-11T08:23:26+08:00`，與公開服務所回傳的 9 月 24 日版本不同。後續發佈可能改變公開資料；評審應重新讀取其 `generated_at`、來源健康、缺口及 [#20](https://github.com/Reese-max/taichung-police-intel/issues/20) 的最新執行結果。真人績效、正式報名與機關採用均未驗證。

舊名 **Taichung Police Public Intelligence** 的 2026 年 8 月 Kiro 參賽作品、影片、使用紀錄與當時截止日期保留在下方的「Historical Kiro competition package」；它們是歷史佐證，並非本次 GovIntel 參賽規則或新功能成果。

## Problem and users

Police policy and council-liaison staff must monitor scattered official pages, proposals, reports, meeting records, and videos. Finding what changed can take one to two hours, and a summary without a source locator is difficult to trust under questioning.

This competition version focuses on one real task: preparing for a council question. It uses public information only and excludes internal duty data, emergency dispatch, 110 calls, case-level criminal data, personal data, and operational command functions.

## What works

- A focused council-preparation brief for a police policy user.
- Five live official-source adapters with isolated failure handling.
- Source health kept separate from date-window completeness.
- Intelligence-gap reasons instead of silently turning collection failure into zero results.
- Last-known-good retained when a later source fetch fails.
- Official URLs, collection time, data-as-of time, raw snapshot count, and SHA-256 manifest.
- An evidence drawer with official HLS playback, searchable transcript segments, and word-level timestamp navigation.
- A Traditional Chinese / English toggle covering the primary journey while preserving the official Chinese transcript as labelled navigation text.
- A static-export deployment path that needs no paid database or application server.

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
- Kiro CLI V3 only for reproducing the Kiro workflow

Install dependencies:

```bash
npm ci --prefix apps/web
python -m pip install -r requirements.txt
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

For an MCP client using stdio, run `python scripts/query-gateway-stdio.py`; it
reuses the same read-only JSON-RPC gateway. A reviewed located-facts bundle can
be loaded with `--located-facts-bundle`; only hash-bound
`CONFIRMED_OFFICIAL` facts enter the answer-evidence catalog.

Set `NEXT_PUBLIC_QUERY_GATEWAY_URL=http://127.0.0.1:8788/query` when starting the Web app to enable **Ask GovIntel**. The default checked-in snapshot exposes five typed operations: `search_evidence`, `get_current_brief`, `get_publication_receipt`, `get_source_health`, and `validate_answer`. A validated PublicEvent store can additionally be supplied with `--public-events` for `search_events`, `get_event`, and `compare_event_versions`; a validated typed statistics store can be supplied with `--statistics` for `query_statistics`. Without those canonical stores, the domain operations stay explicitly `CAPABILITY_NOT_AVAILABLE` and are not advertised by MCP. All operations share the same read-only Gateway, reject arbitrary URL/SQL/path arguments, apply a process-local request cap, and report stale/partial/unknown states instead of converting them to zero events.

Every query response also carries the catalog-derived `query_coverage` projection: policy version/hash, supported capability, required and covered sources, collection completeness, missing or stale sources, coverage limitations, and whether a bounded no-match statement is allowed. Optional domain-store queries additionally remain `freshness=UNKNOWN` and `domain_store_coverage_status=UNVERIFIED` until a source-health receipt is bound; a healthy index is not treated as complete coverage of the requested world.

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

Source contract drift is fail-closed. `scripts/schema_drift.py --live` observes the three HTML candidate lists, council JSON APIs, and data.gov.tw JSON/CSV resources; `--input` remains available for deterministic replay. A missing required field, changed type, failed HTML identity, missing pagination marker, or HTTP error becomes a drift receipt and preserves the last-known-good fingerprint; additive fields are recorded as compatible. The current receipt is reconciled into the system-health discovery lane and public Review Inbox projection; `scripts/review-inbox.py reconcile` persists local audit state. Running it without an observed input intentionally produces `UNKNOWN`, not a fabricated healthy result.

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
- the merged workflow persists the generated V1/V2 checkpoint to the dedicated `publication-state` branch, never directly to protected `main`, then deploys the same verified static artifact when all gates pass;
- manual dispatch can refresh either slot.

The `publication-state` checkpoint lifecycle is now part of `main`; deployment
still requires a successful Pages workflow and anonymous HTTPS readback.

Pages is public with **Source: GitHub Actions**. The historical submission draft is in [SUBMISSION.md](./SUBMISSION.md). A workflow file or successful push-triggered deployment is not a fresh scheduled-publication receipt; acceptance requires anonymous HTTPS version and hash checks.

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

Expected final line:

```text
VERIFY_OK mode=full ... secrets=0
```

The static artifact must also contain `out/index.html`, `out/api/health.json`, and `out/api/status.json`.

## Kiro workflow

- `.kiro/steering/` defines product, technology, repository structure, and evidence/safety boundaries.
- `.kiro/specs/` contains requirements, design, tasks, failure states, retry limits, and executable acceptance commands for three vertical slices.
- `.kiro/hooks/` routes save, Spec-ready, and task-finish events to the checked-in deterministic verifier.
- The verifier deliberately fails if required Kiro artifacts or acceptance contracts are missing.

Authenticated Kiro V3 sessions first reviewed all 16 Steering, Spec, and Hook artifacts, then implemented the English primary path, the two-card cap, and the homepage acceptance test. Entrant-directed browser review rejected the first static-guide-only attempt, found and fixed a drawer initialization defect in the accepted implementation, and reran the full gate. Session IDs, prompts, corrections, Hook truth, model disclosure, credits, and command output are recorded in [docs/KIRO_USAGE.md](./docs/KIRO_USAGE.md).

## Evidence and safety rules

- Official source content is authoritative; derived transcript text is navigation only.
- A collection failure is never presented as zero matching items.
- `source_health` and `window_completeness` remain separate.
- `FAILED` and `NOT_RUN` cannot overwrite last-known-good.
- Every displayed source links to an HTTPS official page or endpoint.
- Public aggregates are allowed; personal and operational police data are out of scope.
- Missing post-meeting evidence remains an explicit gap, not an AI inference.
- Answer drafts pass `apps/web/lib/answer-evidence-gate.js` before release: every factual claim needs exact official evidence (locator + document version), conflicting official sources surface as `CONFLICT` instead of a merged answer, stale sources cannot back current wording, and media-derived records never verify a claim. The shared gate emits one receipt with the publication hash and validator version for both Web Chat and MCP; the read-only `validate_answer` route binds that receipt to the server-controlled catalog and emits no free-text fallback.
- Structured `STATISTIC` propositions and trusted catalog assertions can use `value: { value: decimal string, period: string, geography: string, unit: string }` (for example, `value: "130"`). A decimal string keeps the source digits exact; JSON numeric values are rejected because parsing can round them. The gate compares all four fields within a subject; a different period, geography, or unit neither supports nor conflicts with the requested statistic. Older scalar statistic assertions remain supported for existing located facts, but do not carry these scope or exact numeric-token guarantees. The typed statistics query store is not yet promoted into the trusted answer catalog; its original decimal tokens must be preserved before future promotion.

## Historical Kiro competition package (2026-08)

The following files describe the earlier Kiro competition submission path. They
are preserved for provenance and are not the current GovIntel release status.

- [Submission draft](./SUBMISSION.md)
- [Three-minute demo script](./docs/DEMO_SCRIPT.md)
- [Submission checklist](./docs/SUBMISSION_CHECKLIST.md)
- [Kiro usage evidence](./docs/KIRO_USAGE.md)
- [Official submission form](https://forms.gle/xBLjk9nKMqbi2zie9)

Official competition deadline: **2026-08-23 23:59 UTC**, which is **2026-08-24 07:59 Asia/Taipei**.

## Costs and third parties

| Component | License / attribution | Cost and rate-limit boundary | Setup |
|---|---|---|---|
| Next.js 16.3.1, React / React DOM 19.2.8, `pg` 8.23.0 | MIT | No API quota; no paid service required | `npm ci --prefix apps/web` |
| hls.js 1.7.0 | Apache-2.0 | Official HLS host controls media availability | Installed by the same `npm ci` command |
| Beautiful Soup 4.14.3 / jsonschema 4.26.0 | MIT | No external API quota | `python -m pip install -r requirements.txt` |
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
- The five source adapters passed local canaries and one GitHub-hosted scheduled EVENING run succeeded on 2026-08-23. A completed post-deployment MORNING plus EVENING pair has not yet been observed.
- The public repository, demo, and captioned video have historical anonymous verification receipts; current deployment/version/hash status remains unverified in this checkout. Entrant details and form submission remain pending.

## License and data rights

No repository software license has been selected. Do not assume reuse rights. Official source content remains subject to each publisher's terms; this project preserves provenance and links rather than claiming ownership.
