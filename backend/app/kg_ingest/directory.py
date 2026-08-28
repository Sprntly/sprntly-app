"""Person directory + action-item owner resolution for call ingestion.

Two operations, one module:

1. **Person minting** (`mint_persons_for_call`) — every PARTICIPANT of an
   indexed call becomes a `person` entity `{display_name, company, internal}`,
   derived from the participant's email DOMAIN. The raw email is never stored
   (no address, no hash): only the human-readable name (from the local part)
   and the company (from the domain) survive. Deduped on a deterministic
   `(canonical_label, properties.company)` exact key — NOT embeddings — so the
   same person across two calls is ONE node, and two different people who share
   a name at different companies are two nodes. A participant with no derivable
   company (a bare name with no address, or a consumer domain like gmail) is
   SKIPPED — a company-less person node would be noise.

2. **Owner resolution** (`resolve_owners_for_call`) — an action-item signal
   carries `properties.owner` as a NAME string ("Jane Doe"). This matches that
   name against the call's participants (local-part patterns + fuzzy ratio),
   and on a hit find-or-mints that person and stamps
   `properties.owner_person_id` alongside the raw name. A miss (the owner is not
   among the participants) leaves `owner_person_id` unset — never fabricated.

`account` in this layer is a LABEL (`call_index.account`), not a node.

OFF THE EVIDENCE/RETRIEVAL PATH BY CONSTRUCTION. A `person` entity is
`type="person"` and carries no embedding, and every brief/retrieval reader is
theme/signal-scoped: `synthesis.convergence` only reads `type="theme"` entities
and only counts signal→theme edges (a person→company edge is entity→entity and
is filtered out); `graph.retrieval` reaches entities only through
`find_candidates` kNN (typed + embedding-gated) and typed `query_entities`.
`owner_person_id` is a plain property string on a signal that already reaches
its theme regardless — it changes no score. So a person can neither increment
brief sufficiency nor surface in an answer.

Reuses the call-index domain primitives (`derive_account`, `_own_domains`,
`_GENERIC_EMAIL_DOMAINS`) so "what counts as a company / our own domain /
a consumer domain" is decided in exactly one place.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from app import name_match
from app.call_index import _GENERIC_EMAIL_DOMAINS, _own_domains
from app.graph.facade import GraphFacade
from app.graph.types import Entity, Relationship

logger = logging.getLogger(__name__)

PERSON_ENTITY_TYPE = "person"

#: The entity→entity verb wiring a person to the tenant's `company` root. In
#: RELATIONSHIP_VOCAB; `source_kind="entity"` keeps it off convergence's
#: signal→theme evidence walk.
_PERSON_SCOPE_VERB = "SCOPED_TO"


# ── name / company derivation ────────────────────────────────────────────────

def _display_name_from_email(local_part: str) -> str:
    """`jane.doe` → `Jane Doe`. Splits on the usual local-part separators and
    title-cases each token; a local part that is a single opaque token
    (`jdoe`) is title-cased as-is. Never returns empty for a non-empty part."""
    parts = [p for p in re.split(r"[._\-+]+", local_part) if p]
    if not parts:
        return local_part
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def _person_from_email(email: str, own_domains: set[str]) -> Optional[dict]:
    """`{display_name, company, internal}` for one participant email, or None
    when no company can be derived (bare name with no `@`, or a consumer
    domain). Company + name reuse `derive_account`'s exact domain→title
    transform so the label a person carries matches the label a call carries.

    A participant on OUR OWN domain is a real person with a derivable company
    (ours) — kept, flagged `internal=True`. Only address-less names and consumer
    domains are dropped, which is what "no derivable company" means here."""
    value = (email or "").strip().lower()
    if "@" not in value:
        return None
    domain = value.rsplit("@", 1)[1]
    if not domain or domain in _GENERIC_EMAIL_DOMAINS:
        return None
    # Same transform derive_account applies to its winning domain.
    company = domain.rsplit(".", 1)[0].replace("-", " ").title()
    if not company:
        return None
    return {
        "display_name": _display_name_from_email(value.split("@", 1)[0]),
        "company": company,
        "internal": domain in own_domains,
    }


def _person_key(display_name: str, company: str) -> tuple[str, str]:
    """Deterministic dedup key: case/space-normalized `(name, company)`. Exact,
    not fuzzy — the whole point of a deterministic directory."""
    return (
        " ".join((display_name or "").split()).lower(),
        " ".join((company or "").split()).lower(),
    )


# ── person find-or-create ────────────────────────────────────────────────────

def _load_person_index(
    facade: GraphFacade, enterprise_id: str
) -> dict[tuple[str, str], str]:
    """`{(name_key, company_key): entity_id}` over this tenant's existing
    persons — the find half of find-or-create, loaded once so a batch of calls
    is one query, not one per participant."""
    idx: dict[tuple[str, str], str] = {}
    for ent in facade.query_entities(enterprise_id, type=PERSON_ENTITY_TYPE):
        company = (ent.properties or {}).get("company") or ""
        idx[_person_key(ent.canonical_label, company)] = ent.id
    return idx


def _find_or_create_person(
    facade: GraphFacade,
    enterprise_id: str,
    person: dict,
    index: dict[tuple[str, str], str],
    *,
    provider: Optional[str] = None,
) -> str:
    """Return the entity id for `{display_name, company, internal}`, creating it
    (and its SCOPED_TO→company edge) only when the deterministic key is new.
    `index` is mutated so repeated calls in one pass stay deduped without a
    re-query."""
    key = _person_key(person["display_name"], person["company"])
    existing = index.get(key)
    if existing is not None:
        return existing
    ent = Entity(
        enterprise_id=enterprise_id,
        type=PERSON_ENTITY_TYPE,
        canonical_label=person["display_name"],
        properties={"company": person["company"], "internal": person["internal"]},
        provenance={"source": "directory",
                    **({"provider": provider} if provider else {})},
    )
    facade.create_entity(enterprise_id, ent)
    _link_person_to_company(facade, enterprise_id, ent.id)
    index[key] = ent.id
    return ent.id


def _link_person_to_company(
    facade: GraphFacade, enterprise_id: str, person_id: str
) -> None:
    """Wire a freshly-minted person to the tenant's single `company` root via an
    entity→entity SCOPED_TO edge. Only ever called on CREATE, so re-minting an
    existing person never duplicates the edge."""
    company_entity_id = facade.ensure_company_entity(enterprise_id)
    facade.write_relationship(enterprise_id, Relationship(
        enterprise_id=enterprise_id,
        type=_PERSON_SCOPE_VERB,
        source_kind="entity", source_id=person_id,
        target_kind="entity", target_id=company_entity_id,
        provenance={"source": "directory"},
    ))


# ── entry point 1: person minting ────────────────────────────────────────────

def mint_persons_for_call(
    facade: GraphFacade,
    enterprise_id: str,
    indexed_call,
    own_domains: set[str],
    *,
    person_index: Optional[dict[tuple[str, str], str]] = None,
) -> list[str]:
    """Find-or-create a `person` node for every participant of one indexed call
    that has a derivable company. Returns the entity ids touched (created or
    matched). Idempotent: re-running mints nothing new.

    `person_index` lets a caller amortize the find-half over a whole sweep of
    calls (loaded once, mutated in place). Passed None, this loads its own — the
    single-call/unit-test contract."""
    index = person_index if person_index is not None else _load_person_index(
        facade, enterprise_id)
    provider = getattr(indexed_call, "provider", None)
    touched: list[str] = []
    for raw in (getattr(indexed_call, "participants", None) or []):
        person = _person_from_email(str(raw), own_domains)
        if person is None:
            continue  # bare name or consumer domain → no company-less person
        touched.append(
            _find_or_create_person(facade, enterprise_id, person, index,
                                   provider=provider)
        )
    return touched


def mint_persons_for_indexed_calls(
    facade: GraphFacade, enterprise_id: str, calls: list
) -> int:
    """Sweep a tenant's indexed calls, minting persons for all of them under one
    shared own-domain calculation and one shared person index. Returns the
    number of DISTINCT person ids touched. Thin orchestration over
    `mint_persons_for_call` for the call-index sync wiring."""
    if not calls:
        return 0
    own = _own_domains(
        enterprise_id,
        [{"participants": getattr(c, "participants", None) or []} for c in calls],
    )
    index = _load_person_index(facade, enterprise_id)
    touched: set[str] = set()
    for call in calls:
        try:
            touched.update(
                mint_persons_for_call(facade, enterprise_id, call, own,
                                      person_index=index)
            )
        except Exception:  # noqa: BLE001 — one bad call must not stop the sweep
            logger.warning(
                "directory: person minting failed for a %s call in %s",
                getattr(call, "provider", "?"), enterprise_id, exc_info=True,
            )
    return len(touched)


# ── entry point 2: owner resolution ──────────────────────────────────────────

def resolve_owners_for_call(
    facade: GraphFacade,
    enterprise_id: str,
    provider: str,
    external_id: str,
    participants: Iterable[str],
    own_domains: set[str],
    *,
    person_index: Optional[dict[tuple[str, str], str]] = None,
) -> int:
    """For one call's action-item signals carrying `properties.owner`, resolve
    the owner NAME to a participant and stamp `properties.owner_person_id`.
    Returns the number of signals stamped.

    Only an owner that matches a participant WITH an address can be minted to a
    person — a name-only match (Meet, Zoom-host-only) confirms who but yields no
    company-bearing node, so `owner_person_id` stays unset there (graceful
    degradation, not a bug). A raw `owner` name that matches nobody is left
    entirely alone. Never fabricates a person."""
    participant_list = [str(p) for p in (participants or []) if p]
    signals = facade.signals_for_call(enterprise_id, provider, external_id)
    pending = [
        s for s in signals
        if (s.properties or {}).get("owner")
        and not (s.properties or {}).get("owner_person_id")
    ]
    if not pending:
        return 0
    index = person_index if person_index is not None else _load_person_index(
        facade, enterprise_id)
    stamped = 0
    for sig in pending:
        owner_name = str((sig.properties or {}).get("owner") or "").strip()
        matched = name_match.match_name(owner_name, participant_list)
        if not matched or "@" not in matched:
            continue  # miss, or name-only participant → nothing to mint
        person = _person_from_email(matched, own_domains)
        if person is None:
            continue  # owner on a consumer domain → no company-bearing node
        pid = _find_or_create_person(facade, enterprise_id, person, index,
                                     provider=provider)
        facade.update_signal_properties(
            enterprise_id, sig.id, {"owner_person_id": pid})
        stamped += 1
    return stamped


# ── steady-state race backfill ───────────────────────────────────────────────

def backfill_source_call_ids(facade: GraphFacade, enterprise_id: str) -> int:
    """Relink signals that raced ahead of their `call_index` row.

    A call-shaped extraction stamps `source_call_id` from
    `call_index.resolve_call_id` at extraction time; when the transcript reached
    extraction before the index had catalogued it, that resolved to NULL but the
    signal still carries `provenance.provider` / `provenance.external_id`. Once
    the index catches up (this runs right after a call-index refresh), those keys
    resolve, so set `source_call_id`.

    One scoped read + bounded per-signal updates. No LLM, no external calls.
    Legacy pre-branch batched signals have no per-call `external_id` — unlinkable
    by construction — and are left NULL. Returns the number relinked."""
    from app import call_index

    linked = 0
    for sig in facade.unlinked_call_signals(enterprise_id):
        prov = sig.provenance or {}
        provider = prov.get("provider")
        external_id = prov.get("external_id")
        if not external_id or provider not in call_index.CALL_PROVIDERS:
            continue
        call_id = call_index.resolve_call_id(enterprise_id, provider, external_id)
        if call_id is None:
            continue  # still not catalogued — a later cycle heals it
        facade.set_source_call_id(enterprise_id, sig.id, call_id)
        linked += 1
    return linked
