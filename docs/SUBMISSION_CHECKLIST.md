# Competition Submission Checklist

Official deadline: **2026-08-23 23:59 UTC / 2026-08-24 07:59 Asia/Taipei**.

## Completed locally

- [x] A local Git repository exists and the application source is present.
- [x] Root `.kiro/` contains Steering, Specs, and Hooks.
- [x] English README contains the setup, use, testing, cost, third-party, and limitation baseline.
- [x] Static production build succeeds locally.
- [x] GitHub Pages workflow includes two daily refresh schedules.
- [x] Public demo requires no login, API key, database, payment, or other test credentials for anonymous judge access.
- [x] Three-minute English demo script is ready.
- [x] Submission draft is ready.
- [x] Kiro CLI is authenticated with Builder ID.
- [x] One meaningful Kiro V3 Spec session reviewed all 16 control artifacts.
- [x] The real session ID, prompts, reviewed artifacts, human corrections, and `VERIFY_OK` output are saved in `docs/KIRO_USAGE.md`.
- [x] Kiro implemented the bounded English homepage path and executable acceptance test in a retained V3 Spec session.
- [x] Kiro model selection and retained-session credits are disclosed without inventing Auto's undisclosed routed base model.
- [x] The complete English primary journey, source translations, limitations, drawer controls, and responsive layouts passed local browser QA.

## Must complete before submission

- [ ] Entrant confirms every member is an eligible adult and the project was created during the 2026-08-08 00:00 UTC to 2026-08-23 23:59 UTC competition period without prohibited outside support.
- [x] Preserve honest competition-period evidence through public Git history and Kiro session records; `docs/KIRO_USAGE.md` separates Kiro work, entrant-directed corrections, unobserved Hooks, and later deployment evidence.
- [x] Complete the full English primary product journey with translated UI, source names, and controls. Official Chinese transcript remains Chinese and is explicitly labeled as navigation-only derived text, not authoritative evidence.
- [x] Use Kiro to implement and verify one bounded product task; retain changed files, session ID, human corrections, Hook-activity truth, and final acceptance output.
- [x] Capture the Kiro session ID and final `VERIFY_OK` line in the demo video.
- [x] Set the repository owner/name and replace the repository and demo URL placeholders.
- [ ] Replace `PENDING_ENTRANT_NAME` and `PENDING_CONTRIBUTION_STATEMENT` after the entrant provides identity/contribution details.
- [x] Make the repository public. (Verified: `private: false` via GitHub API, 2026-08-23.)
- [x] Enable GitHub Pages with **Source: GitHub Actions**. (Verified: `build_type: workflow` via GitHub API, 2026-08-23.)
- [x] Confirm the Pages workflow and both static JSON endpoints return HTTP 200. (Verified: run 32631305048 conclusion=success; `/`, `/api/health.json`, `/api/status.json` each returned HTTP 200 anonymously, 2026-08-23.)
- [x] Confirm anonymous access in a signed-out browser. (Verified: Kiro-owned anonymous curl and browse.exe headless checks, 2026-08-23; no login prompt, no console errors, no 4xx/5xx assets.)
- [x] Observe one natural post-deployment schedule. (Verified: run 32634682889 conclusion=success; `CR-DEMO-20260823-EVENING-SCHEDULE`, `trigger=SCHEDULE`, 2026-08-23.)
- [ ] Observe a completed post-deployment MORNING plus EVENING pair; the next MORNING run is scheduled for 2026-08-24 06:30 Asia/Taipei.
- [x] Record a demo no longer than three minutes in English or with complete English captions. (Verified locally: H.264, 1280×720, 2:43, full decode succeeds.)
- [x] Upload the video as public or unlisted and verify anonymous playback. (Verified: run 32650584348 succeeded; HTTP 200 `video/mp4`; remote SHA-256 matches; anonymous Chrome reports 163 seconds, 1280×720, `readyState=4`, 2026-08-24.)
- [ ] Add entrant/team and contribution details.
- [x] List direct third-party APIs, datasets, libraries, assets, AI/development tools, costs, rate limits, setup requirements, attribution, and usage-right boundaries in `README.md` and `SUBMISSION.md`.
- [ ] Complete the official submission form and save the receipt.
- [ ] After the deadline, freeze source, documentation, README, video, links, credentials, application, and team membership through the judging period unless the organizers explicitly authorize a correction.

## Official form field map

- Representative name and Discord username.
- Entry type: solo, team of two, or team of three.
- Team name, member names, Discord usernames, email addresses, roles, and contributions when applicable.
- Eligibility, competition-period originality, support, and rules confirmations.
- Project description, problem, key features, target users, and project type.
- Public repository, working demo or test build, and demo video URLs.
- Setup/testing notes and safe test credentials, if needed.
- Meaningful Kiro usage and confirmations for root `.kiro`, README, and video evidence.
- Rights, secrets/malware, English/translation, third-party resources, paid services, rate limits, and setup confirmations.

## Final acceptance commands

```bash
npm run check
curl -f https://reese-max.github.io/taichung-police-intel/api/health.json
curl -f https://reese-max.github.io/taichung-police-intel/api/status.json
```

Verify manually:

- homepage and English judge guide load;
- all five source cards are present;
- official links open;
- evidence drawer opens;
- transcript search and timestamp seeking work;
- mobile and desktop layouts have no blocking overflow;
- repository, demo, and video remain public through judging.
