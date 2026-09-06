"""Prose attached in chat, read as evidence — and still written nowhere.

WHAT THE TESTS BELOW ARE ACTUALLY DEFENDING. A run-scoped document is not
"one more source"; every decision about it is a decision about how the
refutation rules will see it, and each of those decisions has a failure mode
that produces a plausible-looking run rather than an error:

  * artifact identity too coarse -> every prose-only cluster dies as `echo`,
    and the run reports having read the document while finding nothing in it;
  * artifact identity EMPTY -> `pipeline._refute` declines the echo rule for
    the whole cluster, so the rule passes VACUOUSLY and the run claims a check
    it never ran;
  * a mis-split accepted silently -> findings with confidently wrong
    provenance, which is worse than the per-file answer it should fall back to;
  * no strength ceiling -> a document the reader wrote themselves sizes a
    finding as measured fact, outranking the connected corpus;
  * prose claims not persisted -> a stalled-enrichment sweep rebuilds a
    smaller claim set and `recommend`'s claim-id gate drops the citations
    naming them, leaving prose-backed findings whose recommendations cite
    nothing.

Every one of those is a test here, and each is written so it FAILS when the
mechanism is removed rather than only when an import breaks — the paired
"…and here is what a careless version would do" assertions are the point.

Pure and offline: no model call, no database, no network. Extraction items are
constructed directly, because what is under test is what the engine does WITH
them, not the model that produced them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.crucible import prose
from app.crucible.claims import project_signals
from app.crucible.kg_themes import assign_themes
from app.crucible.pipeline import build_findings

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
CO = "00000000-0000-0000-0000-0000000000c0"


# ─── Fixtures: three conversations, one topic, three named accounts ─────────
#
# SHAPED TO MAKE THE ECHO RULE DECIDABLE, and nothing else about it is
# incidental. `_refute` fires `echo` only when a cluster has at least
# MIN_CLAIMS_PER_FINDING claims, spans less than ECHO_WINDOW, and every claim
# names the SAME artifact — so the corpus below has three claims, three days
# apart, on one theme. Three different accounts keeps `single_account` out of
# the way; `customer_voice` + `feature_request` (-> `preference`) keeps
# `no_authority` out of the way. What is left varying between the two arms is
# artifact identity, which is the whole experiment.


def _item(content: str, account: str, *, theme: str = "Bulk export speed",
          kind: str = "feature_request", source_type: str = "customer_voice",
          **props) -> dict:
    return {
        "kind": kind,
        "content": content,
        "source_type": source_type,
        "theme": theme,
        "relationship": "REQUESTS",
        "properties": {"account": account, **props},
    }


CALLS = [
    ("Northwind renewal call", datetime(2026, 8, 20, tzinfo=timezone.utc),
     _item("Northwind said the bulk export takes over an hour to finish.",
           "Northwind Logistics")),
    ("Contoso discovery call", datetime(2026, 8, 22, tzinfo=timezone.utc),
     _item("Contoso said their team gave up waiting on the bulk export.",
           "Contoso Manufacturing")),
    ("Fabrikam QBR", datetime(2026, 8, 24, tzinfo=timezone.utc),
     _item("Fabrikam asked for the bulk export to run in the background.",
           "Fabrikam Industries")),
]


def _rows(*, per_conversation: bool, file_name: str = "customer_calls.pdf"):
    """The same three extraction items under two artifact-identity schemes.

    ONE FUNCTION, ONE VARIABLE. The items, the dates, the accounts and the
    theme are byte-identical between the two arms; only `doc_name` moves. A
    difference in the ledger is therefore attributable to artifact identity and
    to nothing else, which is the only way this comparison means anything.
    """
    rows: list[dict] = []
    items_by_id: dict[str, dict] = {}
    for title, observed_at, item in CALLS:
        artifact_id = f"{file_name}#{title}" if per_conversation else file_name
        built = prose._signals_for(
            [item], artifact_id=artifact_id, enterprise_id=CO,
            observed_at=observed_at)
        for sig in built:
            items_by_id[sig.id] = item
            rows.append(prose.signal_to_row(sig, created_at=NOW))
    return rows, items_by_id


def _run(rows, items_by_id, *, graph_theme_map=None):
    """Rows -> claims -> the graph's grouping -> the real pipeline."""
    claims, stats = project_signals(rows)
    theme_map = prose.theme_map_for(items_by_id, graph_theme_map or {})
    grouped, unthemed, _ = assign_themes(list(claims), theme_map)
    result = build_findings(grouped, currency="accounts", now=NOW)
    return grouped, unthemed, result, stats


# ─── 1. Artifact identity is the whole experiment ───────────────────────────


def test_per_conversation_identity_survives_refutation_where_per_file_dies():
    """THE MEASUREMENT THIS FEATURE RESTS ON, at test scale.

    On a real ten-call transcript, per-file identity produced 3 findings and
    killed 12 clusters as `echo`; per-conversation produced 7 and killed 8. The
    corpus here is three calls rather than ten, and the mechanism is identical:
    one document is one conversation echoing, three conversations are a pattern.
    """
    per_file = _run(*_rows(per_conversation=False))[2]
    per_conv = _run(*_rows(per_conversation=True))[2]

    assert len(per_conv.findings) > len(per_file.findings)
    # AND FOR THE STATED REASON, not for some other one that happens to differ.
    # Without this the test would pass just as happily if per-file died at
    # `single_account`, and would tell us nothing about the echo rule.
    assert (per_file.stats["dropped"].get("echo") or 0) >= 1
    assert (per_conv.stats["dropped"].get("echo") or 0) == 0


def test_the_two_arms_differ_in_nothing_but_the_artifact_id():
    """The control for the test above.

    A comparison between two arms is worth exactly as much as the claim that
    they are otherwise identical — so that claim is asserted rather than
    assumed. Same assertions, same dates, same accounts, same claim types,
    same authority; different documents.
    """
    file_claims = _run(*_rows(per_conversation=False))[0]
    conv_claims = _run(*_rows(per_conversation=True))[0]

    def _shape(claims):
        return sorted(
            (c.assertion, c.type, c.strength, c.authoritative,
             c.observed_at.isoformat(), tuple(sorted(c.population.segments.get('accounts', ()))))
            for c in claims
        )

    assert _shape(file_claims) == _shape(conv_claims)
    assert len({c.artifact_id for c in file_claims}) == 1
    assert len({c.artifact_id for c in conv_claims}) == 3


def test_two_conversations_saying_the_same_sentence_are_two_claims():
    """A CONTENT-KEYED ID WOULD COLLAPSE CORROBORATION INTO AN ECHO.

    The graph keys a signal id on `enterprise_id|content` so a re-sync cannot
    duplicate a fact — right there, wrong here: two different customers
    independently saying one sentence is the repetition the engine is looking
    for. `prose.artifact_namespace` puts the conversation in the NAMESPACE,
    which is what keeps them apart.
    """
    said = _item("The bulk export is too slow.", "Northwind Logistics")
    a = prose._signals_for([said], artifact_id="calls.pdf#one",
                           enterprise_id=CO, observed_at=NOW)[0]
    b = prose._signals_for([said], artifact_id="calls.pdf#two",
                           enterprise_id=CO, observed_at=NOW)[0]
    assert a.id != b.id
    # ...and the same sentence in the SAME conversation is still one claim,
    # which is the half of the graph's behaviour worth keeping.
    again = prose._signals_for([said], artifact_id="calls.pdf#one",
                               enterprise_id=CO, observed_at=NOW)[0]
    assert a.id == again.id


def test_a_prose_claim_id_can_never_collide_with_a_graph_signal_id():
    """A CARELESS VERSION REUSES THE EXTRACTOR'S NAMESPACE, AND THIS IS WHAT
    THAT COSTS.

    The graph's id is `uuid5(_NS, "<company>|<content>")`. A sentence in an
    attached file that also exists verbatim in `kg_signal` would derive that
    exact id, and the two would then be one key in every `claims_by_id` map
    downstream — a run-scoped claim standing in for a stored one, or being
    overwritten by it, with nothing anywhere to say it happened.
    """
    import uuid as _uuid

    from app.graph.extractor import _NS

    said = "The bulk export is too slow."
    graph_id = str(_uuid.uuid5(_NS, f"{CO}|{said}"))
    run_scoped = prose._signals_for(
        [_item(said, "Northwind Logistics")], artifact_id="calls.pdf",
        enterprise_id=CO, observed_at=NOW)[0]
    assert run_scoped.id != graph_id


# ─── 2. A split is used only when the document verifies it ──────────────────

_HEADER = "Customer calls · 3 calls · exported 2026-08-25\n\n"


