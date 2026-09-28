# Issue #27 — Taiwan Intel Dashboard discovery adapter

This checkout now has a bounded, read-only consumer for the producer contract
in `C:/Users/Administrator/taiwan-intel-dashboard`:

- `intel_v2/discovery_adapter.py` validates the versioned envelope and fails closed on schema, state, stale, or ordering errors.
- Media remains `DISCOVERY_UNVERIFIED`; an official candidate remains only a candidate until a server-controlled official document match is supplied.
- Material changes trigger bounded verification; summary/risk/entity/topic-only changes do not.
- Same-source direct/Dashboard paths use `original_source_identity` and do not increase independent-source count.
- Existing PublicEvent links are reused; new official matches are emitted as `PENDING_PUBLICATION` inputs. The adapter never writes canonical events.
- `summarize_canary()` preserves the complete 14-day denominator, including no-match, conflict, and publication-failure outcomes.

Replay:

```powershell
python -X utf8 scripts/discovery-adapter.py self-check
python -X utf8 -m unittest discover -s tests -p test_discovery_adapter.py -v
```

Evidence boundary: this is `IMPLEMENTED` and `CORE_TESTED` against the checked-in
producer contract fixture. It is not a live upstream canary, does not grant the
Dashboard write access, and does not prove GovIntel Pages publication.
