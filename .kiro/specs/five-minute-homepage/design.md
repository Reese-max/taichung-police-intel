# Five-minute homepage design

## Flow

```text
AUTO_PASS claims
  -> deterministic eligibility and reason codes
  -> Generator: concise evidence-bound wording
  -> Verifier: sentence and locator checks
  -> maximum-ten homepage response
  -> evidence drawer
```

## Deterministic checks

- Enforce the ten-item limit, two central-policy item limit, accepted statuses, coverage window, required reason codes, evidence count, and working locators.
- Reject `SEARCH_ONLY`, suppressed, pending, disputed, or quarantined items from the formal homepage.

## Generator

The generator may shorten verified claims but may not add facts, dates, causal explanations, or local impact absent from bound evidence.

## Verifier

The verifier checks each sentence against original evidence and confirms that stage, local-impact label, next milestone, and reason code are not conflated.

## Failure states

Invalid items are not published. Summary failures fall back to verified source text; locator failures become `CLAIM_REJECTED`; model disagreement becomes `AI_DISAGREEMENT` or `QUARANTINED`.

## Retry limit

One summary/verification retry is allowed. A second disagreement removes the generated wording and does not weaken deterministic gates.

## Acceptance command

```bash
node --test apps/web/tests/homepage.test.mjs
npm run check
```

The homepage test is created with the implementation and must prove limits, fallback behavior, and evidence navigation.
