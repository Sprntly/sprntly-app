"""On-Call agent — incident triage + investigation (proposals only).

The agent NEVER acts autonomously (PRD invariant). It investigates a live
incident against the knowledge graph and proposes PM-gated actions; every
proposed action carries requires_pm_approval=true. No execution layer.
"""
