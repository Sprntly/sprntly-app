"""Gate decision function: turns a ranked LocateResult into one of three outcomes.

Pure function — no LLM, no network, no logging, no side effects.
The endpoint (the route that calls decide_gate) resolves the repo-specific
threshold via threshold_for_repo and passes it in explicitly, keeping this
module stateless and easily testable.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.design_agent.codebase_map.locate import (
    ExternalEntryPointSignal,
    LocateCandidate,
    LocateResult,
)
from app.design_agent.codebase_map.shell import APP_SHELL_NODE_ID

GateDecision = Literal["auto_proceed", "proceed_with_note", "ranked_confirm"]

# The two host targets a spanning would-be-decline can be routed to instead of
# being declined. Surfaced as GateResult.routing; None on every non-spans path.
SpansRouting = Literal["attach-to-shell", "attach-to-primary-domain"]

# Starting threshold for the auto-proceed gate. 100% precision on the
# evaluation set. Re-calibrate per repo via _PER_REPO_THRESHOLD entries as
# telemetry accumulates.
_DEFAULT_AUTO_PROCEED_THRESHOLD = 80

# Trust floor for acting on a candidate's placement classification when routing a
# spanning would-be-decline to a host. A SEPARATE signal from the auto-proceed
# threshold (which gates which-SURFACE confidence): this gates how certain the
# model is of the KIND of placement before we override a decline and attach the
# feature to a host. Calibrated at 85 from the validation battery — below it we
# do not trust the placement and the feature still declines.
_SPANS_ROUTING_CLASSIFICATION_THRESHOLD = 85

# Per-repo overrides. Empty in the initial release — the calibration report
# feeds future entries. No DB; this is an in-code starting value.
_PER_REPO_THRESHOLD: dict[str, int] = {}

# Trust floor for surfacing the external-entry-point signal on an outcome that
# would otherwise be a plain ranked_confirm (no strong in-app match — see
# `_external_surface` below). A SEPARATE signal from both the auto-proceed
# threshold (which-surface confidence) and the spans-routing classification
# threshold (kind-of-placement confidence): this one gates the model's
# certainty that the PRD's entry point lives OUTSIDE the connected codebase
# entirely. Calibrated below auto-proceed (a heuristic escape hatch does not
# need auto-proceed's bar) but high enough that an ordinary weak/ambiguous
# in-app flow is not mistaken for an external one.
_EXTERNAL_SURFACE_CONFIDENCE_THRESHOLD = 70


def threshold_for_repo(repo: str) -> int:
    """Per-repo auto-proceed threshold.

    Returns the per-repo override when one is registered, else the default.
    The override map is empty in the initial release; telemetry and the
    calibration report feed future entries (no DB; this is an in-code
    starting value, not persisted state).
    """
    return _PER_REPO_THRESHOLD.get(repo, _DEFAULT_AUTO_PROCEED_THRESHOLD)


class GateResult(BaseModel):
    decision: GateDecision
    chosen: list[LocateCandidate] = Field(default_factory=list)
    # auto_proceed / proceed_with_note → the screen(s) generation runs on
    # ranked_confirm                    → [] (the user picks from `ranked`)
    ranked: list[LocateCandidate] = Field(default_factory=list)
    # the full ranked top-3 the picker shows (always populated when non-empty)
    threshold: int  # threshold this decision was made against (for telemetry)
    top_confidence: int = 0  # leading candidate's confidence; 0 when no candidates
    routing: Optional[SpansRouting] = None
    # set ONLY on the spans-routing path: "attach-to-shell" when a spanning
    # would-be-decline was routed to the global app-shell surface, or
    # "attach-to-primary-domain" when anchored to the top-ranked involved domain
    # surface. None for every other decision. Additive + default-safe, so existing
    # call-sites and response serializers are unaffected.
    external_surface: Optional[ExternalEntryPointSignal] = None
    # Populated ONLY on a ranked_confirm outcome that would otherwise decline —
    # the no-candidates, ambiguous-leading, and genuine-no-host-decline
    # branches — AND only when the SAME locate call's own read of the PRD
    # flagged the entry point as genuinely external at/above
    # _EXTERNAL_SURFACE_CONFIDENCE_THRESHOLD. NEVER populated on auto_proceed /
    # proceed_with_note / the spans-routing rescue: a real in-app match always
    # wins over this heuristic, so an ordinary flow is byte-for-byte
    # unaffected. None on every other path. Additive + default-safe, mirroring
    # the `routing` field's shape.


def _route_spanning_decline(
    result: LocateResult,
    *,
    threshold: int,
    has_app_shell: bool,
    app_shell_node_id: str,
    top_confidence: int,
) -> Optional[GateResult]:
    """Rescue a would-be no-host decline the model flagged as spanning surfaces.

    Some cross-cutting features (a notification center, a global search, a command
    palette) have no single domain screen that owns them, so the locate model
    declines — yet it marks the feature spans_multi_surface because it overlays
    surfaces it does not own. Rather than lose it to a decline, route it to a host
    the model ALREADY named:

      - chrome-level → the global app-shell surface, but ONLY when the model
        actually echoed the app-shell node as one of its candidates. A feature
        merely SOUNDING global ("open from anywhere") is NOT enough — that is the
        over-fit trap, and with no echoed app-shell host it still declines, so the
        app-shell never degrades into a catch-all.
      - otherwise → the top-ranked domain candidate the model named
        (attach-to-primary-domain).

    Routing trusts the HOST candidate's classification_confidence (NOT the
    advisory modify-vs-attach sub-label) and only acts at or above the routing
    threshold. Returns a proceed GateResult when a trustworthy host is found, else
    None — the caller then falls through to the genuine-decline path. A spanning
    feature with no trustworthy host (not even the app-shell) is a brand-new
    domain area and correctly declines.
    """
    # The spanning-decline signal: the model declined but flagged the feature as
    # spanning surfaces. Absent this signal there is nothing to rescue.
    spanning_decline = any(
        c.classification == "no-host-decline" and c.spans_multi_surface
        for c in result.candidates
    )
    if not spanning_decline:
        return None

    # Candidate hosts are the non-decline candidates the model actually named
    # (a real id or route). The decline candidate itself carries no host.
    host_candidates = [
        c
        for c in result.candidates
        if c.classification != "no-host-decline" and (c.id or c.route)
    ]

    # Chrome-level: the map carries an app-shell surface AND the model echoed its
    # node id as a host. Trust the placement at/above the routing threshold.
    if has_app_shell:
        for c in host_candidates:
            if (
                c.id == app_shell_node_id
                and c.classification_confidence
                >= _SPANS_ROUTING_CLASSIFICATION_THRESHOLD
            ):
                return GateResult(
                    decision="proceed_with_note",
                    chosen=[c],
                    ranked=list(result.candidates),
                    threshold=threshold,
                    top_confidence=top_confidence,
                    routing="attach-to-shell",
                )

    # Not chrome-level: anchor to the top-ranked DOMAIN host the model named,
    # trusting the placement at/above the routing threshold. candidates arrive
    # descending by confidence, so the first qualifying domain host is primary.
    for c in host_candidates:
        if (
            c.id != app_shell_node_id
            and c.classification_confidence
            >= _SPANS_ROUTING_CLASSIFICATION_THRESHOLD
        ):
            return GateResult(
                decision="proceed_with_note",
                chosen=[c],
                ranked=list(result.candidates),
                threshold=threshold,
                top_confidence=top_confidence,
                routing="attach-to-primary-domain",
            )

    return None


def _external_surface(result: LocateResult) -> Optional[ExternalEntryPointSignal]:
    """The external-entry-point signal to attach to a ranked_confirm outcome.

    Trusts the SAME locate call's own `external_entry_point` read (no second
    LLM round-trip) when it is both detected and at/above the trust floor;
    else None. Called ONLY from the three ranked_confirm return sites in
    decide_gate below — the auto_proceed / proceed_with_note / spans-routing
    branches never call this, which is what keeps the signal from ever firing
    on an ordinary flow that already found a real in-app match.
    """
    sig = result.external_entry_point
    if sig.detected and sig.confidence >= _EXTERNAL_SURFACE_CONFIDENCE_THRESHOLD:
        return sig
    return None


def decide_gate(
    result: LocateResult,
    *,
    threshold: Optional[int] = None,
    has_app_shell: bool = False,
    app_shell_node_id: str = APP_SHELL_NODE_ID,
) -> GateResult:
    """Apply the location gate to a LocateResult and return a structured decision.

    Precedence (first match wins):
    1. No candidates                        → ranked_confirm
    2. Leading candidate is ambiguous       → ranked_confirm
    3. is_multi_node + confidence >= t + not ambiguous → proceed_with_note
    4. confidence >= t + not ambiguous + not multi-node → auto_proceed
    5. Spanning would-be-decline with a trustworthy host → proceed_with_note
       (routing="attach-to-shell" | "attach-to-primary-domain")
    6. confidence < t / genuine no-host decline → ranked_confirm

    Every ranked_confirm outcome (1, 2, 6) additionally carries
    `external_surface` when the SAME locate call's own read of the PRD flagged
    the entry point as genuinely external — see `_external_surface`. This is
    layered ON TOP of the precedence above, never a 7th branch: it only ever
    enriches an outcome that was already going to be ranked_confirm, so a real
    in-app match (auto_proceed / proceed_with_note, including the spans-routing
    rescue) is never affected.

    locate.py guarantees candidates are ordered descending by confidence; this
    function does NOT re-sort them — the invariant is asserted below.

    The caller (the endpoint) is responsible for resolving the per-repo
    threshold via threshold_for_repo(repo) and passing it in. When threshold
    is None, the default is used directly (the gate does not look up the repo
    itself — this keeps the function pure).

    has_app_shell / app_shell_node_id are the MINIMAL map signal the spans-routing
    step needs: whether the map promoted an app-shell surface, and that surface's
    stable id. Both are keyword-optional and default-safe so existing call-sites
    compile unchanged; the gate never receives the whole MapResult.
    """
    t = _DEFAULT_AUTO_PROCEED_THRESHOLD if threshold is None else threshold

    if not result.candidates:
        return GateResult(
            decision="ranked_confirm",
            chosen=[],
            ranked=[],
            threshold=t,
            top_confidence=0,
            external_surface=_external_surface(result),
        )

    # The locate service guarantees descending-by-confidence order; the gate
    # relies on this invariant and must not re-sort.
    leading = result.candidates[0]
    top_confidence = leading.confidence

    if leading.ambiguous:
        # The forced-abstention flag is authoritative; a candidate the model
        # itself flagged ambiguous never auto-proceeds even at a high numeric
        # confidence.
        return GateResult(
            decision="ranked_confirm",
            chosen=[],
            ranked=list(result.candidates),
            threshold=t,
            top_confidence=top_confidence,
            external_surface=_external_surface(result),
        )

    if result.is_multi_node and leading.confidence >= t:
        # Superset: the PRD legitimately spans a screen set. Recreating an
        # adjacent screen is a cost issue, not a wrongness issue. Proceed with
        # the whole set and surface a note to the user.
        return GateResult(
            decision="proceed_with_note",
            chosen=list(result.candidates),
            ranked=list(result.candidates),
            threshold=t,
            top_confidence=top_confidence,
        )

    if leading.confidence >= t:
        # Threshold is inclusive: confidence == threshold auto-proceeds (>=).
        # Single-node case only — multi-node was handled above.
        return GateResult(
            decision="auto_proceed",
            chosen=[leading],
            ranked=list(result.candidates),
            threshold=t,
            top_confidence=top_confidence,
        )

    # Spans-routing: before declining, try to rescue a no-host candidate the model
    # flagged as spanning multiple surfaces by routing it to a host it already
    # named (the app-shell for chrome-level features, else the primary domain
    # surface). Only a feature with no trustworthy host — including the app-shell —
    # falls through to the genuine decline below.
    spans_routed = _route_spanning_decline(
        result,
        threshold=t,
        has_app_shell=has_app_shell,
        app_shell_node_id=app_shell_node_id,
        top_confidence=top_confidence,
    )
    if spans_routed is not None:
        return spans_routed

    # Below threshold / genuine no-host decline.
    return GateResult(
        decision="ranked_confirm",
        chosen=[],
        ranked=list(result.candidates),
        threshold=t,
        top_confidence=top_confidence,
        external_surface=_external_surface(result),
    )
