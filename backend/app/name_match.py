"""General person-name resolution — match a name to an email or another name.

A scenario-agnostic primitive: given a person's NAME ("David Troha", "Jay
Watson") and a set of candidate strings (email addresses AND/OR bare display
names), return the candidate that refers to the same person, or None when
nothing matches confidently. It knows nothing about any particular org, person,
or domain — every convention is DERIVED from the name, and the only data tables
are (a) a general nickname/diminutive map and (b) a fuzzy acceptance threshold.

Three tiers, cheapest and most certain first:

  1. **Local-part conventions.** A `(first, last)` name is expanded into the
     email local parts an org might actually use, in ALL orderings — `first`,
     `first.last`, `last.first`, `flast`, `lastf`, `firstl`, initials, etc. —
     so reversed and last-led conventions (`trohad` for David Troha) resolve as
     exact hits, not fuzzy guesses. Separators are normalized away, so
     `jane.doe` / `jane_doe` / `janedoe` are one pattern.

  2. **Nickname / diminutive normalization.** Each name token is expanded
     through `NICKNAME_GROUPS` (Jay↔Jason, Bob/Rob↔Robert, Bill↔William, …),
     BOTH directions, BEFORE patterns are generated — so `jason.watson`
     resolves "Jay Watson" deterministically instead of scraping past a fuzzy
     threshold. The table is plain data: extend a set, or add a group.

  3. **Calibrated fuzzy fallback.** Only when the deterministic tiers miss, a
     `difflib` ratio against the expanded full-name forms decides — at a
     threshold high enough that genuinely different people DECLINE.

Safe failure mode is the contract: no confident match → return None. A caller
that needs attribution must treat None as "unknown", never fabricate.

Reusable beyond owner attribution: any place that has a human name and a bag of
addresses/handles to reconcile (meeting owners, comment mentions, assignee
strings, membership reconciliation) should call `match_name` rather than
re-deriving conventions.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional

#: Fuzzy acceptance floor for the tier-3 fallback. Deterministic tiers (1 and 2)
#: return BEFORE fuzzy runs, so this only gates genuinely inexact matches. Held
#: at 0.82: the motivating real-data misses (reversed-order `trohad`,
#: nickname `Jay`↔`Jason`) are now resolved deterministically by tiers 1–2, so
#: the threshold did not need lowering — lowering it would only admit more
#: false positives between different people.
DEFAULT_THRESHOLD = 0.82

#: When the fuzzy fallback's top two candidates BOTH clear the threshold and sit
#: within this ratio of each other, the match is too close to call — decline
#: rather than pick by a rounding difference. Mirrors the deterministic-tier
#: ambiguity rule for the inexact tier.
_FUZZY_AMBIGUITY_MARGIN = 0.05

#: Nickname / diminutive equivalence groups — plain data, easy to extend. Every
#: token in a group is treated as interchangeable with the others, in BOTH
#: directions, so the canonical given name and its diminutives all resolve to
#: one another. Add a name to a set, or add a whole group, with no code change.
#: A token that appears in more than one group (e.g. "jay" for both James and
#: Jason) is deliberately allowed to expand to the union — recall-oriented, and
#: safe because full-name matching still requires the OTHER token (the surname)
#: to line up.
NICKNAME_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"robert", "rob", "robbie", "bob", "bobby"}),
    frozenset({"william", "will", "willy", "bill", "billy", "liam"}),
    frozenset({"richard", "rich", "richie", "rick", "ricky", "dick"}),
    frozenset({"james", "jim", "jimmy", "jamie", "jay"}),
    frozenset({"jason", "jay"}),
    frozenset({"john", "johnny", "jon", "jack"}),
    frozenset({"jonathan", "jon", "jonny", "nathan"}),
    frozenset({"michael", "mike", "mikey", "mick", "mitch"}),
    frozenset({"christopher", "chris", "topher", "kit"}),
    frozenset({"charles", "charlie", "chas", "chuck"}),
    frozenset({"daniel", "dan", "danny"}),
    frozenset({"david", "dave", "davey"}),
    frozenset({"thomas", "tom", "tommy", "thom"}),
    frozenset({"anthony", "tony", "ant"}),
    frozenset({"joseph", "joe", "joey"}),
    frozenset({"edward", "ed", "eddie", "ted", "ned"}),
    frozenset({"benjamin", "ben", "benji", "benny"}),
    frozenset({"matthew", "matt", "matty"}),
    frozenset({"andrew", "andy", "drew"}),
    frozenset({"nicholas", "nick", "nicky"}),
    frozenset({"peter", "pete", "petey"}),
    frozenset({"samuel", "sam", "sammy"}),
    frozenset({"stephen", "steven", "steve", "stevie"}),
    frozenset({"timothy", "tim", "timmy"}),
    frozenset({"kenneth", "ken", "kenny"}),
    frozenset({"ronald", "ron", "ronnie"}),
    frozenset({"donald", "don", "donnie"}),
    frozenset({"gerald", "gerry", "jerry"}),
    frozenset({"gregory", "greg", "gregg"}),
    frozenset({"lawrence", "larry", "lars"}),
    frozenset({"frederick", "fred", "freddie", "freddy"}),
    frozenset({"francis", "frank", "frankie"}),
    frozenset({"albert", "al", "bert", "bertie"}),
    frozenset({"alexander", "alex", "al", "xander", "sander"}),
    frozenset({"philip", "phil", "phillip"}),
    frozenset({"patrick", "pat", "paddy", "rick"}),
    frozenset({"raymond", "ray"}),
    frozenset({"walter", "walt", "wally"}),
    frozenset({"eugene", "gene"}),
    frozenset({"vincent", "vince", "vinnie"}),
    frozenset({"douglas", "doug"}),
    frozenset({"russell", "russ"}),
    frozenset({"leonard", "leo", "len", "lenny"}),
    # Women's given names.
    frozenset({"elizabeth", "liz", "lizzie", "beth", "betty", "eliza", "libby"}),
    frozenset({"margaret", "maggie", "meg", "peggy", "marge", "greta"}),
    frozenset({"katherine", "catherine", "kate", "katie", "kathy", "cathy", "kit", "kay"}),
    frozenset({"jennifer", "jen", "jenny", "jenn"}),
    frozenset({"jessica", "jess", "jessie"}),
    frozenset({"patricia", "pat", "patty", "trish", "tricia"}),
    frozenset({"deborah", "deb", "debbie", "debby"}),
    frozenset({"barbara", "barb", "babs"}),
    frozenset({"susan", "sue", "susie", "suzy"}),
    frozenset({"christine", "christina", "chris", "chrissy", "tina"}),
    frozenset({"rebecca", "becca", "becky", "reba"}),
    frozenset({"victoria", "vicky", "vic", "tori"}),
    frozenset({"virginia", "ginny", "ginger"}),
    frozenset({"cynthia", "cindy", "cyn"}),
    frozenset({"dorothy", "dot", "dottie", "dolly"}),
    frozenset({"pamela", "pam"}),
    frozenset({"samantha", "sam", "sammy"}),
    frozenset({"abigail", "abby", "gail"}),
    frozenset({"amanda", "mandy", "amy"}),
    frozenset({"veronica", "ronnie", "vera"}),
    frozenset({"gabrielle", "gabby", "gabi"}),
    frozenset({"stephanie", "steph", "steffi"}),
    frozenset({"theodora", "thea", "dora"}),
    frozenset({"alexandra", "alex", "lexi", "sandra", "sandy"}),
)


def _build_variant_index() -> dict[str, frozenset[str]]:
    """token → the union of every group that contains it (plus the token
    itself). Built once at import; a token in several groups maps to the union
    of them all."""
    index: dict[str, set[str]] = {}
    for group in NICKNAME_GROUPS:
        for token in group:
            index.setdefault(token, set()).update(group)
    return {token: frozenset(members) for token, members in index.items()}


_VARIANT_INDEX = _build_variant_index()


def name_variants(token: str) -> frozenset[str]:
    """Every token interchangeable with `token` (itself included) per
    `NICKNAME_GROUPS`. Unknown tokens map to just `{token}`."""
    token = token.lower()
    return _VARIANT_INDEX.get(token, frozenset({token}))


def _alnum(text: str) -> str:
    """Lowercased, separators/punctuation stripped — the canonical comparison
    form so `jane.doe`, `jane_doe` and `janedoe` collapse to one string."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def parse_name(name: str) -> list[str]:
    """A name string into its lowercase alphanumeric tokens, splitting on any
    separator (space, dot, comma, underscore). `"Troha, David"` →
    `["troha", "david"]`."""
    return [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]


