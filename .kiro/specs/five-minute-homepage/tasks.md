# Five-minute homepage tasks

- [x] Define the typed homepage response and deterministic eligibility function.
  - Acceptance: tests reject non-`AUTO_PASS` items and lists over ten items.
- [x] Build a fixed fixture with recurring, council-attention, cross-source, and near-milestone reason codes.
  - Acceptance: ordering is identical across repeated runs.
- [x] Connect evidence-bound wording generation and independent sentence verification.
  - Acceptance: unsupported text is rejected and verified source text remains available.
- [x] Implement the English primary path and at most two central-policy cards.
  - Acceptance: the same evidence IDs and official links appear in both languages.
- [x] Add `apps/web/tests/homepage.test.mjs` and run the Spec acceptance command.
  - Acceptance: both commands in `design.md` exit 0.