def _transcript(n: int, *, stated: int | None = None, dated: bool = True,
                titled: bool = True) -> str:
    head = (f"Customer calls · {stated} calls · exported 2026-08-25\n\n"
            if stated is not None else "Customer calls\n\n")
    out = [head]
    for i in range(n):
        title = f"Call with {['Northwind', 'Contoso', 'Fabrikam', 'AdventureWorks'][i % 4]}"
        out.append(f"{title if titled else ''}\n")
        out.append(f"Date:\t{'2026-08-%02d' % (20 + i) if dated else 'unknown'}\n")
        out.append("Type:\tdiscovery\n\n")
        out.append(f"They said the bulk export is slow. " * 8 + "\n\n")
    return "".join(out)


#: THE REAL DOCUMENT'S OPENING, STRUCTURE FOR STRUCTURE.
#:
#: EVERY CHARACTER OF THIS SHAPE IS LOAD-BEARING and none of it is decoration:
#: the `## Page 1` header (so there is a bare number in the text), the BLANK
#: line after it (so `\s` can span it), a heading whose first word begins with
#: a letter (so `\d+\s+word` completes a match), and the TAB before "calls"
#: on a later line (so a `[ ]`-only fix would still fail here).
#:
#: A synthetic transcript without a page header passes either way and proves
#: nothing — which is exactly what the first version of this suite did, and it
#: is why the defect below reached a real document before anything caught it.
#: The tenant name is the repo's synthetic convention; the name is not what
#: causes the bug, the line structure is.
_REAL_HEADER = (
    "## Page 1\n"
    "\n"
    "Call Transcripts \u2014 Rolling 90 Days\n"
    "Northwind \u00b7 Gong export \u00b7 10\tcalls \u00b7 "
    "2026-06-30 to 2026-09-04\n"
)


def _real_shape_document(n: int = 10) -> str:
    """`_REAL_HEADER` plus `n` tab-separated per-call headers across pages.

    Titles REPEAT, because they do: "Call with Northwind — renewal" recurring
    across a quarter is the normal case for a rolling export, and a document
    where every title happened to be unique would hide the id collision this
    also pins.
    """
    accounts = ["Northwind", "Contoso", "Fabrikam", "AdventureWorks"]
    out = [_REAL_HEADER, "\n"]
    for i in range(n):
        if i and i % 4 == 0:
            out.append(f"## Page {i // 4 + 1}\n\n")
        out.append(f"Call with {accounts[i % 4]} \u2014 renewal\n")
        out.append(f"Date:\t2026-0{7 + i // 5}-{10 + i:02d}\n")
        out.append("Type:\tdiscovery\n\n")
        out.append("They said the bulk export is slow. " * 10 + "\n\n")
    return "".join(out)


def test_a_page_number_is_not_the_documents_count_of_its_conversations():
    """THE DEFECT A SYNTHETIC FIXTURE MISSED, AND IT COST THE WHOLE FEATURE.

    `_STATED_COUNT` used `\\s+`, and `\\s` matches a NEWLINE. On the real
    document `1\\n\\nCall` matched — the page number, the blank line, and the
    first word of the heading — and `search()` returns the FIRST match, so the
    document's own count parsed as 1 when it says 10.

    Nothing errored. The split found all ten headers correctly, "disagreed"
    with a number the document never stated, and fell back to one whole-file
    segment dated to the run clock: 3 findings where there should have been 7.
    The guard was right, the disclosure was accurate, and the answer was wrong.
    """
    assert prose._STATED_COUNT.search(_REAL_HEADER).group(1) == "10"
    # AND ONLY ONE THING IN THAT HEADER LOOKS LIKE A COUNT. Asserting the
    # parsed value alone would still pass a pattern that matched twice and
    # happened to order them favourably.
    assert len(prose._STATED_COUNT.findall(_REAL_HEADER)) == 1


def test_no_pattern_here_may_match_across_a_line_boundary():
    """THE DEFECT CLASS, NOT THE INSTANCE.

    Everything this module parses is positional — a count in a header line, a
    date on its label line, a title on the line above. A pattern permitted to
    span a newline reaches into the next line and takes whatever is there, and
    it does it silently. Swept over every compiled pattern in the module so a
    fourth one added later is covered by construction rather than by memory.
    """
    probe = ("## Page 1\n\nCall Transcripts\nDate:\n2026-08-20\n"
             "Northwind \u00b7 10\tcalls \u00b7\n")
    for name in ("_DATE_LABEL", "_ISO_DATE", "_STATED_COUNT"):
        pattern = getattr(prose, name)
        spanning = [m.group(0) for m in pattern.finditer(probe)
                    if "\n" in m.group(0)]
        assert not spanning, f"{name} matched across a line: {spanning!r}"


def test_the_real_document_shape_splits_into_ten_dated_conversations():
    """THE WHOLE THING, over the shape that actually broke it.

    Ten headers, the document's own count agreeing, ten distinct dates read
    from ten different header lines, and ten distinct artifact ids.
    """
    doc = _real_shape_document(10)
    segments, per_conversation, markers, stated, fallback = prose.segment(
        doc, name="calls.pdf", now=NOW)

    assert (len(segments), markers, stated, per_conversation, fallback) == (
        10, 10, 10, True, "")
    assert len({s.observed_at for s in segments}) == 10

    d = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=len(doc), segments=segments,
        per_conversation=per_conversation, markers_found=markers,
        stated_count=stated)
    ids = [d.artifact_id_for(s) for s in segments]
    assert len(set(ids)) == 10, ids
    assert all(ids)
    assert d.how_it_was_read.startswith("read as 10 separate conversations")
    assert "the document says it holds 10, and it does" in d.how_it_was_read


def test_two_conversations_sharing_a_title_are_two_artifacts():
    """A TITLE-KEYED ID MERGES THEM, AND THE MERGE IS THE FAILURE.

    Two calls with the same account in one rolling export carry the same
    title. Collapsing them to one artifact hands the echo rule a cluster built
    from two separate conversations and tells it they came from one document —
    which is the exact collapse per-conversation identity exists to prevent,
    arriving through the back door. Disambiguated on collision only, the way
    `recon.read_uploads` already disambiguates two attachments sharing a
    filename, so the first occurrence keeps a name a citation can print.
    """
    doc = _real_shape_document(10)
    segments, per_conversation, markers, stated, _f = prose.segment(
        doc, name="calls.pdf", now=NOW)
    d = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=len(doc), segments=segments,
        per_conversation=per_conversation, markers_found=markers,
        stated_count=stated)
    ids = [d.artifact_id_for(s) for s in segments]
    assert ids[0] == "calls.pdf#Call with Northwind \u2014 renewal"
    assert ids[4] == "calls.pdf#Call with Northwind \u2014 renewal (2)"
    assert ids[8] == "calls.pdf#Call with Northwind \u2014 renewal (3)"


def test_a_date_is_read_from_the_field_and_not_from_the_neighbourhood():
    """SAME CLASS AS THE COUNT BUG, ONE FIELD OVER — AND THE FIRST VERSION OF
    THIS TEST DID NOT PROVE IT.

    The wrap fallback exists for one reason: a PDF text layer sometimes puts
    `Date:` and its value on separate lines. It was written as a 3-line window
    scanned for the first date-shaped thing, which is wider than that reason —
    it takes a date out of ANY nearby line and stamps the conversation with it.

    THE HONEST HISTORY, because it is the point. A first attempt at this
    "preferred the label's own value", which changed nothing at all:
    `_plausible_date` scans forward and the label line was already first in the
    window, so the preference preferred something that had already won. The
    mutation that should have caught it did not, and the comment above it
    claimed a fix that had not been made. What follows tests the narrowing that
    actually changes behaviour.
    """
    body = "They said the export is slow. " * 10

    # A. The ordinary case: the value is on the label line.
    segments, per_conversation, *_rest = prose.segment(
        f"Call one\nDate:\t2026-08-20\n\n{body}\n\n"
        f"Call two\nDate:\t2026-08-25\n\n{body}\n", name="c.pdf", now=NOW)
    assert per_conversation is True
    assert [s.observed_at.date().isoformat() for s in segments] == [
        "2026-08-20", "2026-08-25"]

    # B. The case the fallback is FOR: the value wrapped onto the next line.
    segments, per_conversation, *_rest = prose.segment(
        f"Call one\nDate:\n2026-08-20\n\n{body}\n\n"
        f"Call two\nDate:\n2026-08-25\n\n{body}\n", name="c.pdf", now=NOW)
    assert per_conversation is True
    assert [s.observed_at.date().isoformat() for s in segments] == [
        "2026-08-20", "2026-08-25"]

    # C. THE ONE THAT MATTERS: a bare label, and a date on the NEXT FIELD.
    # Taking it would date this call from a sentence about something else and
    # leave nothing in the output to show for it. Refusing it leaves the
    # segment undated, and `segment` then declines to split the document at
    # all — visible, disclosed, and correct.
    segments, per_conversation, _m, _st, fallback = prose.segment(
        f"Call one\nDate:\nType:\tdiscovery \u2014 agreed 2026-09-01\n{body}\n\n"
        f"Call two\nDate:\t2026-08-25\n\n{body}\n", name="c.pdf", now=NOW)
    assert per_conversation is False
    assert "carry no date" in fallback

    # D. AND THE SEARCH STOPS AT THE FIRST NON-EMPTY LINE, which is what makes
    # this a read of one field rather than a scan of the neighbourhood. A
    # wrapped value is always the line immediately after its label; a line two
    # down that merely STARTS with a date is a sentence, not the value.
    segments, per_conversation, _m, _st, fallback = prose.segment(
        f"Call one\nDate:\nType:\tdiscovery\n2026-09-01 was the follow-up\n"
        f"{body}\n\nCall two\nDate:\t2026-08-25\n\n{body}\n",
        name="c.pdf", now=NOW)
    assert per_conversation is False
    assert "carry no date" in fallback


