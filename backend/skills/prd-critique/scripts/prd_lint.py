#!/usr/bin/env python3
"""
prd_lint.py — deterministic structural checks for a PRD.
Catches the boring-but-load-bearing gaps so the LLM critique can focus on judgment.

Usage:
    python3 prd_lint.py path/to/prd.md
    cat prd.md | python3 prd_lint.py -

Exit code 0 = no blocking issues, 1 = one or more checks failed.
This is heuristic (keyword/section based), not a parser. Treat warnings as prompts to look, not verdicts.
"""
import sys, re

SOLUTION_WORDS = ["dashboard", "button", "page", "screen", "feature", "tool", "system", "platform", "modal"]

def load(arg):
    if arg == "-" or arg is None:
        return sys.stdin.read()
    with open(arg, encoding="utf-8") as f:
        return f.read()

def has_section(text, *names):
    for n in names:
        if re.search(rf"(^|\n)\s*#+.*{re.escape(n)}", text, re.I):
            return True
        if re.search(rf"(^|\n)\s*\*\*?{re.escape(n)}", text, re.I):
            return True
    return False

def check(text):
    findings = []
    # 1. Problem statement present
    if not has_section(text, "problem"):
        findings.append(("BLOCK", "No problem section found."))
    # 2. Problem not solution-smuggled (first 600 chars of problem area)
    m = re.search(r"problem[^\n]*\n(.{0,600})", text, re.I | re.S)
    if m:
        seg = m.group(1).lower()
        hits = [w for w in SOLUTION_WORDS if w in seg]
        if hits:
            findings.append(("WARN", f"Possible solution-smuggling in problem section (mentions: {', '.join(sorted(set(hits)))}). Confirm the problem is stated without a solution."))
    # 3. Success metric + baseline
    if not has_section(text, "metric", "success", "goal"):
        findings.append(("BLOCK", "No success-metric / goals section found."))
    else:
        if not re.search(r"baseline|current|from\s+\d|→|->", text, re.I):
            findings.append(("WARN", "Primary metric may lack a baseline/target (no 'baseline', 'current', or 'X → Y' found)."))
        if not re.search(r"guardrail|must not|not regress|do not regress", text, re.I):
            findings.append(("WARN", "No guardrail metric found — what must NOT regress?"))
    # 4. Non-goals
    if not has_section(text, "non-goal", "non goals", "out of scope"):
        findings.append(("BLOCK", "No non-goals / out-of-scope section — scope is likely unbounded."))
    # 5. Done when
    if not (has_section(text, "done when", "definition of done", "acceptance") or re.search(r"done when", text, re.I)):
        findings.append(("WARN", "No testable 'done when' / acceptance criteria found."))
    # 6. Risk / assumption
    if not (has_section(text, "risk", "assumption") or re.search(r"riskiest assumption|pre-?mortem", text, re.I)):
        findings.append(("WARN", "No risks / riskiest-assumption section found."))
    return findings

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    text = load(arg)
    findings = check(text)
    if not findings:
        print("PRD lint: PASS — all structural checks satisfied.")
        sys.exit(0)
    blocks = [f for f in findings if f[0] == "BLOCK"]
    for level, msg in findings:
        icon = "BLOCK" if level == "BLOCK" else "warn "
        print(f"[{icon}] {msg}")
    print(f"\nSummary: {len(blocks)} blocking, {len(findings)-len(blocks)} warnings.")
    sys.exit(1 if blocks else 0)

if __name__ == "__main__":
    main()
