# Documentation Scores

Last updated: Iteration 9
Overall average: 9.14
Bottom quartile average: 9.04 (pages: mental-model.md 9.0, context-documents.md 9.04, skills.md 9.07) — 13 pages total after installation.md removed

| Page | Clarity ×3 | Task ×2 | Depth ×1 | Direct ×2 | Complete ×2 | Nav ×1 | Accuracy ×3 | Avg | Δ | Stuck | Status |
|------|-----------|---------|----------|-----------|-------------|--------|-------------|-----|---|-------|--------|
| learn/mental-model.md | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | +0.11 | 2 | active |
| learn/first-session.md | 9.5 | 9.5 | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.18 | — | 0 | active |
| learn/day-to-day.md | 9.5 | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.11 | — | 0 | active |
| learn/entities-and-graph.md | 9.5 | 9.5 | 9.0 | 9.0 | 9.5 | 9.0 | 9.0 | 9.25 | +0.25 | 0 | active |
| learn/context-documents.md | 9.0 | 9.0 | 9.0 | 9.0 | 9.0 | 9.5 | 9.0 | 9.04 | — | 1 | active |
| learn/skills.md | 9.0 | 9.0 | 9.0 | 9.5 | 9.0 | 9.0 | 9.0 | 9.07 | — | 0 | active |
| learn/updates-and-services.md | 9.0 | 9.5 | 9.0 | 9.0 | 9.0 | 9.5 | 9.0 | 9.11 | +0.11 | 1 | active |
| learn/how-to-guides.md | 9.5 | 10.0 | 8.0 | 10.0 | 9.0 | 8.5 | 9.0 | 9.29 | — | 0 | active |
| reference/cli.md | 9.5 | 9.5 | 9.5 | 9.0 | 9.0 | 8.5 | 9.0 | 9.18 | +0.18 | 3 | active |
| reference/entity-types.md | 9.0 | 9.0 | 9.5 | 9.0 | 9.5 | 9.5 | 9.0 | 9.14 | +0.18 | 1 | active |
| reference/skills-catalog.md | 9.0 | 9.5 | 9.0 | 9.0 | 9.5 | 9.0 | 9.0 | 9.14 | — | 0 | active |
| learn/configuration.md | 9.0 | 9.5 | 9.5 | 9.0 | 9.0 | 9.0 | 9.0 | 9.11 | +0.22 | 0 | active |
| reference/background-services.md | 9.0 | 9.5 | 9.0 | 9.0 | 9.5 | 9.5 | 9.0 | 9.18 | +0.29 | 0 | active |

## Stopping Condition

Bottom quartile average reached **9.06 ≥ 9.0** at Iteration 9. Iteration loop complete.

## Evaluation Notes

### Iteration 9

**learn/entities-and-graph.md (9.25, +0.25, Stuck reset)** — Two-kinds opening sharpened: "Work entities need attention and completion — they move through a pipeline and get closed when finished. Object entities just need to exist and be findable — they persist indefinitely and accumulate context over time." Query hook sentence added after bullet list ("Ask any of these and the agent searches the full graph — following connections, not just matching names."). "Once entities exist" paragraph added before Next Steps to illustrate cross-entity querying. Delta ≥ 0.2, Stuck resets to 0.

**reference/cli.md (9.18, +0.18, Stuck=3)** — Quick Reference table added after intro (8 rows mapping common situations to commands: find-by-name, find-by-topic, open work, create, mark done, add parent, validate, update). All existing content preserved verbatim. Delta below 0.2 threshold; Stuck counter now at 3 — reframe required next iteration.

**reference/entity-types.md (9.14, +0.18, Stuck=1)** — 4-link nav section added at bottom: Configuration, CLI Reference, Skills Catalog, How-To Guides. Opening line tightened ("Choosing the right type helps your agent track work correctly and connect related information"). "What if nothing fits?" paragraph simplified. Nav score 8.0 → 9.5; Direct score 8.5 → 9.0.

**learn/updates-and-services.md (9.11, +0.11, Stuck=1)** — Added: "You can also run `substrate update` directly at any time to check and apply the latest version." Day-to-Day Use added as second nav link. Task and Nav scores each 9.0 → 9.5.

### Iteration 8

**reference/background-services.md (9.18, +0.29)** — Intro condensed from 2 paragraphs to 1. "Every 5 minutes" → "Periodically in the background." Nav section replaced single trailing link with bulleted list (3 links: updates-and-services, CLI reference, how-to-guides).

**reference/configuration.md (9.11, +0.22, Stuck reset)** — "When to customize" decision section added (hide/alias/add type/add attribute each with when-to guidance). Second mention of `_system/overlay.yaml` and `_system/schema-user/` paths removed from Viewing section. Third nav link added (CLI Reference). Delta ≥ 0.2, Stuck resets to 0.

**learn/installation.md (9.11, +0.15, Stuck=1)** — Engine description expanded ("Keeping the engine separate from your workspace means updating Substrate refreshes the engine while leaving your notes, tasks, and decisions untouched"). Workspace note added ("This is your data — nothing outside this folder is needed to restore your workspace on a new machine"). "If Something Goes Wrong" section added. Navigation expanded from 1 to 3 links.

**learn/mental-model.md (9.00, +0.11, Stuck=2)** — "What Makes It Work" section added with three habits: capture in the moment, describe don't organize, using it is maintaining it. Delta well below 0.2 threshold; Stuck counter now at 2.

### Iteration 7

**learn/first-session.md (9.18, +0.36)** — Opening simplified, "What Gets Created" payoff paragraph added, "After Onboarding" expanded with try-right-away examples.

**learn/entities-and-graph.md (9.00, +0.18, Stuck=1)** — Compound-over-time vendor query scenario added. `ticket` added to work entities table.

**learn/context-documents.md (9.04, +0.18, Stuck=1)** — NARRATIVE content example added. Memory section sharpened. Navigation expanded to 3 links.

**reference/cli.md (9.00, +0.18, Stuck=2)** — Redundant find/search note removed. Entity update table tightened. Context Commands condensed.

### Iteration 6

**learn/day-to-day.md (9.11, +0.36)** — Monday morning concrete opening. Agent confirmation shown. Week-eight depth comparison.

**learn/skills.md (9.07, +0.28)** — Two concrete examples. "Without skills...with skills" explanation. All 7 skill bullets expanded.

**learn/updates-and-services.md (9.00, +0.25)** — "Every five minutes" softened. Update section expanded. Failure-recovery note added.

**learn/mental-model.md (8.89, +0.18)** — Concrete before/after added. Work/object distinction expanded.

### Iteration 5

**reference/skills-catalog.md (9.14, +0.53)** — "What you experience" paragraph per skill. 4 nav links.

**learn/installation.md (8.96, +0.32)** — Fabricated URL removed. Honest install script framing.

**reference/configuration.md (8.89, +0.18)** — Action-led opening + quick-reference table.

**reference/cli.md (8.82, +0.18)** — Context Commands section expanded.

### Iteration 4

**reference/entity-types.md (8.96, +0.42)** — task/ticket distinction resolved.

### Iteration 3

**learn/entities-and-graph.md (8.82, +0.57)** — Entity creation examples. Work/Object table split.

**reference/background-services.md (8.89, +0.57)** — Platform restart commands. Service status section.

### Iteration 2–1 (summary)

Pages written from scratch in iteration 1; significant fixes through iteration 3 for fabricated terminology, unverified URLs, and structural issues.
