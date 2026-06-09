"""Onboarding flows — turn the thin inputs a new company gives at sign-up into
the structured org context downstream agents read through.

`website_analysis` infers industry / business-type / a readable context brief /
suggested success metrics from a product website, to pre-fill the onboarding
form (the human can always edit). It NEVER hard-fails: an unreachable / blocked
/ empty site degrades to a graceful `ok: false` result so onboarding falls back
to manual entry.
"""