def test_a_split_that_is_not_corroborated_says_so():
    """THE THIRD CASE, WHICH USED TO BE SILENT.

    A document with per-call headers and no count of its own IS split — an
    absent count is not a disagreement — but nothing corroborates that split,
    and the sentence has to say which of the two it is. Otherwise a checked
    claim and an unchecked one wear identical words.
    """
    doc = _real_shape_document(3).replace("10\tcalls", "calls")
    segments, per_conversation, markers, stated, _f = prose.segment(
        doc, name="calls.pdf", now=NOW)
    assert (per_conversation, stated) == (True, None)
    d = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=len(doc), segments=segments,
        per_conversation=per_conversation, markers_found=markers,
        stated_count=stated)
    assert "states no count of its own" in d.how_it_was_read
    assert "nothing corroborates that split" in d.how_it_was_read


def test_a_split_that_matches_the_documents_own_count_is_used():
    segments, per_conversation, markers, stated, fallback = prose.segment(
        _transcript(3, stated=3), name="calls.pdf", now=NOW)
    assert per_conversation is True
    assert (len(segments), markers, stated, fallback) == (3, 3, 3, "")
    assert [s.observed_at.date().isoformat() for s in segments] == [
        "2026-08-20", "2026-08-21", "2026-08-22"]


def test_a_split_that_disagrees_with_the_documents_own_count_falls_back():
    """AND SAYS SO. A silent mis-split produces findings whose provenance is
    confidently wrong; the per-file answer is less precise and true."""
    segments, per_conversation, markers, stated, fallback = prose.segment(
        _transcript(3, stated=10), name="calls.pdf", now=NOW)
    assert per_conversation is False
    assert len(segments) == 1
    assert (markers, stated) == (3, 10)
    # THE DISCLOSURE NAMES BOTH NUMBERS. "I read it as one document" on its own
    # is not a disclosure — a reader can act on "it says 10 and I found 3".
    assert "10" in fallback and "3" in fallback


def test_the_fallback_reason_reaches_the_sentence_the_reader_sees():
    doc = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=100,
        segments=(prose.ProseSegment(0, "", NOW, "t"),),
        per_conversation=False, markers_found=3, stated_count=10,
        fallback_reason="it says it holds 10 conversations and 3 headers "
                        "were found, so splitting it would be a guess",
    )
    said = doc.how_it_was_read
    assert said.startswith("read as one document, because")
    assert "10" in said and "3" in said
    # AND THE VERIFIED CASE SAYS IT WAS VERIFIED, which is the sentence a
    # reader can disagree with.
    verified = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=100,
        segments=tuple(prose.ProseSegment(i, f"c{i}", NOW, "t")
                       for i in range(3)),
        per_conversation=True, markers_found=3, stated_count=3,
    )
    assert "3 separate conversations" in verified.how_it_was_read
    assert "the document says it holds 3, and it does" in verified.how_it_was_read
    assert "split at the per-call headers" in verified.how_it_was_read


def test_an_undated_conversation_sends_the_whole_file_back_to_one_segment():
    """THE DATE AND THE BOUNDARY COME FROM ONE MARKER OR FROM NEITHER.

    Per-conversation ids with `now()` on the claims leaves decay wrong and lets
    a two-year-old document outrank last week's Slack — the ids would look
    right and the ranking would be wrong, which is the hardest kind of defect
    to see from the output.
    """
    _segments, per_conversation, _m, _s, fallback = prose.segment(
        _transcript(3, stated=3, dated=False), name="calls.pdf", now=NOW)
    assert per_conversation is False
    assert "no date" in fallback


def test_a_document_with_no_headers_at_all_is_read_whole_and_dated_from_itself():
    text = ("A memo about the export.\n\nWe reviewed the pipeline on "
            "2026-07-01 and again on 2026-08-15.\n" + "Detail. " * 60)
    segments, per_conversation, markers, stated, fallback = prose.segment(
        text, name="memo.docx", now=NOW)
    assert (per_conversation, markers, stated) == (False, 0, None)
    assert "no per-conversation headers" in fallback
    # THE NEWEST DATE, NOT THE FIRST. A document that discusses a history is
    # observed at the end of it; dating it to the oldest date it mentions would
    # decay the whole file from a day none of its evidence was gathered on.
    assert segments[0].observed_at.date().isoformat() == "2026-08-15"


def test_a_date_outside_the_plausible_band_is_not_read_as_an_observation():
    """A `\\d{4}-\\d{2}-\\d{2}` match can be a part number or a future-dated
    plan. Accepting one makes a claim look fresher than any evidence in the
    corpus, which is a silent, permanent overstatement."""
    text = ("Roadmap memo. Target ship 2031-04-01, legacy SKU 0007-01-01.\n"
            + "Detail. " * 60)
    segments, *_ = prose.segment(text, name="memo.docx", now=NOW)
    assert segments[0].observed_at is None


# ─── 3. An artifact id is never empty ───────────────────────────────────────


def test_a_segment_with_no_title_falls_back_to_the_file_and_never_to_empty():
    doc = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=100,
        segments=(prose.ProseSegment(0, "", NOW, "t"),),
        per_conversation=True, markers_found=1, stated_count=1,
    )
    assert doc.artifact_id_for(doc.segments[0]) == "calls.pdf"


def test_read_prose_never_produces_an_empty_artifact_id():
    """The end-to-end version of the assertion above, over a document whose
    FIRST header has nothing before it to take a title from — the one shape
    that genuinely produces an untitled segment, since every later header can
    at worst fall back to the preceding call's text."""
    body = "They said the bulk export is slow. " * 8
    data = ("Date:\t2026-08-20\nType:\tdiscovery\n\n" + body
            + "\n\nCall with Contoso\nDate:\t2026-08-21\n\n"
            + body).encode()
    docs, unread = prose.read_prose([("calls.txt", data)], now=NOW)
    assert docs and not unread
    doc = docs[0]
    assert doc.per_conversation is True
    # THE UNTITLED SEGMENT IS ACTUALLY PRESENT — without this the test would
    # pass over a document where every header happened to have a title, and
    # would be asserting nothing about the fallback.
    assert doc.segments[0].title == ""
    ids = [doc.artifact_id_for(s) for s in doc.segments]
    assert all(ids), ids
    assert ids[0] == "calls.txt"


def test_an_empty_artifact_id_would_switch_the_echo_rule_off_entirely():
    """THE MUTATION PROOF FOR THE RULE ABOVE, RUN AGAINST THE REAL `_refute`.

    This is what a careless fallback to `""` actually buys: not a slightly
    worse provenance string, but a cluster on which the echo rule DECLINES TO
    RUN — `fully_attributed` is False, so the check the report says it
    performed never happens and every finding in that cluster passes it
    vacuously. Asserted here against the production rule rather than described
    in a comment, because a comment cannot fail.
    """
    from dataclasses import replace

    from app.crucible.pipeline import _refute

    claims = _run(*_rows(per_conversation=False))[0]
    from app.crucible.pipeline import _accounts
    accounts = list(_accounts(claims))
    assert _refute(claims, accounts) is not None
    assert _refute(claims, accounts).code == "echo"

    blanked = [replace(c, artifact_id="") for c in claims]
    assert _refute(blanked, accounts) is None


# ─── 4. The transport caps the strength; the witness sets the type ──────────


