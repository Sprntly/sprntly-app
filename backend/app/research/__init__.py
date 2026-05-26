"""Research domain — competitor profiles + multi-source monitoring.

Two layers live here:

  - profile.py / profile_service.py — the persistent record of a
    competitor (name + URLs) and the time-series of signals observed
    against it.
  - monitors/ — per-source pollers that turn external feeds (App Store
    RSS, changelog HTML, …) into CompetitorSignal rows.

The weekly digest job (see app/research/digest.py on the sibling
feat/research-competitive-digest branch) reads from this state but
does not own it.
"""
