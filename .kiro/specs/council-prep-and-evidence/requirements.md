# Council preparation and evidence requirements

## Goal

Connect an upcoming council agenda to historical questions, oral answers, proposals, project reports, and official evidence without inventing post-meeting implementation status.

## Requirements

### R1 — Preparation chain

WHEN an upcoming police-related agenda or questioning order is selected THE SYSTEM SHALL connect it to at least one relevant historical question and oral answer when official evidence exists.

### R2 — Evidence types

WHEN evidence is displayed THE SYSTEM SHALL distinguish `ORAL_OFFICIAL`, `WRITTEN_OFFICIAL`, `RESOLUTION`, `GROQ_ASR`, and AI synthesis.

### R3 — Media navigation

WHEN a reviewer selects an ASR segment or word THE SYSTEM SHALL seek the official media to the corresponding absolute timestamp.

### R4 — Unknown follow-up

WHEN no systematic official post-meeting response or implementation source exists THE SYSTEM SHALL display `GAP-001` and `UNVERIFIED_AFTER_MEETING` without inferring completion or non-completion.

### R5 — Validation

WHEN a council-preparation claim is generated THE SYSTEM SHALL bind it to evidence IDs and require a separate verifier run to check the original official material.

### R6 — Degradation

WHEN media, ASR, or verifier access fails THE SYSTEM SHALL retain official links and verified text while suppressing disputed derived claims.