def _one_row(*, source_type: str, kind: str, attachment: bool) -> dict:
    provenance = {"source": "extractor", "doc": "book.pdf"}
    if attachment:
        provenance["channel"] = prose.ATTACHMENT_CHANNEL
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "kind": kind,
        "source_type": source_type,
        "content": "Weekly active users fell 18% after the export change.",
        "properties": {"account": "Northwind Logistics"},
        "provenance": provenance,
        "valid_at": "2026-08-20T00:00:00+00:00",
        "created_at": "2026-09-06T00:00:00+00:00",
        "source_id": None,
    }


def test_an_attachment_claim_is_capped_at_reported_strength():
    """A DOCUMENT THE READER WROTE MAY NOT SIZE A FINDING AS MEASURED FACT.

    `analytics` + `metric_anomaly` is the strongest thing the projection can
    produce short of an experiment platform: authoritative for `magnitude`, and
    `measured` by `DEFAULT_STRENGTH`. Arriving through a connector that is
    exactly right — the tenant wired the platform up, and every row it produces
    carries that standing. Arriving inside one chat message it is a number in a
    file, and nothing in the bytes says which.
    """
    claim, = project_signals(
        [_one_row(source_type="analytics", kind="metric_anomaly",
                  attachment=True)])[0]
    assert claim.strength == "reported"


def test_the_identical_row_without_the_attachment_marker_stays_measured():
    """THE MUTATION PROOF FOR THE CAP. Removing the cap makes the test above
    pass for the wrong reason — this is the row that tells the two apart, and
    it is the SAME row but for one provenance key."""
    claim, = project_signals(
        [_one_row(source_type="analytics", kind="metric_anomaly",
                  attachment=False)])[0]
    assert claim.strength == "measured"


def test_the_cap_does_not_take_the_authority_with_it():
    """A CEILING, NOT A REFUSAL, and the difference decides whether the feature
    does anything at all. Dropping authority as well would kill every
    upload-only cluster at `no_authority` and hand back a run that read the
    document and found nothing in it."""
    claim, = project_signals(
        [_one_row(source_type="analytics", kind="metric_anomaly",
                  attachment=True)])[0]
    assert claim.type == "magnitude"
    assert claim.authoritative is True


def test_nothing_pins_the_witness_type_of_an_attachment():
    """THE SOURCE TYPE IS THE WITNESS AND "UPLOAD" IS A TRANSPORT.

    The measured corpus had the extractor choose `customer_voice` for 62 of 68
    claims unprompted, and `no_authority` fired zero times — so pinning a
    source type here would be overriding a correct answer with a guess. What
    the projection sees is whatever the model said.
    """
    built = prose._signals_for(
        [_item("Contoso wants background exports.", "Contoso Manufacturing",
               source_type="communication")],
        artifact_id="calls.pdf#one", enterprise_id=CO, observed_at=NOW)
    assert built[0].source_type == "communication"
    assert built[0].provenance["channel"] == prose.ATTACHMENT_CHANNEL


def test_the_writer_and_the_reader_of_the_attachment_marker_agree():
    """TWO MODULES, ONE STRING. `prose` writes `provenance["channel"]` and
    `claims` reads it; a rename on one side alone silently removes the cap and
    nothing else changes, which no other test in this file would notice."""
    from app.crucible.claims import ATTACHMENT_CHANNEL

    assert prose.ATTACHMENT_CHANNEL == ATTACHMENT_CHANNEL


# ─── 5. Grouping: the graph's themes first, for prose too ───────────────────


def test_prose_claims_join_the_graph_theme_that_names_the_same_subject():
    """OTHERWISE THE FEATURE DELIVERS NOTHING, SILENTLY.

    A prose claim's id is not in `kg_signal`, so `load_theme_map` cannot know
    it and `_load_embeddings` has no vector for it — every one of them would
    land in `assign_clusters` with nothing to cluster on, one pseudo-group
    each, and the anecdote rule would drop all of them. A run that read a
    document and produced nothing from it looks exactly like a document with
    nothing in it.
    """
    graph = {"sig-1": ("entity-billing", "Bulk export speed", "SUPPORTS")}
    rows, items_by_id = _rows(per_conversation=True)
    mapped = prose.theme_map_for(items_by_id, graph)
    assert {v[0] for v in mapped.values()} == {"entity-billing"}
    assert {v[1] for v in mapped.values()} == {"Bulk export speed"}


def _graph(*themes) -> dict:
    """`load_theme_map`-shaped map: signal id -> (entity id, label, verb).

    ONE ENTRY PER SIGNAL, which is the shape the real function returns and the
    reason a theme's citation count is derivable from it at all. A theme
    carrying 51 signals is 51 entries pointing at one entity id.
    """
    out = {}
    for entity_id, label, n in themes:
        for i in range(n):
            out[f"{entity_id}-sig{i}"] = (entity_id, label, None)
    return out


def test_a_prose_label_joins_the_theme_the_graph_leaned_on_not_the_first_one():
    """MEASURED BIAS, NOT A TIE-BREAK.

    On a real tenant's 2,129 theme labels an extractor's "pricing" had 52
    candidates qualifying under `same_topic`, and "tabletop exercise
    scheduling" had 27 — so which candidate wins is doing real work on nearly
    every attachment rather than settling the occasional draw. Sorting
    alphabetically skews hard toward `A`, and on a graph that also carries
    meeting-assistant themes an attached pricing discussion attached to
    ANOTHER PRODUCT'S pricing theme.

    That failure is invisible in the output: the claim is not dropped and not
    flagged, it is counted and cited under the wrong theme. So the rule is
    `canonicalize_themes`' own — most-cited, ties on the entity id.

    BOTH CANDIDATES HERE QUALIFY, and they disagree: the thin one sorts first
    alphabetically, the heavy one is the topic's real home. A fixture with one
    candidate passes under either rule and proves nothing.
    """
    from app.crucible.kg_themes import content_tokens, same_topic

    graph = _graph(("e-ai-platform", "AI tabletop exercise platform", 5),
                   ("e-tabletop", "Tabletop exercise programme", 51))
    probe = content_tokens("tabletop exercise scheduling")
    # THE PRECONDITION, ASSERTED. If only one of these qualified the test would
    # be pinning the overlap rule, not the ordering.
    assert same_topic(probe, content_tokens("AI tabletop exercise platform"))
    assert same_topic(probe, content_tokens("Tabletop exercise programme"))
    # ...and they really do disagree, so the assertion below has something to
    # decide. Alphabetically the thin `AI ...` label comes first.
    assert sorted(["AI tabletop exercise platform",
                   "Tabletop exercise programme"])[0].startswith("AI")

    mapped = prose.theme_map_for(
        {"s1": {"theme": "tabletop exercise scheduling",
                "relationship": "REQUESTS"}}, graph)
    assert mapped["s1"][0] == "e-tabletop"
    assert mapped["s1"][1] == "Tabletop exercise programme"


def test_the_exact_match_tier_picks_by_citations_too():
    """THE SAME DEFECT ONE TIER UP, which a fix to the overlap loop alone
    would leave in place.

    Two entities whose labels normalise identically is the ordinary case the
    theme fold exists for — 'Pricing' and 'pricing' are separate rows in the
    graph today purely because of capitalisation. The exact-match tier picks
    among them with `setdefault` over the candidate list, so it inherits
    whatever ordering that list has; under alphabetical it took the thin one.
    """
    graph = _graph(("e-thin", "PRICING", 3), ("e-home", "pricing", 40))
    assert sorted(["PRICING", "pricing"])[0] == "PRICING"  # the thin one

    mapped = prose.theme_map_for(
        {"s1": {"theme": "Pricing", "relationship": "AFFECTS"}}, graph)
    assert mapped["s1"][0] == "e-home"


def test_the_attachment_is_the_same_on_two_identical_runs():
    """THE TIE-BREAK'S OWN JOB. Most-cited alone leaves equal-count candidates
    decided by dict order, so the same corpus would attach differently
    depending on which page a batch landed on — and the same evidence would
    yield different findings on different days. Ties go to the entity id,
    exactly as `canonicalize_themes` does it."""
    from app.crucible.kg_themes import content_tokens, same_topic

    graph = _graph(("e-zzz", "Billing invoicing platform", 9),
                   ("e-aaa", "Invoicing billing workflow", 9))
    items = {"s1": {"theme": "billing and invoicing errors",
                    "relationship": "AFFECTS"}}
    # THE PRECONDITION. Both must QUALIFY and their counts must be EQUAL, or
    # the tie-break is never consulted and this passes without it — which is
    # exactly what the first version of this test did.
    probe = content_tokens("billing and invoicing errors")
    assert same_topic(probe, content_tokens("Billing invoicing platform"))
    assert same_topic(probe, content_tokens("Invoicing billing workflow"))

    first = prose.theme_map_for(items, graph)
    assert first["s1"][0] == "e-aaa"  # the lower id, not the first-inserted
    # SAME MAP, OPPOSITE ITERATION ORDER — which is what a different page of a
    # paged read hands you.
    reversed_graph = dict(reversed(list(graph.items())))
    for _ in range(5):
        assert prose.theme_map_for(items, reversed_graph) == first


