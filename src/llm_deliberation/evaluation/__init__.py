"""Evaluation harness (Part B of the next-phase task): controlled pipeline
variants (SINGLE/DUAL/CRITIQUE/FULL), evaluation-only persistence, blind
review export, and descriptive reporting.

Never used by the production deliberation flow. Never changes the
production default (see llm_deliberation.service/orchestrator, which this
package only reads from/reuses, never modifies). No live provider call
happens anywhere in this package's *import* or construction -- only
`variants.run_variant()` makes real calls, and only when the caller
actually awaits it with real Settings/providers.
"""
