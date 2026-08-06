"""The engineering-spec compiler + its validator.

Short by design: Part B is markdown with no viewer, no CSS and no class
vocabulary, so the only structural guarantee is that the B0-B9 ids survive a
customer's format. Everything below is either that rule or the storage
invariant `resolve_impl_spec_template` depends on.
"""
import pytest

from app.artifact_templates import compile_impl_spec
from app.artifact_templates.validate import (
    IMPL_SPEC_SECTION_IDS,
    missing_impl_spec_ids,
    validate_impl_spec_skeleton,
)

pytestmark = pytest.mark.real_template_compile


_GOOD = (
    "# Engineering Spec — {{title}}\n"
    "## B0. Where this came from\n"
    "## B1. Background\n## B2. Stakes\n## B3. Behaviour rules\n"
    "## B4. Contracts\n## B5. Escalations\n## B6. Cross-cutting\n"
    "## B7. Work breakdown\n## B8. Done when\n## B9. Independent check\n"
)


# ── the B0-B9 rule ───────────────────────────────────────────────────────────

def test_the_house_skeleton_itself_validates():
    """Our own shipped template through our own validator, so dropping a
    B-section from the house format goes red before any customer's does."""
    house = compile_impl_spec._house_skeleton()
    assert missing_impl_spec_ids(house) == []
    assert validate_impl_spec_skeleton(house).status == "ready"


def test_a_renamed_section_keeps_its_id_and_passes():
    """The customer's NAMES are theirs; the ids are ours. `## B3. ACME
    BEHAVIOUR RULES` is a correct adoption, not a violation."""
    renamed = _GOOD.replace("B3. Behaviour rules", "B3. ACME BEHAVIOUR RULES")
    assert validate_impl_spec_skeleton(renamed).status == "ready"


@pytest.mark.parametrize("dropped", IMPL_SPEC_SECTION_IDS)
def test_every_missing_id_blocks_activation(dropped):
    """Any missing id is `needs_review` — previewable and fixable, but not
    activatable, because what it breaks downstream is silent: the ticket
    generator inherits acceptance criteria from B3 and raises nothing at all
    when it finds none."""
    broken = "\n".join(
        line for line in _GOOD.splitlines() if f"{dropped}." not in line
    )
    assert missing_impl_spec_ids(broken) == [dropped]
    verdict = validate_impl_spec_skeleton(broken)
    assert verdict.status == "needs_review"
    assert verdict.notes, "a blocked format must say why"


def test_a_bare_mention_in_prose_does_not_satisfy_a_missing_section():
    """The id has to appear as its own token. A sentence saying 'see B7' is not
    a B7 section, and counting it would pass a skeleton that has none."""
    without_b7 = "\n".join(
        line for line in _GOOD.splitlines() if "B7." not in line
    )
    assert missing_impl_spec_ids(without_b7 + "\nSee also B7B for details.\n") == ["B7"]


def test_a_script_is_refused_outright():
    """Markdown permits raw HTML, and Part B is handed to a coding agent and
    pushed into tracker descriptions. `failed` means the skeleton is never
    stored, so it can never be previewed or activated."""
    verdict = validate_impl_spec_skeleton(_GOOD + "<script>alert(1)</script>")
    assert verdict.status == "failed"
    assert verdict.notes[0]["code"] == "unsafe_script"


# ── the compiler ─────────────────────────────────────────────────────────────

def _seed(db, *, company_id="co-spec", artifact_type="impl_spec"):
    from app.db.artifact_templates import insert_template
    return insert_template(
        company_id=company_id, workspace_id="ws-1", artifact_type=artifact_type,
        name="Acme spec", source_md="# Acme spec format\n",
        content_hash="hash12345678", uploader_id="u-1", uploader_name="Ada",
    )


def test_the_customer_markdown_is_fenced_and_tagged(isolated_settings, monkeypatch):
    """Untrusted text reaching a prompt: BEGIN/END markers so a `#` heading in
    their file cannot read as a section of this prompt, a company-uploaded tag,
    and the addendum bounding how far its authority reaches. Only their markdown
    is uncached; the house skeleton rides the cacheable prefix."""
    row = _seed(isolated_settings["supabase"])
    captured = {}

    class _R:
        output = {"skeleton_md": _GOOD}

    def _call(**kwargs):
        captured.update(kwargs)
        return _R()

    monkeypatch.setattr(compile_impl_spec, "llm_call", _call)
    compile_impl_spec.compile_impl_spec_template("co-spec", row["id"])

    assert "--- BEGIN COMPANY-UPLOADED FORMAT ---" in captured["input"]
    assert "--- END COMPANY-UPLOADED FORMAT ---" in captured["input"]
    assert "company-uploaded" in captured["input"]
    assert compile_impl_spec._UNTRUSTED_TEMPLATE_ADDENDUM in captured["system"]
    assert captured["skill"] == "implementation-spec"
    # The house skeleton is the reference vocabulary, and it is byte-stable
    # across companies — so it is a cache read after the first call.
    assert "B0. Derivation" in captured["user_cacheable_prefix"]
    assert "B0. Derivation" not in captured["input"]


def test_a_clean_compile_stores_the_skeleton_ready(isolated_settings, monkeypatch):
    row = _seed(isolated_settings["supabase"])

    class _R:
        output = {"skeleton_md": _GOOD}

    monkeypatch.setattr(compile_impl_spec, "llm_call", lambda **k: _R())
    updated = compile_impl_spec.compile_impl_spec_template("co-spec", row["id"])
    assert updated["compile_status"] == "ready"
    assert "B9" in updated["compiled"]


def test_a_failed_compile_leaves_the_last_good_skeleton_standing(
    isolated_settings, monkeypatch
):
    """THE STORAGE INVARIANT `resolve_impl_spec_template` depends on from the
    other side. A recompile of the company's ACTIVE format must not blank the
    skeleton it is generating with right now."""
    from app.db.artifact_templates import set_compile_result

    row = _seed(isolated_settings["supabase"])
    set_compile_result(
        company_id="co-spec", template_id=row["id"],
        compile_status="ready", compiled=_GOOD, compile_notes=[],
    )

    def _boom(**_kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(compile_impl_spec, "llm_call", _boom)
    updated = compile_impl_spec.compile_impl_spec_template("co-spec", row["id"])
    assert updated["compile_status"] == "failed"
    assert updated["compiled"] == _GOOD, "the last good skeleton must survive"


def test_a_foreign_id_is_none_and_a_prd_row_is_left_alone(
    isolated_settings, monkeypatch
):
    """The company-filtered read is the tenancy boundary, and the type check is
    the backstop for a mis-dispatch."""
    row = _seed(isolated_settings["supabase"])
    prd_row = _seed(isolated_settings["supabase"], artifact_type="prd")

    def _never(**_kwargs):
        raise AssertionError("no model call should be made")

    monkeypatch.setattr(compile_impl_spec, "llm_call", _never)
    assert compile_impl_spec.compile_impl_spec_template("other-co", row["id"]) is None
    left = compile_impl_spec.compile_impl_spec_template("co-spec", prd_row["id"])
    assert left["compile_status"] == "pending"