def test_a_topic_the_graph_has_never_seen_gets_its_own_group_not_none():
    rows, items_by_id = _rows(per_conversation=True)
    mapped = prose.theme_map_for(items_by_id, {})
    ids = {v[0] for v in mapped.values()}
    assert len(ids) == 1 and all(ids)
    # AND IT IS NOT MISTAKEABLE FOR A REAL ENTITY. A synthetic id colliding
    # with a `kg_entity` id would silently merge run-scoped evidence into a
    # stored theme nobody asked it to join.
    assert ids != {"entity-billing"}


def test_every_prose_claim_is_grouped_rather_than_ungroupable():
    rows, items_by_id = _rows(per_conversation=True)
    _grouped, unthemed, result, _stats = _run(rows, items_by_id)
    assert unthemed == []
    assert (result.stats["dropped"].get("ungroupable") or 0) == 0


def test_without_the_prose_theme_map_the_document_contributes_nothing():
    """THE MUTATION PROOF FOR THE GROUPING. This is the run a careless version
    produces: every claim its own pseudo-group, every group an anecdote, and a
    report that read the file and found nothing."""
    rows, _items = _rows(per_conversation=True)
    claims, _stats = project_signals(rows)
    grouped, unthemed, _ = assign_themes(list(claims), {})
    assert len(unthemed) == len(claims)
    from app.crucible.cluster import assign_clusters

    regrouped, _ = assign_clusters(grouped, {})
    result = build_findings(regrouped, currency="accounts", now=NOW)
    assert not result.findings
    # AND FOR THE STATED REASON: one pseudo-group per claim, every one of them
    # a lone anecdote. The run reads the document and reports nothing, which
    # looks exactly like a document with nothing in it.
    assert (result.stats["dropped"].get("ungroupable") or 0) == len(claims)


# ─── 6. Surviving a stalled-enrichment sweep ────────────────────────────────


def test_only_the_rows_a_finding_cites_are_kept_for_recovery():
    rows, _items = _rows(per_conversation=True)
    cited = {rows[0]["id"], rows[2]["id"]}
    kept = prose.rows_for_recovery(rows, cited)
    assert {r["id"] for r in kept} == cited


def test_recovery_rows_never_carry_an_embedding():
    """1,536 floats per row, in a jsonb blob read on every poll of the run, to
    serve a path that does not read them."""
    rows, _items = _rows(per_conversation=True)
    kept = prose.rows_for_recovery(rows, {r["id"] for r in rows})
    assert kept and not any("embedding" in r for r in kept)


def test_recovery_rows_are_bounded(monkeypatch):
    monkeypatch.setattr(prose, "MAX_PERSISTED_ROWS", 2)
    rows, _items = _rows(per_conversation=True)
    kept = prose.rows_for_recovery(rows, {r["id"] for r in rows})
    assert len(kept) == 2


def test_a_run_stored_before_this_existed_reads_back_as_no_prose():
    assert prose.rows_from_meta({}) == []
    assert prose.rows_from_meta({"plan": {}}) == []
    assert prose.rows_from_meta({prose.META_KEY: "not a list"}) == []
    assert prose.rows_from_meta({prose.META_KEY: [{"no": "id"}]}) == []


def test_a_swept_run_reconstitutes_its_prose_claims(monkeypatch):
    """THE HOLE THIS CLOSES, EXERCISED THROUGH THE REAL SWEEP FUNCTION.

    `_reenrich_stalled_run` rebuilds claims from `kg_signal` BY ID — and a
    prose claim's id is deliberately not there. Left alone, a run whose
    enrichment stalled (a deploy mid-flight is enough) comes back from the
    sweep having lost every prose-backed claim, and `recommend`'s claim-id gate
    then drops the citations naming them: findings that still read as
    prose-backed, carrying recommendations the engine can no longer ground.
    """
    import app.routes.crucible as routes

    rows, _items = _rows(per_conversation=True)
    prose_id = rows[0]["id"]
    meta = {"plan": {"definition_text": "d"}, prose.META_KEY: [rows[0]]}
    seen: dict = {}

    monkeypatch.setattr(routes, "_meta_of", lambda *a, **k: dict(meta))
    monkeypatch.setattr(routes.runs_db, "load_findings",
                        lambda *a, **k: ([{"id": 1, "statement": "s",
                                           "claim_ids": [prose_id],
                                           "adjudication": "supported"}], []))
    monkeypatch.setattr(routes, "_load_signals_by_id", lambda *a, **k: [])
    monkeypatch.setattr(routes, "_self_account_keys", lambda *a: frozenset())
    monkeypatch.setattr(routes.runs_db, "update",
                        lambda *a, **k: seen.setdefault("wrote", k))

    def _capture(**kwargs):
        seen["claims"] = kwargs["claims"]
        return {}

    monkeypatch.setattr(routes, "_run_enrichment", _capture)
    routes._reenrich_stalled_run(7, CO, {"goal_text": "g"})

    assert [c.id for c in seen["claims"]] == [prose_id]
    assert seen["wrote"]["prioritisation"]["enrichment_outcome"] == \
        "completed_by_sweep"


def test_without_the_stored_rows_the_swept_run_loses_the_claim(monkeypatch):
    """THE MUTATION PROOF FOR THE TEST ABOVE — the same sweep, over a run whose
    prose was never persisted. This is exactly the silent loss, and it is what
    the assertion above would look like if `_remember_prose` regressed."""
    import app.routes.crucible as routes

    rows, _items = _rows(per_conversation=True)
    prose_id = rows[0]["id"]
    meta = {"plan": {"definition_text": "d"}}
    seen: dict = {}

    monkeypatch.setattr(routes, "_meta_of", lambda *a, **k: dict(meta))
    monkeypatch.setattr(routes.runs_db, "load_findings",
                        lambda *a, **k: ([{"id": 1, "statement": "s",
                                           "claim_ids": [prose_id],
                                           "adjudication": "supported"}], []))
    monkeypatch.setattr(routes, "_load_signals_by_id", lambda *a, **k: [])
    monkeypatch.setattr(routes, "_self_account_keys", lambda *a: frozenset())
    monkeypatch.setattr(routes.runs_db, "update", lambda *a, **k: None)

    def _capture(**kwargs):
        seen["claims"] = kwargs["claims"]
        return {}

    monkeypatch.setattr(routes, "_run_enrichment", _capture)
    routes._reenrich_stalled_run(7, CO, {"goal_text": "g"})
    assert seen["claims"] == ()


def test_remember_prose_stores_only_what_the_findings_cite(monkeypatch):
    import app.routes.crucible as routes

    rows, _items = _rows(per_conversation=True)
    wrote: dict = {}
    monkeypatch.setattr(routes, "_meta_of", lambda *a, **k: {"plan": {"a": 1}})
    monkeypatch.setattr(routes.runs_db, "update",
                        lambda *a, **k: wrote.update(k))
    routes._remember_prose(
        7, CO, rows, [{"claim_ids": [rows[1]["id"], "not-a-prose-id"]}])
    stored = wrote["prioritisation"][prose.META_KEY]
    assert [r["id"] for r in stored] == [rows[1]["id"]]
    # AND THE REST OF THE BLOB SURVIVES. A wholesale replace here would erase
    # the approved plan the report has to reprint.
    assert wrote["prioritisation"]["plan"] == {"a": 1}


# ─── 7. And still nothing reaches the knowledge graph ───────────────────────


def test_nothing_reaches_the_knowledge_graph(monkeypatch):
    """THE CONSTRAINT THIS WHOLE PATH EXISTS UNDER, asserted the same way
    `test_crucible_chat_uploads.py` asserts it for the tabular half: by making
    every mutating verb raise, rather than by inspecting what was called.

    A tenant running this has real connector data in `kg_signal`. The
    connector-upload path would write an attached pack into it permanently and
    there is no undo in the product. So: reading a document, segmenting it,
    projecting it into claims, grouping it and running the pipeline over it
    writes nothing, to any table, ever.
    """
    class _Forbidden:
        def __init__(self, name):
            self._name = name

        def __getattr__(self, verb):
            if verb in ("insert", "upsert", "update", "delete", "rpc"):
                raise AssertionError(
                    f"the run-scoped prose path wrote to {self._name!r} "
                    f"via .{verb}() — nothing may reach the knowledge graph")
            return lambda *a, **k: self

        def execute(self):
            class _R:
                data: list = []
                count = 0
            return _R()

    class _Client:
        def table(self, name):
            return _Forbidden(name)

        def __getattr__(self, item):
            raise AssertionError(f"unexpected client access: {item}")

    monkeypatch.setattr("app.db.client.require_client", _Client)

    docs, unread = prose.read_prose(
        [("calls.txt", _transcript(3, stated=3).encode())], now=NOW)
    assert docs and not unread
    rows, items_by_id = _rows(per_conversation=True)
    _grouped, _unthemed, result, stats = _run(rows, items_by_id)

    # AND IT DID THE WORK. A vacuously empty run passes every assertion above.
    assert stats["projected"] == 3
    assert len(result.findings) == 1


