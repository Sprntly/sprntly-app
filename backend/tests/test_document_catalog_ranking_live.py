"""The catalog's ranking itself, against a REAL Postgres — not the fake client.

Why this file exists at all. Every other test of document retrieval stubs
`document_find_candidates` and asserts what Python does with its result,
because the fake Supabase client has no SQL engine behind `rpc()`. That is a
reasonable boundary, and it is also exactly where a whole day's worth of wrong
answers hid: the ranking returned the newest document for whatever was asked
and labelled it a topic match, and no test could have noticed because no test
ran the function. The ranking is SQL. Verifying it needs SQL.

Why it is not in CI. Neither CI lane runs a Postgres service, and adding one
is a change to a shared, PR-gating workflow — out of scope for the fix that
found this. So this file SKIPS cleanly wherever the ingredients are absent and
runs where they are present: a local rig or staging. It adds no dependency to
`requirements*.txt` — the driver import is optional and its absence is a skip.

Run it with:

    DOCUMENT_CATALOG_TEST_DSN=postgresql://user:pw@host:port/db \\
        pytest tests/test_document_catalog_ranking_live.py -m integration

Everything is built inside a throwaway schema created per-session and dropped
afterwards, so pointing this at a populated database reads and writes nothing
that already exists there.

The two migrations are applied FROM THE REPO, in order, so this also proves
the thing that made the original fix a no-op risk: the later migration is a
separate file that applies cleanly on top of the earlier one and rebuilds the
stored column, rather than an edit to an already-tracked migration that no
environment would ever re-run.
"""
from __future__ import annotations

import os
import pathlib
import uuid

import pytest

try:  # Optional: absent from requirements on purpose — see the module docstring.
    import psycopg
except ImportError:  # pragma: no cover - environment-dependent
    psycopg = None  # type: ignore[assignment]

