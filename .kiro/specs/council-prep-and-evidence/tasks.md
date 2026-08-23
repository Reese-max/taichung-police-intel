# Council preparation and evidence tasks

- [ ] Define the council-preparation response and evidence-type contract.
  - Acceptance: invalid or missing evidence types and locators are rejected.
- [ ] Add a fixed end-to-end council fixture from agenda to historical oral answer and project report.
  - Acceptance: every displayed fact has an evidence ID and official locator.
- [ ] Connect the existing evidence drawer to real claim/evidence records.
  - Acceptance: selecting a segment or word yields the expected absolute official-media timestamp.
- [ ] Add producer/verifier separation and `GAP-001` policy enforcement.
  - Acceptance: attempts to infer post-meeting progress become `CLAIM_REJECTED` or `UNVERIFIED_AFTER_MEETING`.
- [ ] Implement degraded media/ASR/verifier states without losing official links.
  - Acceptance: disputed derived text is hidden while authoritative evidence remains reachable.
- [ ] Add `apps/web/tests/council-prep.test.mjs` and run the Spec acceptance command.
  - Acceptance: both commands in `design.md` exit 0.
