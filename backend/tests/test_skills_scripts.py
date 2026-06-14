"""The 4 deterministic skill scripts run locally via the typed-arg runner."""
from __future__ import annotations

from app.skills.scripts import SCRIPT_TOOLS, tool_for


def test_sample_size_computes():
    out = SCRIPT_TOOLS["experiment-design"].run({"baseline": 0.10, "mde": 0.01})
    assert "Per-arm sample size" in out
    assert "Total (2 arms)" in out


def test_prioritize_score_ranks():
    out = SCRIPT_TOOLS["prioritize"].run(
        {
            "method": "rice",
            "items": [
                {"name": "SSO", "reach": 100, "impact": 2, "confidence": 0.8, "effort": 3},
                {"name": "export", "reach": 300, "impact": 1, "confidence": 0.9, "effort": 2},
            ],
        }
    )
    assert "RICE ranking" in out
    # export (135) outranks SSO (~53)
    assert out.index("export") < out.index("SSO")


def test_prd_lint_returns_findings_despite_nonzero_exit():
    # prd_lint exits 1 when it finds blocking issues — the runner must still
    # return the stdout findings, not swallow them as an error.
    out = SCRIPT_TOOLS["prd-critique"].run({"prd_text": "# Title\njust a body, no sections"})
    assert "BLOCK" in out
    assert "Summary:" in out
    assert not out.startswith("(script")  # not an error string


def test_validation_guards_bad_args():
    assert SCRIPT_TOOLS["prioritize"].run({"method": "bogus", "items": []}).startswith("(score:")
    assert SCRIPT_TOOLS["experiment-design"].run({}).startswith("(sample_size:")
    assert SCRIPT_TOOLS["saas-metrics-diagnosis"].run({"metrics": "nope"}).startswith("(saas_metrics:")
    assert SCRIPT_TOOLS["prd-critique"].run({}).startswith("(prd_lint:")


def test_argv_is_not_shell_evaluated():
    # A malicious-looking item name is passed as JSON data on stdin, never as a
    # shell token — it should appear verbatim in the output, not execute.
    out = SCRIPT_TOOLS["prioritize"].run(
        {
            "method": "rice",
            "items": [{"name": "x; rm -rf /", "reach": 1, "impact": 1, "confidence": 1, "effort": 1}],
        }
    )
    assert "rm -rf" in out  # treated as a label, not run


def test_tool_for_and_as_tool_shape():
    t = tool_for("prioritize")
    assert t is not None
    td = t.as_tool()
    assert td["name"] == "prioritize_score"
    assert td["input_schema"]["required"] == ["method", "items"]
    assert tool_for("roadmap") is None  # not a script skill