def local_part_patterns(tokens: list[str]) -> set[str]:
    """The email local parts an org might build from this name, across every
    common convention and BOTH name orderings, separator-stripped.

    Systematically derived, never enumerated per person:
      * single token → the token and its nickname variants;
      * two tokens (first, last) → for every (first-variant, last-variant)
        pair: `first`, `first+last`, `last+first`, `finitial+last`,
        `last+finitial`, `first+linitial`, `linitial+first`, and the two
        initials both ways.

    Deliberately NOT included: the bare LAST name alone — it is the one form
    that routinely collides between different people who share a surname, and
    every last-LED *combined* convention (`lastfirst`, `lastf`, `flast`, …) is
    still covered."""
    tokens = [t for t in tokens if t]
    if not tokens:
        return set()
    if len(tokens) == 1:
        out = set()
        for v in name_variants(tokens[0]):
            out.add(v)
        return {_alnum(p) for p in out if p}
    first, last = tokens[0], tokens[-1]
    out: set[str] = set()
    for f in name_variants(first):
        for l in name_variants(last):
            fi, li = f[:1], l[:1]
            out.update({
                f,                    # first (and its variants) alone
                f + l, l + f,         # firstlast / lastfirst
                fi + l, l + fi,       # flast / lastf   (last-led: trohad)
                f + li, li + f,       # firstl / lfirst
                fi + li, li + fi,     # initials, both orders
            })
    return {_alnum(p) for p in out if p}


