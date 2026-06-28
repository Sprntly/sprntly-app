"""Locate LLM service: maps a PRD + compact MapResult to ranked screen candidates.

Single LLM call. Returns up to three LocateCandidates ranked by confidence, each
carrying a 0-100 confidence score, a one-line rationale, and an explicit ambiguous
flag. Mirrors the single-shot messages.create + JSON-fence-strip + model_validate +
RunUsage never-raise pattern from design_system/brief.py.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.types import MapResult

logger = logging.getLogger(__name__)

# Canonical model for the locate call; never substitute opus here.
_MODEL = "claude-sonnet-4-6"

# Hard output cap — the JSON payload is at most three candidates.
_LOCATE_MAX_TOKENS = 1024

# Clamp free-text rationale fields after parsing.
_MAX_RATIONALE_CHARS = 300

# Cap a user-supplied steer ("search again" direction) before it enters the
# prompt. Defensive only — the route layer trims/caps first; this is the floor.
_MAX_HINT_CHARS = 300

# Guard against pathologically large repos blowing the stable prefix.
_COMPACT_MAP_CHAR_CAP = 8000

# ── Image-as-steer ────────────────────────────────────────────────────────────
# An optional screenshot of the target screen can ride the volatile user turn as
# an Anthropic image content block. It is a richer steer signal (read for its
# on-screen text/route cues), NOT a visual pixel-match. The cached system+map
# prefix is untouched so a steered re-search stays a prefix cache hit.
#
# Server-side decode cap on the image bytes (≈5 MB), mirroring the client bound.
# Oversized or undecodable images fall OPEN to the text-only path — never raise.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Allowed image MIME types (Anthropic vision set, minus gif).
_ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
# Defensive bounds on the model-emitted `read_cues` list.
_MAX_READ_CUES = 12
_MAX_READ_CUE_CHARS = 120
# data:image/<type>;base64,<payload> — the only shape the client sends.
_DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.DOTALL)

# Appended to the volatile user turn when (and only when) a valid image rides
# along. Instructs the model to read the screenshot's TEXT/route cues and steer
# the ranking — explicitly not a pixel-match — and to report the cues it read.
_IMAGE_INSTRUCTION = (
    "A screenshot of the target screen is attached. Read its on-screen TEXT and "
    "route cues — URL/route, nav labels, headings, button text — and re-rank the "
    "screen candidates toward what it depicts. This is a text/route-cue read, NOT "
    "a visual pixel-match. List the cues you read in `read_cues`."
)


def _decode_image_data_url(image: object) -> tuple[Optional[str], Optional[str], str]:
    """Validate a base64 image data URL. Returns (media_type, raw_b64, status).

    status is one of:
      - "applied"          — well-formed, allowed MIME, decodes within the cap.
      - "ignored_oversize" — decodes but exceeds _MAX_IMAGE_BYTES.
      - "ignored_decode"   — not a str, malformed data URL, disallowed MIME, or
                             a base64 decode error.

    NEVER raises. On any ignored_* status the caller falls open to the text-only
    locate path (no image block, plain-string user turn).
    """
    try:
        if not isinstance(image, str):
            return (None, None, "ignored_decode")
        match = _DATA_URL_RE.match(image.strip())
        if not match:
            return (None, None, "ignored_decode")
        media_type = match.group(1).lower()
        raw_b64 = match.group(2)
        if media_type not in _ALLOWED_IMAGE_MIME:
            return (None, None, "ignored_decode")
        try:
            decoded = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError):
            return (None, None, "ignored_decode")
        if len(decoded) > _MAX_IMAGE_BYTES:
            return (media_type, raw_b64, "ignored_oversize")
        return (media_type, raw_b64, "applied")
    except Exception:  # noqa: BLE001 — fall open, never let a steer image 500
        return (None, None, "ignored_decode")


class LocateCandidate(BaseModel):
    route: str = ""
    # Stable node id, echoed from the [id] shown at the start of each compact-map
    # line. The candidate-validity check keys on this id (falling back to `route`
    # when empty, since a routed node's id IS its route). Carrying the id is what
    # lets a non-route host — the app shell, an in-page section — survive the
    # drop; route-only keying silently deleted those.
    id: str = ""
    entry_component: str = ""
    confidence: int = 0          # 0-100, clamped on parse — certainty in WHICH surface
    rationale: str = ""          # one-line model rationale
    ambiguous: bool = False      # the model's explicit abstention flag for this candidate
    # Placement classification: the KIND of placement the PRD implies for this
    # surface. "modify-existing" and "attach-to-host" both mean a host surface
    # was located — the difference is an advisory placement hint, not a routing
    # gate. "no-host-decline" is reserved for a genuinely unhosted feature. An
    # unrecognized value normalizes back to "modify-existing" on parse.
    classification: Literal[
        "modify-existing", "attach-to-host", "no-host-decline"
    ] = "modify-existing"
    # True when the feature itself legitimately spans more than one surface.
    # Distinct from LocateResult.is_multi_node (which says the RESULT is a
    # screen set); this is a per-candidate signal about the feature.
    spans_multi_surface: bool = False
    # 0-100, clamped on parse. Certainty IN THE CLASSIFICATION — a separate
    # signal from `confidence` (certainty in which surface). A downstream gate
    # consumes this field; this module only carries the signal, it does not
    # apply any threshold here.
    classification_confidence: int = 0


class LocateResult(BaseModel):
    candidates: list[LocateCandidate] = Field(default_factory=list)  # ranked, ≤3
    is_multi_node: bool = False  # True when the PRD legitimately spans a screen set
    # honest default: empty candidates ⇒ "no codebase locate" ⇒ caller degrades
    # Cues the model read off an attached screenshot. Optional,
    # defensively coerced; forced empty on any image fall-open so the UI never
    # claims an image steer that did not happen.
    read_cues: list[str] = Field(default_factory=list)
    # How an attached image was handled: "absent" (none passed), "applied"
    # (rode the call), "ignored_oversize" / "ignored_decode" (fell open to
    # text-only). Surfaced so the route + UI can avoid an image-steer claim.
    image_status: str = "absent"


def compact_map(m: "MapResult") -> str:
    """One line per screen node: route · entry_component · N components.

    Includes a SHELL line (brand + nav labels) and the posture.
    No file bodies, no source — the registry view is sufficient for locate.
    """
    lines: list[str] = []
    lines.append(f"POSTURE: {m.posture}")

    nav_labels = ", ".join(item.label for item in m.shell.nav_items)
    lines.append(f'SHELL: brand="{m.shell.brand}" nav=[{nav_labels}]')

    lines.append("SCREENS:")
    for node in m.nodes:
        count = len(node.composed_components)
        suffix = " (route-state)" if node.is_route_state else ""
        kind_suffix = "" if node.kind == "route" else f" ({node.kind})"
        lines.append(
            f"- [{node.id}] {node.route} · {node.entry_component}"
            f" · {count} components{suffix}{kind_suffix}"
        )

    result = "\n".join(lines)
    if len(result) > _COMPACT_MAP_CHAR_CAP:
        result = result[: _COMPACT_MAP_CHAR_CAP - 4] + "\n..."
    return result


def locate_screen(
    prd_text: str,
    map_result: "MapResult",
    *,
    hint: Optional[str] = None,
    image: Optional[str] = None,
    client=None,
) -> LocateResult:
    """Map a PRD to ranked screen candidates via a single LLM call.

    Returns a LocateResult on success, or LocateResult() (empty candidates) on
    any failure. Never raises — callers degrade to no-locate rather than 500.

    Parameters
    ----------
    prd_text:
        The PRD text to locate a target screen for.
    map_result:
        The codebase map containing the set of valid screen nodes.
    hint:
        An optional user-supplied direction ("search again" steer, e.g. "the
        settings page"). When present and non-blank it is appended to the
        volatile user turn as a `User direction:` line so candidates re-rank
        toward the steer. The cached system+map prefix is untouched, so a steered
        re-search is a cache hit on the prefix. Empty/blank/None ⇒ the user turn
        is byte-for-byte today's `PRD:\\n{prd_text}` and behaviour is unchanged.
    image:
        An optional base64 image data URL ("data:image/<png|jpeg|webp>;base64,…")
        — a screenshot of the target screen, client-downscaled. When present and
        decodable within the server cap, it rides the volatile user turn as an
        Anthropic image content block (alongside PRD + hint) and the user turn is
        switched from a plain string to a content-block list; the model reads its
        on-screen text/route cues and re-ranks. When absent/blank the user turn is
        byte-for-byte today's plain string (no regression). When oversized or
        undecodable it FALLS OPEN to the text-only path — never raises — and the
        result's `image_status` records why. The cached system+map prefix is
        untouched in both paths, so a steered re-search is a prefix cache hit.
    client:
        An Anthropic client (or any compatible object). When None, the cached
        design-agent client is used. Injecting a fake here enables unit-testing
        without network calls.
    """
    from app.design_agent.prompts import LOCATE_SYSTEM

    start_ms = time.monotonic()
    _usage: Optional[object] = None
    _status = "error"
    _error_class: Optional[str] = None
    result = LocateResult()

    try:
        if client is None:
            from app.design_agent.client import get_design_agent_client

            client = get_design_agent_client()

        from app.llm_telemetry import RunUsage

        map_text = compact_map(map_result)
        # Candidate validity is checked against BOTH the stable node-id set and
        # the route set. The id set admits a non-route host (an in-page section,
        # the app shell) that carries a descriptive, non-route id; the route set
        # keeps backward-compat for a route-only candidate (or any candidate that
        # echoed a known route but not its id). See the OR-based check below.
        valid_node_ids = {node.id for node in map_result.nodes}
        valid_routes = {node.route for node in map_result.nodes}

        # System blocks: stable prefix ends with the compact map carrying the
        # cache breakpoint. PRD is the volatile user turn — no cache_control.
        system_blocks = [
            {"type": "text", "text": LOCATE_SYSTEM},
            {
                "type": "text",
                "text": map_text,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        # The user turn carries the PRD and — when the PM re-searched with a
        # direction — an explicit steer line. The steer is appended (never
        # prepended) so the PRD anchor is unchanged and an empty/blank hint
        # yields the exact same content string as before.
        user_turn = f"PRD:\n{prd_text}"
        steer = (hint or "").strip()
        if steer:
            steer = steer[:_MAX_HINT_CHARS]
            user_turn = (
                f"{user_turn}\n\n"
                f"User direction: {steer}\n"
                "Re-rank the screen candidates toward this direction."
            )

        # Image steer: when a valid screenshot is attached, append the
        # image instruction to the text (order: PRD → hint → image instruction) and
        # carry the image as a content block so the user turn becomes a list. An
        # oversized/undecodable image FALLS OPEN — the text-only plain string is
        # kept and image_status records why. Absent/blank ⇒ byte-for-byte today.
        image_status = "absent"
        image_block = None
        if image is not None and str(image).strip():
            media_type, raw_b64, image_status = _decode_image_data_url(image)
            if image_status == "applied":
                user_turn = f"{user_turn}\n\n{_IMAGE_INSTRUCTION}"
                image_block = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": raw_b64,
                    },
                }
            # ignored_* → fall open: no image block, plain-string user turn below.
        result.image_status = image_status

        if image_block is not None:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_turn},
                        image_block,
                    ],
                }
            ]
        else:
            messages = [{"role": "user", "content": user_turn}]

        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_LOCATE_MAX_TOKENS,
            # Pin to temperature=0 for deterministic screen matching: the same PRD +
            # codebase map should resolve to the same candidate screens on every run.
            temperature=0,
            system=system_blocks,
            messages=messages,
        )

        _usage = RunUsage()
        _usage.add(resp.usage)

        # Extract text from the first content block.
        raw_text: str = ""
        try:
            raw_text = resp.content[0].text
        except (AttributeError, IndexError, TypeError):
            logger.warning("locate: unexpected response shape; returning empty")
            _status = "empty"
            return result

        # Strip optional ```json ... ``` fences.
        text = raw_text.strip()
        fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Parse and validate via the output schema.
        parsed = json.loads(text)

        # Pre-coerce numeric + enum fields before Pydantic validation. The model
        # may emit a float like 0.92, an out-of-range int, or an unrecognized
        # classification label; sanitize here so one stray field never fails the
        # whole validation (which would drop every candidate).
        if isinstance(parsed, dict):
            for cand in parsed.get("candidates", []):
                if not isinstance(cand, dict):
                    continue
                if "confidence" in cand:
                    try:
                        cand["confidence"] = int(float(cand["confidence"]))
                    except (ValueError, TypeError):
                        cand["confidence"] = 0
                # classification_confidence: same int(float) coercion as
                # confidence (truncates a 0-1 float); clamped post-validate.
                if "classification_confidence" in cand:
                    try:
                        cand["classification_confidence"] = int(
                            float(cand["classification_confidence"])
                        )
                    except (ValueError, TypeError):
                        cand["classification_confidence"] = 0
                # Default an unrecognized classification to "modify-existing" so
                # the Literal type validates and the host-located semantics hold.
                if "classification" in cand and cand["classification"] not in (
                    "modify-existing",
                    "attach-to-host",
                    "no-host-decline",
                ):
                    cand["classification"] = "modify-existing"
                # Coerce the spans flag to a real bool (avoid the "false" string
                # truthiness trap while still accepting the common encodings).
                if "spans_multi_surface" in cand:
                    raw_spans = cand["spans_multi_surface"]
                    if isinstance(raw_spans, str):
                        cand["spans_multi_surface"] = raw_spans.strip().lower() in (
                            "true",
                            "1",
                            "yes",
                            "on",
                        )
                    else:
                        cand["spans_multi_surface"] = bool(raw_spans)

            # Defensively coerce the optional top-level `read_cues` BEFORE
            # validation. The model may emit a string, a non-list, or a list with
            # non-str items; a raw bad value would fail the whole model_validate
            # (dropping every candidate). Coerce to a clean, bounded list of
            # non-empty strings so a malformed read_cues never breaks the parse.
            if "read_cues" in parsed:
                raw_cues = parsed.get("read_cues")
                coerced_cues: list[str] = []
                if isinstance(raw_cues, list):
                    for item in raw_cues:
                        if len(coerced_cues) >= _MAX_READ_CUES:
                            break
                        if isinstance(item, str):
                            cleaned = item.strip()[:_MAX_READ_CUE_CHARS]
                            if cleaned:
                                coerced_cues.append(cleaned)
                parsed["read_cues"] = coerced_cues

        raw_result = LocateResult.model_validate(parsed)

        # Post-parse normalization.
        candidates = []
        for c in raw_result.candidates:
            # A candidate is valid if ANY branch admits it:
            #   - its id matches a real node id — admits a routed screen, an
            #     in-page section, or the app shell (the model echoes the
            #     bracketed id from the compact map); OR
            #   - its route matches a real route — backward-compat for a
            #     route-only candidate, or one that echoed a known route but not
            #     its id; OR
            #   - it is a no-host-decline candidate, which has no backing map node
            #     and IS the "nothing in this app can host the feature" signal.
            # A candidate matching none of these named neither a known id nor a
            # known route — a true hallucination — and is dropped.
            if not (
                c.id in valid_node_ids
                or c.route in valid_routes
                or c.classification == "no-host-decline"
            ):
                continue
            # Clamp confidence to [0, 100]; coerce to int first.
            c.confidence = max(0, min(100, int(c.confidence)))
            # Clamp classification_confidence to [0, 100] independently — it is a
            # separate signal from which-surface confidence.
            c.classification_confidence = max(
                0, min(100, int(c.classification_confidence))
            )
            # Clamp free-text rationale.
            if len(c.rationale) > _MAX_RATIONALE_CHARS:
                c.rationale = c.rationale[:_MAX_RATIONALE_CHARS]
            candidates.append(c)

        # read_cues are only a meaningful claim when an image was actually
        # applied. On any fall-open (ignored_*) or no image (absent), force the
        # list empty so the UI never claims an image steer that did not happen.
        read_cues = list(raw_result.read_cues) if image_status == "applied" else []

        # Enforce the ≤3 cap even when the model returns more.
        result = LocateResult(
            candidates=candidates[:3],
            is_multi_node=raw_result.is_multi_node,
            read_cues=read_cues,
            image_status=image_status,
        )
        _status = "complete" if result.candidates else "empty"
        return result

    except Exception as exc:
        _error_class = type(exc).__name__
        _status = "error"
        logger.warning("locate: failed; returning empty — %r", exc)
        return result

    finally:
        duration_ms = int((time.monotonic() - start_ms) * 1000)
        try:
            from app.llm_telemetry import RunUsage, log_llm_run

            if _usage is None:
                _usage = RunUsage()
            log_llm_run(
                operation="design_agent.locate.complete",
                identifier={"repo": map_result.repo, "sha": map_result.commit_sha},
                usage=_usage,
                duration_ms=duration_ms,
                status=_status,
                model=_MODEL,
                error_class=_error_class,
                iters=1,
                n_candidates=len(result.candidates),
            )
        except Exception:
            logger.debug("locate: telemetry failed (non-fatal)", exc_info=True)


def emit_locate_telemetry(
    *,
    repo: str,
    sha: str,
    gate_result: "GateResult",
    n_candidates: int,
) -> None:
    """Emit one structured calibration line per locate request.

    Mirrors the k=v discipline of llm_telemetry.log_llm_run: identifiers only,
    no PRD body, no screen source, no rationale, no installation token.
    Emitted on every /locate request including the unmapped fail-open path
    (sha='', n_candidates=0) so the unmapped rate is observable in logs.
    """
    chosen_screen = gate_result.chosen[0].route if gate_result.chosen else ""
    leading_ranked = gate_result.ranked[0] if gate_result.ranked else None
    ambiguous = leading_ranked.ambiguous if leading_ranked is not None else False
    logger.info(
        "codebase_map.locate repo=%s sha=%s top_confidence=%d decision=%s"
        " chosen_screen=%s ambiguous=%s n_candidates=%d threshold=%d",
        repo,
        sha,
        gate_result.top_confidence,
        gate_result.decision,
        chosen_screen,
        ambiguous,
        n_candidates,
        gate_result.threshold,
    )
