"""RawRecord — the generic envelope every connector puller emits (§1b).

The ONLY connector-specific code is the thin puller that fetches raw data and
wraps it in this envelope; everything downstream (extraction, resolution, KG
writes) is generic. Adding a connector = a new puller, no schema change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: Shared DEFENSIVE ceiling on the in-memory extraction text one call-shaped
#: provider (fireflies / zoom / google_meet) hands to a single extraction
#: call. NOT a head-truncation window: a high-value fact can sit anywhere in
#: a long call — deep asks on a real 1,064-sentence call landed ~22k chars
#: in — so these providers feed the FULL transcript (their own
#: summary/agenda first, where they have one) and this exists only to stop a
#: pathological multi-hour call from blowing the model's context window.
#: 200k chars ~= 50k tokens, ~25% of claude-sonnet-4-6's ~200k-token context,
#: leaving ample headroom for the system prompt, schema and output. One
#: shared constant so the three providers can't silently drift apart on it.
TRANSCRIPT_CHAR_CEILING = 200_000


@dataclass
class RawRecord:
    provider: str            # "clickup" | "hubspot" | "fireflies" | ...
    kind: str                # provider-side record kind: task / deal / meeting / note
    external_id: str         # provider-side id (drives idempotency)
    title: str
    text: str                # compact textual rendering for extraction
    properties: dict = field(default_factory=dict)   # structured fields (amounts, status…)
    timestamp: Optional[str] = None                   # ISO — provider-side updated/created

    def render(self) -> str:
        """One-record rendering used inside extraction batches."""
        props = ", ".join(f"{k}={v}" for k, v in self.properties.items() if v not in (None, ""))
        parts = [f"[{self.provider}/{self.kind} id={self.external_id}"
                 + (f" at={self.timestamp}" if self.timestamp else "") + "]",
                 f"title: {self.title}"]
        if props:
            parts.append(f"data: {props}")
        if self.text and self.text != self.title:
            parts.append(self.text)
        return "\n".join(parts)
