"""MoSCoW: `constraint` earns MUST, `preference` earns SHOULD/COULD, graded by
independent source documents rather than raw claim count (the echo trap the
reasoning spike measured — a dozen restatements of one sentence from one
document is one witness, not twelve)."""
from app.crucible.moscow import bucket_for, moscow_for


def test_a_constraint_is_a_must_when_well_corroborated():
    bucket, basis = bucket_for(["constraint"], doc_count=3)
    assert bucket == "MUST"
    assert "3" in basis


def test_a_single_document_constraint_is_flagged_thin_not_demoted():
    """A thin MUST is still real evidence of a blocker — I1 forbids letting
    corroboration (or its absence) change IMPACT, so this is flagged for a
    human to confirm rather than silently re-bucketed as a SHOULD."""
    bucket, basis = bucket_for(["constraint"], doc_count=1)
    assert bucket == "MUST?"
    assert "single source document" in basis


def test_a_preference_backed_by_several_documents_is_a_should():
    bucket, basis = bucket_for(["preference"], doc_count=2)
    assert bucket == "SHOULD"


def test_a_preference_from_one_document_is_a_could():
    bucket, basis = bucket_for(["preference"], doc_count=1)
    assert bucket == "COULD"


def test_a_constraint_outranks_a_preference_in_the_same_finding():
    """Strongest type decides — same rule as `rice.impact_for`: one blocked
    deal among ten wants is still about a blocked deal."""
    bucket, _ = bucket_for(["preference", "constraint", "mechanism"], doc_count=3)
    assert bucket == "MUST"


def test_a_finding_with_neither_type_is_unranked_not_dropped():
    bucket, basis = bucket_for(["mechanism", "existence"], doc_count=5)
    assert bucket == "unranked"
    assert "neither" in basis


def test_doc_count_reads_the_pipelines_own_per_document_counts():
    """`surfaced_by` arrives PRE-COUNTED per document (`pipeline._sources_of`
    already collapsed repeat claims from one source into one `"doc (n)"`
    entry — that is the corroboration-inflation guard the echo rule enforces
    upstream). Two entries here means two documents, regardless of how many
    claims either one carries."""
    row = moscow_for(
        label="x", reach=5.0, reach_unit="accounts",
        claim_types=["constraint"],
        surfaced_by=["doc-a (3)", "doc-b (1)"],
    )
    assert row.doc_count == 2
    assert row.bucket == "MUST"


def test_reach_none_renders_as_none_not_zero():
    """I3: an un-sized finding is unmeasured, never a confident zero."""
    row = moscow_for(
        label="x", reach=None, reach_unit="accounts",
        claim_types=["constraint"], surfaced_by=["doc-a"],
    )
    assert row.reach is None


def test_blank_surfaced_by_entries_are_not_counted_as_documents():
    row = moscow_for(
        label="x", reach=None, reach_unit="accounts",
        claim_types=["preference"], surfaced_by=["", None, "doc-a"],
    )
    assert row.doc_count == 1


def test_the_overflow_summary_entry_expands_back_to_its_real_count():
    """`surfaced_by` is `pipeline._sources_of`'s DISPLAY format: up to
    MAX_NAMED_SOURCES real `"doc (n)"` entries plus one trailing
    `"+K more documents"` summary once there are more than that. Treating the
    summary as one more document would undercount corroboration on exactly
    the best-attested findings — the ones with the most sources."""
    row = moscow_for(
        label="x", reach=None, reach_unit="accounts",
        claim_types=["constraint"],
        surfaced_by=["doc-a (5)", "doc-b (3)", "doc-c (2)", "doc-d (1)",
                     "+3 more documents"],
    )
    assert row.doc_count == 7   # 4 named + 3 summarised, not 5
