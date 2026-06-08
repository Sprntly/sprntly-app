"""Business Context — the company's structured, provenance-tracked "lens".

The organizational context every downstream agent reads a signal through
(skill: business-context). Mirrors `backend/skills/business-context/templates/
business-context-schema.yaml` faithfully: eight layers (identity, business_model,
users_segments, product_value, market_competition, goals_strategy, vocabulary,
meta), with every leaf wrapped in a `Meta` provenance envelope
({src, conf, as_of, evidence}). Unknowns are explicit (value=None, src="unknown"),
never omitted-as-if-known, never guessed.

Storage: `companies.business_context jsonb` (the doc carries its own `version`,
bumped on every save — same versioned-config-entity pattern as kpi_tree.py).
Tolerates partials: everything is optional except identity basics, so a freshly
seeded or hand-edited doc still round-trips.

render_for_prompt() emits the compact, brief.md-shaped text agents read.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

from app.db.client import require_client

logger = logging.getLogger(__name__)

Src = Literal["given", "user", "inferred", "web", "unknown"]
Conf = Literal["high", "med", "low"]

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Provenance envelope — every leaf in the schema carries this.
# --------------------------------------------------------------------------- #
class Meta(BaseModel):
    """Per-leaf provenance wrapper from the schema header:
    value + src (given|user|inferred|web|unknown) + conf (high|med|low)
    + as_of (date) + evidence (the exact source snippet for inferred/web leaves).

    The schema names sources given/inferred/unknown; we additionally honor
    `user` (a field a human edited via the PUT route — never overwritten by the
    agent) and `web` (a field the web-research pass filled, which REQUIRES an
    evidence snippet). `given` is treated as user-authoritative too (onboarding
    facts the human supplied).
    """

    value: Any = None
    src: Src = "unknown"
    conf: Optional[Conf] = None
    as_of: Optional[str] = None
    evidence: Optional[str] = None

    @property
    def is_user_authoritative(self) -> bool:
        """True for fields a human supplied — the agent must never overwrite
        these (only fill gaps / add candidates)."""
        return self.src in ("user", "given")

    @property
    def is_known(self) -> bool:
        return self.src != "unknown" and self.value not in (None, "", [], {})


def _m(value: Any, src: Src = "unknown", **kw: Any) -> Meta:
    """Terse Meta constructor for seeders/agents."""
    return Meta(value=value, src=src, **kw)


# --------------------------------------------------------------------------- #
# Layer 1 — Identity & firmographics
# --------------------------------------------------------------------------- #
class Identity(BaseModel):
    legal_name: Meta = Field(default_factory=Meta)
    also_known_as: Meta = Field(default_factory=Meta)
    website: Meta = Field(default_factory=Meta)
    one_liner: Meta = Field(default_factory=Meta)
    industry: Meta = Field(default_factory=Meta)
    sub_vertical: Meta = Field(default_factory=Meta)
    company_size: Meta = Field(default_factory=Meta)
    stage: Meta = Field(default_factory=Meta)
    hq_geography: Meta = Field(default_factory=Meta)
    markets_served: Meta = Field(default_factory=Meta)


# --------------------------------------------------------------------------- #
# Layer 2 — Business model & economics
# --------------------------------------------------------------------------- #
class BusinessModel(BaseModel):
    # `model_type` collides with pydantic's protected `model_` namespace; the
    # field name mirrors the schema verbatim, so opt out of the guard.
    model_config = {"protected_namespaces": ()}

    model_type: Meta = Field(default_factory=Meta)
    revenue_model: Meta = Field(default_factory=Meta)
    pricing_model: Meta = Field(default_factory=Meta)
    who_pays: Meta = Field(default_factory=Meta)
    who_uses: Meta = Field(default_factory=Meta)
    monetization_unit: Meta = Field(default_factory=Meta)
    unit_economics_shape: Meta = Field(default_factory=Meta)
    good_outcome: Meta = Field(default_factory=Meta)


# --------------------------------------------------------------------------- #
# Layer 3 — Users & segments
# --------------------------------------------------------------------------- #
class Segment(BaseModel):
    name: Meta = Field(default_factory=Meta)
    description: Meta = Field(default_factory=Meta)
    jtbd: Meta = Field(default_factory=Meta)
    is_buyer: Meta = Field(default_factory=Meta)
    is_user: Meta = Field(default_factory=Meta)
    is_champion: Meta = Field(default_factory=Meta)
    relative_size: Meta = Field(default_factory=Meta)


class UsersSegments(BaseModel):
    segments: list[Segment] = Field(default_factory=list)
    primary_segment: Meta = Field(default_factory=Meta)


# --------------------------------------------------------------------------- #
# Layer 4 — Product & value
# --------------------------------------------------------------------------- #
class ProductValue(BaseModel):
    what_it_does: Meta = Field(default_factory=Meta)
    core_value_moments: Meta = Field(default_factory=Meta)
    activation_definition: Meta = Field(default_factory=Meta)
    key_features: Meta = Field(default_factory=Meta)
    platforms: Meta = Field(default_factory=Meta)


# --------------------------------------------------------------------------- #
# Layer 5 — Market & competition (lightweight)
# --------------------------------------------------------------------------- #
class MarketCompetition(BaseModel):
    category: Meta = Field(default_factory=Meta)
    main_alternatives: Meta = Field(default_factory=Meta)
    positioning_angle: Meta = Field(default_factory=Meta)


# --------------------------------------------------------------------------- #
# Layer 6 — Goals & strategy
# --------------------------------------------------------------------------- #
class GoalsStrategy(BaseModel):
    stated_goal: Meta = Field(default_factory=Meta)
    north_star: Meta = Field(default_factory=Meta)
    current_priorities: Meta = Field(default_factory=Meta)
    known_constraints: Meta = Field(default_factory=Meta)


# --------------------------------------------------------------------------- #
# Layer 7 — Vocabulary & definitions
# --------------------------------------------------------------------------- #
class VocabTerm(BaseModel):
    term: Meta = Field(default_factory=Meta)
    their_meaning: Meta = Field(default_factory=Meta)
    sprntly_default: Meta = Field(default_factory=Meta)
    note: Meta = Field(default_factory=Meta)


class Vocabulary(BaseModel):
    terms: list[VocabTerm] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Layer 8 — Meta (document-level: dates, refresh trigger, sources, overall conf)
# --------------------------------------------------------------------------- #
class SourceRef(BaseModel):
    url: Optional[str] = None
    as_of: Optional[str] = None


class DocMeta(BaseModel):
    created: Meta = Field(default_factory=Meta)
    last_refreshed: Meta = Field(default_factory=Meta)
    refresh_trigger: Meta = Field(
        default_factory=lambda: Meta(
            value="rebuild on pricing change, new segment, or after 6 months",
            src="given",
        )
    )
    overall_confidence: Meta = Field(default_factory=Meta)
    sources: list[SourceRef] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
class BusinessContext(BaseModel):
    """The full structured business context — eight layers + a version. Every
    field tolerates partials; only identity is required to be present (its
    leaves may still be unknown)."""

    identity: Identity = Field(default_factory=Identity)
    business_model: BusinessModel = Field(default_factory=BusinessModel)
    users_segments: UsersSegments = Field(default_factory=UsersSegments)
    product_value: ProductValue = Field(default_factory=ProductValue)
    market_competition: MarketCompetition = Field(default_factory=MarketCompetition)
    goals_strategy: GoalsStrategy = Field(default_factory=GoalsStrategy)
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)
    meta: DocMeta = Field(default_factory=DocMeta)
    version: int = 1

    # ---- rendering ------------------------------------------------------- #
    def render_for_prompt(self, *, max_chars: Optional[int] = None) -> str:
        """Compact, brief.md-template-shaped text for agent prompts. Shows only
        KNOWN fields (unknowns are omitted, exactly like the human brief) so the
        block stays clean. `max_chars` truncates (callers cap synthesis at ~1500).
        """
        out: list[str] = []

        def known(m: Meta) -> bool:
            return isinstance(m, Meta) and m.is_known

        def val(m: Meta) -> str:
            v = m.value
            if isinstance(v, list):
                return ", ".join(str(x) for x in v if x not in (None, ""))
            return str(v)

        ident = self.identity
        if known(ident.legal_name) or known(ident.one_liner):
            name = val(ident.legal_name) if known(ident.legal_name) else "The company"
            line = name
            if known(ident.one_liner):
                line += f" — {val(ident.one_liner)}"
            out.append(f"About: {line}")
            extras = [val(m) for m in (ident.industry, ident.sub_vertical,
                                       ident.stage, ident.hq_geography) if known(m)]
            if extras:
                out.append("  " + " · ".join(extras))

        bm = self.business_model
        bm_bits = []
        if known(bm.model_type):
            bm_bits.append(f"model {val(bm.model_type)}")
        if known(bm.who_pays):
            bm_bits.append(f"pays: {val(bm.who_pays)}")
        if known(bm.who_uses):
            bm_bits.append(f"uses: {val(bm.who_uses)}")
        if bm_bits:
            out.append("Business model: " + "; ".join(bm_bits))
        if known(bm.good_outcome):
            out.append(f"Good outcome for them: {val(bm.good_outcome)}")

        segs = [s for s in self.users_segments.segments if known(s.name)]
        if segs:
            out.append("User groups:")
            for s in segs:
                roles = []
                for label, m in (("buyer", s.is_buyer), ("user", s.is_user),
                                 ("champion", s.is_champion)):
                    if known(m) and str(m.value).lower() in ("true", "1"):
                        roles.append(label)
                line = f"  - {val(s.name)}"
                if known(s.jtbd):
                    line += f" (job: {val(s.jtbd)})"
                if roles:
                    line += f" [{'/'.join(roles)}]"
                out.append(line)

        pv = self.product_value
        if known(pv.what_it_does):
            out.append(f"Product: {val(pv.what_it_does)}")
        if known(pv.activation_definition):
            out.append(f"Activation (their terms): {val(pv.activation_definition)}")

        vocab = [t for t in self.vocabulary.terms if known(t.term)]
        if vocab:
            out.append("Their vocabulary:")
            for t in vocab:
                line = f"  - {val(t.term)}"
                if known(t.their_meaning):
                    line += f" = {val(t.their_meaning)}"
                out.append(line)

        mc = self.market_competition
        mc_bits = []
        if known(mc.category):
            mc_bits.append(f"category {val(mc.category)}")
        if known(mc.main_alternatives):
            mc_bits.append(f"alternatives: {val(mc.main_alternatives)}")
        if known(mc.positioning_angle):
            mc_bits.append(f"positioned as {val(mc.positioning_angle)}")
        if mc_bits:
            out.append("Market: " + "; ".join(mc_bits))

        gs = self.goals_strategy
        if known(gs.stated_goal):
            out.append(f"Goal: {val(gs.stated_goal)}")
        if known(gs.known_constraints):
            out.append(f"Constraints: {val(gs.known_constraints)}")

        text = "\n".join(out)
        if max_chars is not None and len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return text


# --------------------------------------------------------------------------- #
# Versioned load / save (mirrors kpi_tree.py)
# --------------------------------------------------------------------------- #
def load_business_context(enterprise_id: str) -> Optional[BusinessContext]:
    """Read the company's business context; None if unset/empty/invalid."""
    r = (
        require_client().table("companies")
        .select("business_context")
        .eq("id", enterprise_id)
        .execute()
    )
    if not r.data:
        return None
    raw = r.data[0].get("business_context") or {}
    if not raw:
        return None
    try:
        return BusinessContext.model_validate(raw)
    except Exception:  # noqa: BLE001 — tolerate legacy/hand-edited shapes
        logger.warning(
            "invalid business_context for %s; ignoring", enterprise_id, exc_info=True
        )
        return None


def save_business_context(
    enterprise_id: str, doc: BusinessContext
) -> BusinessContext:
    """Persist; bumps version past whatever is currently stored and stamps
    meta.last_refreshed."""
    current = load_business_context(enterprise_id)
    doc.version = (current.version + 1) if current else max(1, doc.version)
    today = date.today().isoformat()
    if not doc.meta.created.is_known:
        doc.meta.created = Meta(value=today, src="given")
    doc.meta.last_refreshed = Meta(value=today, src="given")
    (
        require_client().table("companies")
        .update({"business_context": doc.model_dump()})
        .eq("id", enterprise_id)
        .execute()
    )
    return doc
