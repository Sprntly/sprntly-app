"""Crucible — the engine behind the user-facing "Goal Analysis" feature.

Users never see the word Crucible (`backend/docs/crucible/README.md`). It is the
internal name for the pipeline that takes a business goal, reads the analyses a
company already has, reconciles their contradictions, and returns a ranked set of
recommendations with sizes, confidence, and what was ruled out.

Build plan: `backend/docs/GOAL_ANALYSIS.md`. Specification:
`backend/docs/crucible/CRUCIBLE-SPEC.md`.

THIS PACKAGE IS NOT WIRED TO ANYTHING YET (PR1 of the Phase 1 sequence). It holds
the data model, the causal lint, and the executable form of the ten invariants —
deliberately built before any pipeline stage, because the invariants are
cross-cutting: stages built against each other before the contract exists violate
them at the seams, and they do it invisibly, since every stage's own tests pass.
"""
