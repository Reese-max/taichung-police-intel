---
inclusion: always
---

# Structure

```text
.kiro/                  Kiro steering, Specs, and Hooks
apps/web/               Next.js evidence-drawer application
evaluation/             Deterministic value contract, schema, and tests
scripts/                Repository-level verification commands
*.py                    Public-source audits, canaries, and ASR utilities
source-catalog.md       Canonical source endpoints, roles, evidence, and gaps
hackathon-master-roadmap-v4.md
                        Canonical delivery scope and dependency order
```

## Rules

- Treat `hackathon-master-roadmap-v4.md` as the active execution roadmap; V3 is history.
- Treat `source-catalog.md` as the source-of-truth for endpoints and source limitations.
- Keep generated/dependency folders out of Git: `node_modules`, `.next`, caches, Graphify output, and `.gstack`.
- Put reusable repository verification in `scripts/verify-project.mjs`; Hooks call that file instead of duplicating shell logic.
- Keep the current evidence drawer in `apps/web`; do not rewrite it while building the missing ingestion and API layers.
- Use lowercase kebab-case for Spec and Hook directories/files.
- Keep fixtures distinct from live output and label their capture date and source hash.