_DSN = os.getenv("DOCUMENT_CATALOG_TEST_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        psycopg is None or not _DSN,
        reason="needs a real Postgres: set DOCUMENT_CATALOG_TEST_DSN and install psycopg",
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_BASE_MIGRATION = "20260803120000_document_catalog.sql"
_TITLE_MIGRATION = "20260803150000_document_catalog_title_lexemes.sql"

_COMPANY = "11111111-1111-1111-1111-111111111111"

# The parents the catalog migration references. Minimal on purpose: this file
# verifies the catalog's ranking, not the tables it has foreign keys into.
_PRELUDE = """
create extension if not exists vector;
create table companies (id uuid primary key);
create table workspaces (id uuid primary key);
create table conversations (id bigserial primary key, company_id uuid, user_id uuid);
"""

# A workspace shaped like the one that reported this: nine documents that all
# talk about the same product, so the workspace's own name is a term almost
# every document shares and therefore separates none of them. That is the
# condition under which the lexical channel goes flat, and a flat lexical
# channel with no semantic channel is a ranking decided entirely by
# `updated_at desc`.
#
# The first row is the document the reporter wanted and did not get. Its
# summariser had not run, so summary and topics are empty exactly as they were
# live — which before the title fix left it with NOTHING in the lexical channel
# at all. The second row is the newest, and is what came back instead.
_DOCS = [
    ("Sprntly_vs_Productboard_Comparison.docx", "", [],
     "2026-08-03T09:00:00+00:00"),
    ("Sprntly_vs_OpenAI_PRD.docx",
     "PRD setting out how Sprntly differs from OpenAI's assistant on product "
     "workflows, with the positioning argument.",
     ["openai", "positioning", "prd"], "2026-08-03T11:30:00+00:00"),
    ("Release_Notes_2026_07.docx",
     "Sprntly release notes for July 2026: shipped features, fixes and known "
     "issues.", ["release notes", "changelog"], "2026-08-01T09:00:00+00:00"),
    ("Samsung_Health_Pilot_Brief.docx",
     "Brief for the Samsung Health pilot running on Sprntly, with the August "
     "go-live plan.", ["samsung", "pilot", "go-live"],
     "2026-07-30T09:00:00+00:00"),
    ("Xometry_Onboarding_Plan.docx",
     "Onboarding plan for Xometry on Sprntly, covering data import and "
     "workspace setup.", ["xometry", "onboarding"],
     "2026-07-29T09:00:00+00:00"),
    ("Knowledge_Graph_Spec.docx",
     "Specification for the Sprntly knowledge graph: entities, signals and "
     "extraction.", ["knowledge graph", "extraction"],
     "2026-07-28T09:00:00+00:00"),
    ("Q3_Pricing_Model.docx",
     "Sprntly pricing model for Q3 with seat tiers and usage allowances.",
     ["pricing", "seats"], "2026-07-27T09:00:00+00:00"),
    ("Support_Escalation_Runbook.docx",
     "Runbook for escalating Sprntly support incidents to on-call.",
     ["support", "runbook", "on-call"], "2026-07-26T09:00:00+00:00"),
    ("Design_Agent_Handoff.docx",
     "Handoff notes for the Sprntly design agent prototype flow.",
     ["design agent", "handoff"], "2026-07-25T09:00:00+00:00"),
]

# The reporter's verbatim question — "product board" as two words, the filename
# never named.
Q_TOPIC = "give me a summary of the product board vs sprntly discussion"
# An unrelated question about the same workspace. Shares only the workspace's
# name with the corpus, which is the whole point: it is the question that
# proves ranking responds to what was ASKED rather than to what is newest.
Q_UNRELATED = "what did the team decide about sprntly last quarter"

_PRODUCTBOARD = "Sprntly_vs_Productboard_Comparison.docx"
_OPENAI_PRD = "Sprntly_vs_OpenAI_PRD.docx"


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


@pytest.fixture(scope="module")
def catalog_schema():
    """A throwaway schema with both migrations applied, in order.

    Isolated by `search_path` so this can point at a populated database without
    reading or writing anything already in it, and dropped on the way out.
    """
    schema = "doccat_test_" + uuid.uuid4().hex[:12]
    conn = psycopg.connect(_DSN, autocommit=True)
    try:
        conn.execute(f"create schema {schema}")
        conn.execute(f"set search_path = {schema}, public")
        conn.execute(_PRELUDE)
        for name in (_BASE_MIGRATION, _TITLE_MIGRATION):
            path = _MIGRATIONS / name
            assert path.exists(), f"migration missing from the repo: {name}"
            conn.execute(path.read_text())
        conn.execute(
            "insert into companies (id) values (%s) on conflict do nothing",
            (_COMPANY,),
        )
        yield conn
    finally:
        try:
            conn.execute(f"drop schema if exists {schema} cascade")
        finally:
            conn.close()


@pytest.fixture(scope="module")
def seeded_catalog(catalog_schema):
    """The nine rows, embedded the way registration embeds them.

    Uses the product's own `embed_texts` and the same payload composition as
    `document_catalog._summary_embedding` (title + summary + topics, empties
    dropped) rather than a hand-rolled call, so a change to the model or to
    that composition shows up here instead of being papered over by a fixture
    that embeds differently from production.
    """
    from app.graph.embeddings import embed_texts

    payloads = [
        " ".join(p for p in (title, summary, " ".join(topics)) if p).strip()
        for title, summary, topics, _ in _DOCS
    ]
    vectors = embed_texts(payloads + [Q_TOPIC, Q_UNRELATED])
    if not vectors or not any(vectors[0]):
        # `embed_texts` hands back zero vectors when no key is configured, and
        # a zero vector in cosine kNN ranks arbitrarily. Ranking measured on
        # those is noise wearing the costume of a result, so skip instead.
        pytest.skip("OPENAI_API_KEY not configured — no usable embeddings")

    doc_vectors = vectors[: len(_DOCS)]
    q_topic, q_unrelated = vectors[len(_DOCS)], vectors[len(_DOCS) + 1]

    catalog_schema.execute("delete from document_catalog")
    for (title, summary, topics, updated), vec in zip(_DOCS, doc_vectors):
        catalog_schema.execute(
            "insert into document_catalog (company_id, provider, external_id,"
            " title, source_name, summary, topics, content_hash, embedding,"
            " doc_date, updated_at)"
            " values (%s, 'uploads', %s, %s, 'Uploads', %s, %s, %s,"
            " %s::vector, %s, %s)",
            (_COMPANY, title, title, summary, list(topics), "hash-" + title,
             _vector_literal(vec), updated, updated),
        )
    return catalog_schema, q_topic, q_unrelated


def _ranked_titles(conn, question, embedding, k=9):
    with conn.cursor() as cur:
        cur.execute(
            "select title, score from document_find_candidates("
            " %s::uuid, null, null, %s, %s::vector(1536), %s)",
            (_COMPANY, question,
             _vector_literal(embedding) if embedding else None, k),
        )
        return cur.fetchall()


# ── T5 (AC1/AC2) — what the lexical channel can actually see in a filename ──


def test_raw_filename_collapses_to_a_single_unsearchable_lexeme(catalog_schema):
    """The cause, asserted rather than described.

    Postgres's default parser reads `Sprntly_vs_Productboard_Comparison.docx`
    as a `host` token — the trailing extension makes it look like a hostname —
    and emits ONE lexeme, the whole string. No question anybody types contains
    that lexeme, so folding the title in raw contributed nothing to the lexical
    rank and the channel rested entirely on `summary` and `topics`.

    This asserts the behaviour of the parser, not of our code, and it is here
    because it is the fact the fix turns on. If a future Postgres tokenises
    this differently, the normalisation below stops being load-bearing and
    somebody should be told.
    """
    with catalog_schema.cursor() as cur:
        cur.execute(
            "select alias, token, lexemes from ts_debug('english', %s)"
            " where alias <> 'blank'",
            (_PRODUCTBOARD,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    alias, token, lexemes = rows[0]
    assert alias == "host"
    assert token == _PRODUCTBOARD
    assert lexemes == [_PRODUCTBOARD.lower()]
    # The words a person would actually ask with are all absent.
    assert "productboard" not in lexemes
    assert "comparison" not in lexemes


def test_normalised_title_yields_the_words_a_question_would_use(catalog_schema):
    """AC1/AC2: the search text the generated column is built from must break
    that filename into its words.

    Asserted through `document_catalog_search_text` — the function the STORED
    column actually calls — rather than through the normaliser alone, so the
    assertion covers the wiring and not just the regex.
    """
    with catalog_schema.cursor() as cur:
        cur.execute(
            "select alias, lexemes from ts_debug('english',"
            " document_catalog_search_text(%s, '', '', array[]::text[]))"
            " where alias <> 'blank'",
            (_PRODUCTBOARD,),
        )
        rows = cur.fetchall()

    lexemes = [lx for _, lxs in rows for lx in (lxs or [])]
    assert all(alias == "asciiword" for alias, _ in rows), rows
    # Four words, each its own lexeme — 'Sprntly' stems to 'sprntli'.
    assert lexemes == ["sprntli", "vs", "productboard", "comparison"]


def test_the_stored_column_is_rebuilt_for_rows_that_already_existed(
    catalog_schema,
):
    """AC3's load-bearing half.

    `create or replace function` does not recompute a STORED generated column;
    Postgres keeps the values it already wrote until the row is written again.
    So the migration drops and re-adds the column, and this asserts the
    consequence: a row inserted through the migrated schema is searchable by a
    word that only the NORMALISED title contains.

    A migration that replaced the function and stopped there would leave every
    existing row exactly as unsearchable as before while looking, in the diff,
    like a fix.
    """
    with catalog_schema.cursor() as cur:
        cur.execute("delete from document_catalog")
        cur.execute(
            "insert into document_catalog (company_id, provider, external_id,"
            " title, source_name, summary, topics, content_hash)"
            " values (%s, 'uploads', 'f-rebuild', %s, 'Uploads', '',"
            " array[]::text[], 'hash-rebuild')",
            (_COMPANY, _PRODUCTBOARD),
        )
        cur.execute(
            "select search_tsv @@ document_catalog_or_tsquery('productboard')"
            " from document_catalog where external_id = 'f-rebuild'"
        )
        assert cur.fetchone()[0] is True
        cur.execute("delete from document_catalog")


# ── AC6 at the SQL layer — zero rows means zero rows ────────────────────────


def test_no_channel_match_returns_nothing_rather_than_the_newest_document(
    catalog_schema,
):
    """AC6: with neither channel ranking anything, the function returns no
    rows — it does not degrade into `order by updated_at desc`.

    This is the guarantee the Python side leans on to promise that nothing is
    ever labelled `match: "topic"` unless the ranking returned it. Run without
    an embedding so the semantic channel is genuinely absent, and with a
    question sharing no term with the corpus.

    Non-goal, restated so nobody reads more into a green here than it carries:
    this is the ZERO-ROWS case only. Fusion has no relevance floor by design,
    so a question the catalog has nothing useful for can still rank documents
    once an embedding is present. That is deliberate and is not what this
    asserts.
    """
    with catalog_schema.cursor() as cur:
        cur.execute("delete from document_catalog")
        cur.execute(
            "insert into document_catalog (company_id, provider, external_id,"
            " title, source_name, summary, topics, content_hash, updated_at)"
            " values (%s, 'uploads', 'f-only', 'Quarterly_Revenue.docx',"
            " 'Uploads', 'Revenue by segment.', array['revenue']::text[],"
            " 'hash-only', now())",
            (_COMPANY,),
        )
        cur.execute(
            "select title from document_find_candidates("
            " %s::uuid, null, null, %s, null, 5)",
            (_COMPANY, "photosynthesis in mangrove seedlings"),
        )
        assert cur.fetchall() == []
        cur.execute("delete from document_catalog")


# ── T1 / T2 — the reported incident, and the regression that guards it ──────


def test_topic_question_ranks_the_document_it_is_about_first(seeded_catalog):
    """T1 (AC1/AC4): the reporter's question, against the document he wanted.

    Before the fix this returned `Sprntly_vs_OpenAI_PRD.docx` and the
    Productboard document did not appear at all — it had no summary, so with a
    raw-title tsvector it was in neither channel.

    Both halves of the fix are load-bearing here and this fails without either:
    the normalised title is what puts the Productboard document into the
    lexical channel, and the embedding is what ranks it first once it is there.
    """
    conn, q_topic, _ = seeded_catalog

    ranked = _ranked_titles(conn, Q_TOPIC, q_topic)
    titles = [t for t, _ in ranked]

    assert titles[0] == _PRODUCTBOARD, ranked
    assert titles.index(_PRODUCTBOARD) < titles.index(_OPENAI_PRD), ranked


def test_the_document_the_summariser_missed_reaches_both_channels(
    seeded_catalog,
):
    """The mechanism behind T1, asserted separately so a regression says WHICH
    half broke.

    `Sprntly_vs_Productboard_Comparison.docx` carries `summary = ''` and
    `topics = {}`. Its ONLY searchable text is its title, so if the title is
    not normalised it is lexically invisible — permanently, and with no error:
    the function returns fewer rows and nothing anywhere says a document was
    skipped.
    """
    conn, q_topic, _ = seeded_catalog

    with conn.cursor() as cur:
        cur.execute(
            "select search_tsv @@ document_catalog_or_tsquery(%s)"
            "  from document_catalog where external_id = %s",
            (Q_TOPIC, _PRODUCTBOARD),
        )
        assert cur.fetchone()[0] is True, "lexically invisible"
        cur.execute(
            "select embedding is not null from document_catalog"
            " where external_id = %s", (_PRODUCTBOARD,),
        )
        assert cur.fetchone()[0] is True


def test_different_questions_return_different_top_candidates(seeded_catalog):
    """T2 — the query-independence regression, and the cheapest guard we have.

    The defect was not "a wrong document scored highest", it was that ranking
    did not depend on the question at all: every candidate tied, the SQL's
    last-resort ordering is `updated_at desc`, and so the newest document was
    returned for anything anybody asked and reported as a topic match.

    Measured on the unfixed schema, both of these questions returned
    `Sprntly_vs_OpenAI_PRD.docx` — the newest row — with the same top score.
    Two questions with nothing in common but the workspace's name must not
    agree on what the best document is.
    """
    conn, q_topic, q_unrelated = seeded_catalog

    topic_ranked = _ranked_titles(conn, Q_TOPIC, q_topic)
    unrelated_ranked = _ranked_titles(conn, Q_UNRELATED, q_unrelated)

    assert topic_ranked and unrelated_ranked
    assert topic_ranked[0][0] != unrelated_ranked[0][0], (
        topic_ranked[:3], unrelated_ranked[:3],
    )
    # And specifically not the failure mode: the newest document winning both.
    assert not (
        topic_ranked[0][0] == _OPENAI_PRD and unrelated_ranked[0][0] == _OPENAI_PRD
    )


def test_an_exact_fusion_tie_is_broken_by_relevance_not_recency(seeded_catalog):
    """The tie is arithmetic, not coincidence, and it lands on exactly the pair
    a topical question is trying to choose between.

    Symmetric RRF over two channels ties whenever two documents swap ranks:
    1/(50+1) + 1/(50+2) is the same number as 1/(50+2) + 1/(50+1). Here the
    Productboard document is lexical #2 / semantic #1 and the OpenAI PRD is
    lexical #1 / semantic #2, so both fuse to the same score. Under the old
    `order by score desc, updated_at desc` the newer row won — and recency is
    not a relevance signal, which is what made the wrong answer look ranked.
    """
    conn, q_topic, _ = seeded_catalog

    ranked = dict(_ranked_titles(conn, Q_TOPIC, q_topic))
    assert ranked[_PRODUCTBOARD] == ranked[_OPENAI_PRD], ranked

    with conn.cursor() as cur:
        cur.execute(
            "select external_id, updated_at from document_catalog"
            " where external_id in (%s, %s) order by updated_at desc",
            (_PRODUCTBOARD, _OPENAI_PRD),
        )
        newest = cur.fetchall()[0][0]
    # The tie-break must have chosen the OTHER one — i.e. not by recency.
    assert newest == _OPENAI_PRD
    assert _ranked_titles(conn, Q_TOPIC, q_topic)[0][0] == _PRODUCTBOARD


def test_a_null_embedding_leaves_the_lexical_ordering_untouched(seeded_catalog):
    """The new tie-break must be inert when only one channel ran.

    With `p_embedding` null the score is `1/(50 + lexical_rank)`, strictly
    decreasing in a rank `row_number()` makes unique — so there are no ties for
    the new rule to break and the ordering is the one the previous function
    produced. Asserted so the tie-break cannot quietly start reordering the
    degraded path.
    """
    conn, _, _ = seeded_catalog

    ranked = _ranked_titles(conn, Q_TOPIC, None)
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores), ranked
