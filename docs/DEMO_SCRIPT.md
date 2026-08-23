# Three-Minute Demo Script

Target length: **2:40**, leaving a 20-second safety margin. Record in English or add complete English captions.

Before recording, prepare four views: the live demo in English, `.kiro/specs/five-minute-homepage/tasks.md`, the model-and-credit table in `docs/KIRO_USAGE.md`, and a terminal showing the final `VERIFY_OK` line. Keep the public GitHub Actions run ready for the close.

## 0:00-0:15 — Problem

"Police policy staff can spend one to two hours scanning scattered public council and government sources. A summary is not enough when a supervisor asks: where did this come from?"

Show the public homepage title and switch to English.

## 0:15-0:40 — Five-minute brief

"This is a focused council-preparation view. It identifies one priority issue and the concrete questions an officer should prepare for. The competition scope is deliberately one complete task, not a generic world map."

Show the priority brief and preparation bullets.

## 0:40-1:10 — Source trust

"Each official source reports endpoint health separately from date-window completeness. We show freshness, explicit intelligence gaps, the next update, and last-known-good. A failed fetch never becomes a misleading zero-result report."

Scroll through the five source cards. Point to an official link, a gap, and an LKG run ID.

## 1:10-1:50 — Traceable evidence

Open the evidence drawer.

"The transcript is navigation, not authority. I search for a police term, select a segment or individual word, and the player seeks to the exact timestamp in the official Taichung City Council video."

Search for `警察局`, click one segment, then click one word. Show the official video link and CER gate.

## 1:50-2:25 — Kiro

Show `.kiro/steering`, one Spec, one Hook, and the authenticated session evidence.

"Kiro V3 shaped the product boundary and implemented this English path from a checked-in Spec. The first static-guide-only pass failed entrant review. The resumed session added the real language toggle, shared evidence contract, two-card limiter, and executable tests. All three retained sessions selected Auto; local records expose qdev auto and 13.006002 credits, but not an underlying routed model. The Hooks are checked in, but no Hook firing is claimed."

Show implementation session `sess_3168ba8f-9bca-4967-9eb7-5640b6a58f31`, the checked homepage tasks, the model-and-credit table, and `VERIFY_OK mode=full required=39 specs=3 secrets=0`.

## 2:25-2:40 — Reproducibility and close

Show the public repository Actions run and the live demo.

"The public demo needs no account or paid service. GitHub Actions refreshes at 06:30 and 18:30 Taipei time, and the first scheduled evening run succeeded. The result is five-minute preparation with evidence, gaps, and provenance visible by default."
