"""URL slugifier for the public share URL's human-readable path segments.

Produces the cosmetic `/p/<company>/<feature>/<token>` segments from a company
display name / PRD title at serve time. Kept SEPARATE from `app.ingest.slugify`
(underscore output, a committed on-disk markdown-filename convention with its own
callers/tests) — this one uses a dash separator for URL aesthetics.
"""
from __future__ import annotations

import re

_URL_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def url_slugify(name: str, *, fallback: str = "item", max_length: int = 40) -> str:
    """Lowercase; collapse runs of non ``[a-z0-9-]`` chars to a single ``-``;
    strip leading/trailing ``-``; cap length (re-stripping any trailing ``-``
    left by the cut). Empty/whitespace-only/None input -> ``fallback``.
    """
    s = _URL_SLUG_RE.sub("-", (name or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        return fallback
    s = s[:max_length].strip("-")
    return s or fallback