def test_this_path_writes_to_no_table_at_all(monkeypatch):
    """STRICTER THAN THE GUARD ABOVE, AND IT IS THE WHOLE CLAIM NOW.

    The extraction cache was a Postgres table when this was first built, so
    this test used to enumerate exactly one permitted write. It does not any
    more: the cache is in-process (see `crucible.prose_cache` for why the table
    was dropped), so the run-scoped path writes to NOTHING. Every `kg_*`
    mutation still raises, and every mutation on any other table is recorded
    and asserted empty — which catches a future durable cache arriving quietly
    as well as it catches a graph write.
    """
    from app.crucible import prose_cache

    touched: list[tuple[str, str]] = []

    class _Recorded:
        def __init__(self, name):
            self._name = name

        def __getattr__(self, verb):
            if verb in ("insert", "upsert", "update", "delete", "rpc"):
                if self._name.startswith("kg_"):
                    raise AssertionError(
                        f"wrote to the knowledge graph table {self._name!r} "
                        f"via .{verb}()")
                touched.append((self._name, verb))
            return lambda *a, **k: self

        def execute(self):
            class _R:
                data: list = []
                count = 0
            return _R()

    class _Client:
        def table(self, name):
            return _Recorded(name)

    monkeypatch.setattr("app.db.client.require_client", _Client)
    monkeypatch.setattr(
        "app.graph.gateway.llm_call",
        lambda **kw: type("R", (), {
            "output": {"signals": [dict(CALLS[0][2])]}})())
    prose_cache.clear()

    # THE REAL EXTRACTION PATH, not just the cache's own put(). An earlier
    # version of this test called `prose_cache.put` directly, which meant a
    # durable cache added inside `_extract_segment` would have sailed straight
    # past it — the exact regression this test exists to catch.
    docs, _unread = prose.read_prose(
        [("calls.txt", _transcript(3, stated=3).encode())], now=NOW)
    evidence = prose.extract_documents(docs, enterprise_id=CO, now=NOW)

    # AND IT DID THE WORK, so the empty `touched` below is a statement about
    # where the answer went rather than about nothing having happened.
    #
    # `failed_segments` IS ASSERTED SEPARATELY AND ON PURPOSE.
    # `extract_documents` is total by contract, so a write raised by the guard
    # above is CAUGHT there and turns into a failed segment rather than into a
    # test failure naming the table. Without this line the test still goes red
    # (no rows), but on a symptom two steps from the cause; with it, the run
    # says a segment failed, which is what actually happened.
    assert evidence.failed_segments == 0
    assert evidence.rows
    assert prose_cache.get(
        enterprise_id=CO,
        content_sha256=__import__("hashlib").sha256(
            docs[0].segments[0].text.encode()).hexdigest(),
        prompt_version=__import__(
            "app.graph.extractor", fromlist=["PROMPT_VERSION"]).PROMPT_VERSION,
    ) is not None
    assert touched == []


# ─── 8. The cache, and the guarantee it does NOT make ───────────────────────


def test_the_cache_is_keyed_on_the_tenant_as_well_as_the_bytes():
    """ONE PROCESS SERVES EVERY WORKSPACE. Keyed on the document hash alone,
    this would hand one tenant's extracted claims to another tenant's
    identically-worded file — the worst failure available to a cache in a
    multi-tenant worker, and silent."""
    from app.crucible import prose_cache

    prose_cache.clear()
    prose_cache.put(enterprise_id=CO, content_sha256="same",
                    prompt_version="v1", output={"signals": [{"a": 1}]})
    assert prose_cache.get(enterprise_id="another-company",
                           content_sha256="same",
                           prompt_version="v1") is None


def test_a_prompt_change_invalidates_by_construction():
    from app.crucible import prose_cache

    prose_cache.clear()
    prose_cache.put(enterprise_id=CO, content_sha256="same",
                    prompt_version="v1", output={"signals": []})
    assert prose_cache.get(enterprise_id=CO, content_sha256="same",
                           prompt_version="v2") is None


def test_an_entry_of_the_wrong_shape_is_a_miss_not_an_error():
    from app.crucible import prose_cache

    prose_cache.clear()
    prose_cache.put(enterprise_id=CO, content_sha256="a", prompt_version="v1",
                    output={"signals": "not a list"})
    assert prose_cache.get(enterprise_id=CO, content_sha256="a",
                           prompt_version="v1") is None
    # A non-dict never lands either, so a caller handed a malformed model
    # response caches nothing rather than caching a wrong shape.
    prose_cache.put(enterprise_id=CO, content_sha256="b", prompt_version="v1",
                    output="not a dict")
    assert prose_cache.get(enterprise_id=CO, content_sha256="b",
                           prompt_version="v1") is None


def test_the_cache_is_bounded_and_evicts_the_least_recently_used(monkeypatch):
    """IT LIVES FOR THE LIFE OF THE PROCESS, so an unbounded dict of extraction
    results is a slow leak on a long-lived worker. LRU rather than
    first-in-first-out because the entry worth keeping is the one a run is
    still re-reading, not the one that happens to be newest."""
    from app.crucible import prose_cache

    monkeypatch.setattr(prose_cache, "MAX_ENTRIES", 2)
    prose_cache.clear()
    for name in ("a", "b"):
        prose_cache.put(enterprise_id=CO, content_sha256=name,
                        prompt_version="v1", output={"signals": [{"n": name}]})
    # Touch "a", so "b" becomes the least recently used.
    assert prose_cache.get(enterprise_id=CO, content_sha256="a",
                           prompt_version="v1") is not None
    prose_cache.put(enterprise_id=CO, content_sha256="c", prompt_version="v1",
                    output={"signals": []})
    assert prose_cache.get(enterprise_id=CO, content_sha256="a",
                           prompt_version="v1") is not None
    assert prose_cache.get(enterprise_id=CO, content_sha256="b",
                           prompt_version="v1") is None


# ─── 9. What the plan says it did ───────────────────────────────────────────


def test_a_file_read_as_prose_is_not_also_reported_as_unread():
    """ONE ATTACHMENT, ONE STATEMENT ABOUT IT. A PDF has always landed in the
    tabular pass's unread list with the reason "this pass reads the structure
    of spreadsheets and CSVs" — true then, and now the same plan would also say
    it read it as ten conversations. A plan that says both is worse than a plan
    that says either."""
    import app.routes.crucible as routes
    from app import attachments_storage

    data = _transcript(3, stated=3).encode()
    routes.attachments_storage = attachments_storage
    original = attachments_storage.read_attachment
    try:
        attachments_storage.read_attachment = (
            lambda *, workspace_id, key: data)
        tables, unread, docs = routes._read_uploads(
            (("chat-attachments/ws-1/u.txt", "calls.txt"),), "ws-1")
    finally:
        attachments_storage.read_attachment = original

    assert [d.name for d in docs] == ["calls.txt"]
    assert tables == []
    assert [u.name for u in unread] == []


def test_the_plan_lists_what_it_read_and_how_it_split_it():
    from app.crucible import plan as plan_mod
    from app.crucible.recon import ReconReport

    doc = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=100,
        segments=tuple(prose.ProseSegment(i, f"c{i}", NOW, "t")
                       for i in range(3)),
        per_conversation=True, markers_found=3, stated_count=3,
    )
    listed = plan_mod.prose_from_report(ReconReport(prose=(doc,)))
    assert [(p.name, p.conversations) for p in listed] == [("calls", 3)]
    # THE SENTENCE IS THE DISCLOSURE, and it is the segmentation's own — not
    # recomposed here from the count, which would be a second implementation
    # of one statement, free to drift from the split that actually ran.
    assert listed[0].how == doc.how_it_was_read


def test_a_plan_built_before_this_existed_reads_back_as_no_prose():
    from app.crucible import plan as plan_mod

    assert plan_mod.prose_from_report(None) == ()
    assert plan_mod.RunPlan(
        goal_text="g", definition_text="d", currency="accounts",
    ).to_json()["prose_uploads"] == []