def full_name_forms(tokens: list[str]) -> set[str]:
    """The subset of `local_part_patterns` that use the WHOLE name (both tokens,
    with variants) — `firstlast` / `lastfirst` and their nickname expansions.
    The identity signature used for name↔name matching and as the fuzzy
    comparison target. For a single-token name this falls back to that token's
    variants so the return is never empty."""
    tokens = [t for t in tokens if t]
    if len(tokens) < 2:
        return local_part_patterns(tokens)
    first, last = tokens[0], tokens[-1]
    out: set[str] = set()
    for f in name_variants(first):
        for l in name_variants(last):
            out.add(_alnum(f + l))
            out.add(_alnum(l + f))
    return {p for p in out if p}


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_name(
    target: str,
    candidates: Iterable[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Optional[str]:
    """Return the candidate string that refers to the same person as `target`,
    or None. Candidates may be email addresses or bare display names, freely
    mixed — an `@` decides which comparison a candidate gets. The RAW candidate
    (original casing) is returned so a caller can tell an address from a name.

    Evaluated in tiers, best/most-certain first: (1) exact local-part
    convention on email candidates, (2) name↔name identity on bare-name
    candidates, (3) calibrated fuzzy fallback. AMBIGUITY DECLINES: at the best
    tier that produces any match, exactly one distinct candidate resolves; two
    or more distinct candidates tie → None. It does NOT fall through to a lower
    tier to break a tie. This is what makes the result order-independent and
    keeps the decline-not-fabricate contract when a nickname legitimately
    expands into two different people (e.g. "Jay" → James AND Jason)."""
    target_tokens = parse_name(target)
    if not target_tokens:
        return None
    patterns = local_part_patterns(target_tokens)
    target_full = full_name_forms(target_tokens)

    # De-dupe candidates by canonical (lowercased) form up front so a repeated
    # address is one candidate, not a false ambiguity — while keeping the first
    # raw spelling for the return value. Order-independence follows from
    # counting DISTINCT candidates at a tier rather than returning on first hit.
    unique: dict[str, str] = {}
    for candidate in candidates:
        raw = (candidate or "").strip()
        if not raw:
            continue
        unique.setdefault(raw.lower(), raw)

    # Tier 1 — exact local-part convention (email candidates).
    tier1 = [
        raw for key, raw in unique.items()
        if "@" in key and (_alnum(key.split("@", 1)[0]) in patterns)
        and _alnum(key.split("@", 1)[0])
    ]
    if tier1:
        return tier1[0] if len(tier1) == 1 else None

    # Tier 2 — name↔name identity (bare-name candidates).
    tier2 = [
        raw for key, raw in unique.items()
        if "@" not in key and (full_name_forms(parse_name(key)) & target_full)
    ]
    if tier2:
        return tier2[0] if len(tier2) == 1 else None

    # Tier 3 — calibrated fuzzy fallback. A comparison string per candidate
    # (email local part, or the whole bare name), scored against the expanded
    # full-name forms. Decline if nothing clears the floor, OR if the top two
    # both clear it within a hair of each other (same ambiguity principle).
    scored: list[tuple[float, str]] = []
    for key, raw in unique.items():
        comp = _alnum(key.split("@", 1)[0]) if "@" in key else _alnum(key)
        ratio = max((_ratio(comp, form) for form in target_full), default=0.0)
        scored.append((ratio, raw))
    scored.sort(key=lambda t: -t[0])
    if not scored or scored[0][0] < threshold:
        return None
    if len(scored) > 1 and scored[1][0] >= threshold \
       and (scored[0][0] - scored[1][0]) <= _FUZZY_AMBIGUITY_MARGIN:
        return None  # two near-equal fuzzy matches → decline rather than guess
    return scored[0][1]