def test_the_opening_sentence_names_the_document_and_how_it_was_read():
    """THE SENTENCE IS FALSE WITHOUT THIS CLAUSE. Every branch of
    `_evidence_sentence` promises the run is computed over the listed tables
    "and nothing else"; a document read as prose produces no table and appears
    in none of them, so a run that read ten calls would open its method by
    telling the reader it read four spreadsheets and nothing else."""
    from app.crucible.planner import _evidence_sentence
    from app.crucible.recon import ReconReport

    doc = prose.ProseDocument(
        name="calls.pdf", sha256="x", chars=100,
        segments=tuple(prose.ProseSegment(i, f"c{i}", NOW, "t")
                       for i in range(3)),
        per_conversation=True, markers_found=3, stated_count=3,
    )
    said = _evidence_sentence(ReconReport(prose=(doc,)), ("customer_voice",))
    assert "calls.pdf" in said
    assert "3 separate conversations" in said
    assert "not added to your knowledge graph" in said
    # AND A RUN WITH NO ATTACHMENT SAYS EXACTLY WHAT IT ALWAYS SAID.
    plain = _evidence_sentence(ReconReport(), ("customer_voice",))
    assert "attached" not in plain


def test_the_report_says_what_it_read_and_what_it_could_not():
    import app.routes.crucible as routes

    evidence = prose.ProseEvidence(
        read=(("calls.pdf", "read as 3 separate conversations"),),
        unread=(("scan.pdf", "it is a binary or unrecognised format"),),
        failed_segments=1,
    )
    notes = routes._prose_notes(evidence)
    blob = " ".join(n["reason"] + " " + n["actual"] for n in notes)
    assert "calls.pdf" in blob and "3 separate conversations" in blob
    assert "scan.pdf" in blob and "binary or unrecognised" in blob
    assert "1 conversation(s)" in blob
    assert "not added to your knowledge graph" in blob
    # A RUN WITH NO ATTACHMENTS SAYS NOTHING, rather than saying it read none.
    assert routes._prose_notes(None) == []
    assert routes._coverage_notes({}, {}, None) == []


# ─── 10. What a file has to be to be read at all ────────────────────────────


def test_a_spreadsheet_is_never_read_as_prose():
    """IT WOULD BE COUNTED TWICE. `recon` reads a workbook structurally and the
    plan gate weighs its own unit against it; flattening the same file into
    sentences and extracting claims from it would have one attachment both
    inform the method and vote on the findings."""
    assert ".xlsx" not in prose.PROSE_SUFFIXES
    assert ".csv" not in prose.PROSE_SUFFIXES
    docs, unread = prose.read_prose([("book.xlsx", b"PK\x03\x04" * 500)],
                                    now=NOW)
    assert docs == () and unread == ()


def test_a_file_the_converter_chokes_on_is_named_rather_than_dropped():
    docs, unread = prose.read_prose(
        [("scan.pdf", b"%PDF-1.4\x00\x01\x02" * 40)], now=NOW)
    assert docs == ()
    assert len(unread) == 1
    assert "could not be opened" in unread[0].reason


def test_a_document_with_almost_no_text_is_named_rather_than_sent_to_a_model():
    docs, unread = prose.read_prose([("note.txt", b"hi")], now=NOW)
    assert docs == ()
    assert str(prose.MIN_PROSE_CHARS) in unread[0].reason


def test_the_document_bound_is_reported_rather_than_applied_quietly():
    body = _transcript(1, stated=1).encode()
    files = [(f"calls{i}.txt", body) for i in range(prose.MAX_PROSE_FILES + 2)]
    docs, unread = prose.read_prose(files, now=NOW)
    assert len(docs) == prose.MAX_PROSE_FILES
    assert len(unread) == 2
    assert all(str(prose.MAX_PROSE_FILES) in u.reason for u in unread)


def test_an_extraction_item_missing_a_required_field_costs_only_itself():
    """`signals_from_items` subscripts `kind`/`content`/`source_type`
    directly — safe on the ingest path, where a KeyError aborts one job, and
    not here, where it would abort a run the reader is watching."""
    good = _item("Northwind wants faster exports.", "Northwind Logistics")
    kept = prose._usable(
        [good, {"content": "no kind here"}, "not a dict", {}],
        enterprise_id=CO, doc_name="calls.pdf")
    assert kept == [good]


@pytest.mark.parametrize("name,expected", [
    ("calls.pdf", True), ("CALLS.PDF", True), ("notes.md", True),
    ("book.xlsx", False), ("data.csv", False), ("archive.zip", False),
])
def test_which_attachments_this_pass_takes_responsibility_for(name, expected):
    assert prose._is_prose_name(name) is expected


# ─── 11. The whole path, through the real routes ───────────────────────────
#
# EVERY TEST ABOVE EXERCISES ONE JOINT. These two exercise the wiring between
# them, which is where a feature that is correct in every part still delivers
# nothing: a plan that never carries the prose onto its stored json, or a run
# that reads the document at the gate and then projects the connected corpus
# alone, fails no unit test above and produces exactly the run this feature
# exists to remove.


@pytest.fixture
def prose_ctx(isolated_settings, monkeypatch):
    """A workspace with one connected signal and one attached transcript."""
    from app import attachments_storage
    from tests import _fake_supabase
    from tests._company_helpers import company_client
    from tests.test_routes_crucible import _DDL, _enable

    _fake_supabase.get_fake_db().executescript(_DDL)
    ctx = company_client(monkeypatch)
    _enable(ctx.company_id)

    from app.db.client import require_client

    # ONE CONNECTED SIGNAL, so the run is not a prose-only corpus and the
    # assertions below are about prose being ADDED rather than about it being
    # the only thing there.
    require_client().table("kg_signal").insert({
        "id": "sig-0001", "enterprise_id": ctx.company_id, "kind": "finding",
        "source_type": "customer_voice",
        "content": "AdventureWorks mentioned the export during onboarding",
        "properties": {"customer": "AdventureWorks Retail"},
        "provenance": {"doc": "call-0"},
        "valid_at": "2026-08-01T00:00:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
        "transaction_at": "2026-08-01T00:00:00+00:00",
        "embedding": str([0.11, 0.22, 0.33, 0.44]),
    }).execute()

    data = _transcript(3, stated=3).encode()
    monkeypatch.setattr(attachments_storage, "owns_key",
                        lambda *, workspace_id, key: True)
    monkeypatch.setattr(attachments_storage, "read_attachment",
                        lambda *, workspace_id, key: data)

    # A COLD CACHE PER TEST. `prose_cache` lives for the life of the PROCESS,
    # so without this a test would inherit the extractions of whichever test
    # ran before it and its call counts would depend on ordering — the exact
    # shape of flake that makes a cache assertion worthless.
    from app.crucible import prose_cache

    prose_cache.clear()

    # THE EXTRACTION, STUBBED — and only the extraction. Everything between
    # the stub and the findings is the production path. ONE ITEM PER
    # CONVERSATION, keyed on the artifact id the prompt carries, because the
    # whole question downstream is whether three conversations produce three
    # differently-attributed claims.
    calls: list[str] = []
    by_artifact = {f"calls.txt#Call with {name}": item
                   for (_t, _d, item), name in zip(
                       CALLS, ("Northwind", "Contoso", "Fabrikam"))}

    def _fake_llm(**kwargs):
        calls.append(kwargs.get("purpose") or "")
        if kwargs.get("purpose") != "extract_run_scoped_prose":
            raise AssertionError(
                f"unexpected model call: {kwargs.get('purpose')}")
        said = kwargs.get("input") or ""
        for artifact_id, item in by_artifact.items():
            if artifact_id in said:
                return type("R", (), {"output": {"signals": [dict(item)]}})()
        raise AssertionError(f"no artifact id in the prompt: {said[:200]!r}")

    monkeypatch.setattr("app.graph.gateway.llm_call", _fake_llm)
    ctx.llm_calls = calls
    ctx.start = lambda: ctx.client.post("/v1/crucible", json={
        "goal_text": "raise net revenue retention",
        "attachments": [{"key": "chat-attachments/ws/calls.txt",
                         "name": "calls.txt"}],
    })
    return ctx


def test_the_gate_reads_the_document_and_says_how_without_calling_a_model(
    prose_ctx,
):
    """THE GATE RETURNS IN ABOUT A SECOND AND MUST KEEP DOING SO.

    An extraction call per conversation is tens of seconds. The read at the
    gate is local — a text layer and a regular expression — and the model call
    happens after the reader has approved. `llm_calls` being empty is that
    property asserted rather than assumed.
    """
    run_id = prose_ctx.start().json()["id"]
    plan = prose_ctx.client.get(
        f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]

    assert [p["name"] for p in plan["prose_uploads"]] == ["calls"]
    assert plan["prose_uploads"][0]["conversations"] == 3
    assert "3 separate conversations" in plan["prose_uploads"][0]["how"]
    # AND IT IS NOT ALSO LISTED AS UNREAD, which would have one plan telling
    # the reader both that it read the document and that it could not.
    assert plan["unread_uploads"] == []
    assert plan["uploads"] == []
    assert "extract_run_scoped_prose" not in prose_ctx.llm_calls


def test_approving_turns_the_document_into_claims_the_run_actually_reads(
    prose_ctx,
):
    """THE JOINT THAT MAKES OR BREAKS THE FEATURE.

    A run that reads the document at the gate and then projects the connected
    corpus alone passes every unit test in this file and delivers nothing. So
    this asserts the numbers the run itself publishes: claims read, and the
    coverage note naming the document.
    """
    run_id = prose_ctx.start().json()["id"]
    prose_ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
    row = prose_ctx.client.get(f"/v1/crucible/{run_id}").json()

    assert "extract_run_scoped_prose" in prose_ctx.llm_calls
    # ONE CONNECTED SIGNAL PLUS THREE FROM THE TRANSCRIPT.
    progress = row["prioritisation"]["progress"]
    assert progress["signals_read"] == 4
    assert progress["claims"] == 4
    notes = " ".join(
        f"{n['reason']} {n['actual']}" for n in (row["coverage_notes"] or []))
    assert "calls.txt" in notes
    assert "3 separate conversations" in notes
    assert "not added to your knowledge graph" in notes

    # THREE CONVERSATIONS, THREE ARTIFACTS. The stored rows are what a
    # stalled-enrichment sweep would rebuild from, so this checks the identity
    # scheme survived all the way to the record rather than only to the
    # projection.
    stored = row["prioritisation"][prose.META_KEY]
    assert len({r["provenance"]["doc"] for r in stored}) == 3
    assert all(r["provenance"]["channel"] == prose.ATTACHMENT_CHANNEL
               for r in stored)
    # AND EVERY ONE OF THEM IS CITED BY A FINDING — the filter kept the set
    # the sweep will actually ask for.
    from app.db import crucible_runs as runs_db

    findings, _ledger = runs_db.load_findings(run_id, prose_ctx.company_id)
    cited = {c for f in findings for c in (f.get("claim_ids") or ())}
    assert {r["id"] for r in stored} <= cited


def test_an_attached_document_does_not_disturb_the_goal_relevance_wiring(
    prose_ctx, monkeypatch,
):
    """CROSS-CUT WITH THE GOAL-POPULATION-AWARE RELEVANCE GATE.

    `_run_enrichment` reads `goal_class` off the run's stored PLAN
    (`plan["routing"]["goal_class"]`) — a fact settled when the plan was
    built, before any attachment is read. Prose only ever joins `claims` and
    `findings`, further downstream, on the same call `execute_run` was
    already making. So an attached document must change neither WHAT
    `goal_class` `judge_relevance` is handed, nor WHERE it is handed one —
    `_run_enrichment` stays the only call site on both the direct run and the
    stalled-enrichment sweep.
    """
    run_id = prose_ctx.start().json()["id"]
    plan = prose_ctx.client.get(
        f"/v1/crucible/{run_id}").json()["prioritisation"]["plan"]
    goal_class = plan["routing"]["goal_class"]

    seen = {}
    import app.crucible.relevance as relevance_mod
    real = relevance_mod.judge_relevance

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(relevance_mod, "judge_relevance", spy)
    approved = prose_ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
    assert approved.status_code == 200
    # THE SAME READING THE PLAN ALREADY MADE, not re-derived and not
    # defaulted away because an attachment was in play.
    assert seen.get("goal_class") == goal_class, seen
    # AND THE PROSE-BACKED FINDINGS ACTUALLY REACHED THIS CALL — proving
    # nothing on the prose path short-circuits around `judge_relevance`.
    assert seen.get("findings"), "the gate never received any findings at all"
    prose_claim_ids = {r["id"] for r in prose_ctx.client.get(
        f"/v1/crucible/{run_id}").json()["prioritisation"].get(
        prose.META_KEY, [])}
    assert prose_claim_ids, "the run stored no prose claim rows to check against"
    reached_ids = {cid for f in seen["findings"] for cid in f.claim_ids}
    assert prose_claim_ids & reached_ids, (
        "no prose-backed claim reached the relevance gate's findings"
    )


def test_the_same_bytes_are_extracted_once_while_the_worker_lives(prose_ctx):
    """WHAT THE CACHE ACTUALLY DELIVERS, AND WHAT IT DOES NOT.

    A model call is a draw, not a lookup, so the same document read twice must
    not be sampled twice. Inside one process it is not: three conversations
    cost three calls, and a second run over the identical attachment costs
    none — which covers the repeats that actually happen, a retry or a re-read
    or a second run before the worker recycles.

    THE LIMIT IS PINNED IN THE SAME TEST, deliberately. `prose_cache` is
    in-process, so a run in a FRESH worker re-extracts and may produce a
    slightly different claim set. That is a real cost and the product must not
    imply otherwise; clearing the cache stands in for that worker, and the
    assertion below is the cost stated as a fact rather than as a caveat.

    It is also not a weakness peculiar to this path: the relevance gate judges
    fresh on a new run for the same reason. Making attached prose reproducible
    ACROSS runs is a decision about the whole engine, and `prose_cache`'s
    docstring is where the argument lives.
    """
    from app.crucible import prose_cache

    run_id = prose_ctx.start().json()["id"]
    prose_ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
    assert prose_ctx.llm_calls.count("extract_run_scoped_prose") == 3

    second = prose_ctx.start().json()["id"]
    prose_ctx.client.post(f"/v1/crucible/{second}/approve", json={})
    assert prose_ctx.llm_calls.count("extract_run_scoped_prose") == 3

    # AND THE COST, ASSERTED. A fresh worker has an empty cache and pays again.
    prose_cache.clear()
    third = prose_ctx.start().json()["id"]
    prose_ctx.client.post(f"/v1/crucible/{third}/approve", json={})
    assert prose_ctx.llm_calls.count("extract_run_scoped_prose") == 6


def test_a_run_with_no_attachment_reads_exactly_what_it_read_before(prose_ctx):
    """THE CONTROL. Every path above is additive or it is a regression."""
    run_id = prose_ctx.client.post(
        "/v1/crucible", json={"goal_text": "raise net revenue retention"},
    ).json()["id"]
    prose_ctx.client.post(f"/v1/crucible/{run_id}/approve", json={})
    row = prose_ctx.client.get(f"/v1/crucible/{run_id}").json()

    assert row["prioritisation"]["plan"]["prose_uploads"] == []
    assert row["prioritisation"]["progress"]["signals_read"] == 1
    assert prose_ctx.llm_calls == []
    assert row["coverage_notes"] is not None


# ─── 12. The one write-back in the run, over a claim that is not in the graph ─


def test_persisting_a_figure_class_cannot_create_a_row_for_a_prose_claim():
    """THE ONLY PER-CLAIM WRITE-BACK IN A RUN, aimed at an id `kg_signal` does
    not hold.

    `figure_class.persist_classes` stores the class beside its own signal so
    the next run reads a fact rather than taking a second sample — and a
    prose-backed claim carrying a stated figure goes into `classify_figures`
    with every other claim, because the run projects ONE corpus and that is the
    containment this feature rests on.

    It is safe today for a reason worth pinning rather than rediscovering: the
    function SELECTS before it updates and skips an id it does not find. The
    obvious "simplification" — an upsert, or a blind write — would silently
    mint `kg_signal` rows for evidence that must never reach the graph, and no
    other test in this repo would notice. Asserted with every mutating verb on
    every `kg_*` table raising, which is the shape that catches all of them.
    """
    from app.crucible.figure_class import persist_classes

    touched: list[str] = []

    class _KgForbidden:
        def __init__(self, name):
            self._name = name

        def __getattr__(self, verb):
            if verb in ("insert", "upsert", "update", "delete", "rpc"):
                if self._name.startswith("kg_"):
                    raise AssertionError(
                        f"persist_classes wrote to {self._name!r} via "
                        f".{verb}() for a claim that is not in the graph")
                touched.append(self._name)
            return lambda *a, **k: self

        def execute(self):
            class _R:
                data: list = []
                count = 0
            return _R()

    class _Client:
        def table(self, name):
            return _KgForbidden(name)

    import app.db.client as db_client

    original = db_client.require_client
    try:
        db_client.require_client = lambda *a, **k: _Client()
        rows, _items = _rows(per_conversation=True)
        written = persist_classes(
            {r["id"]: "deal_value" for r in rows}, company_id=CO)
    finally:
        db_client.require_client = original

    assert written == 0
    assert touched == []
