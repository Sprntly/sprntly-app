---
name: pm-resume-review
description: Review or tailor a PM resume against proven best practices — outcome-driven bullets (action → metric → impact), keyword/ATS fit, structure, and signal-to-noise — with specific rewrites, not vague advice. Use when the user says "review my resume", "tailor my resume", "PM resume", "is my CV good", or pastes a resume (optionally + a job description). Rewrites weak bullets into quantified-impact form, checks fit to a target role, and prioritizes the few changes that matter most.
---

# PM Resume Review

## What it does
Reviews a product-manager resume the way a hiring PM leader would skim it, and returns **specific rewrites** — turning duty bullets into outcome bullets (what you did → the metric → the impact), checking structure and keyword/ATS fit, and cutting noise. If a job description is provided, it tailors to that role. It prioritizes the handful of changes that move the resume most, not a line-by-line nitpick.

## When to use / when NOT to use
- **Use** to strengthen or tailor a PM resume.
- **Do NOT use** for a cover letter, LinkedIn profile rewrite, or non-PM roles (the bullets/keywords differ).

## Inputs
- **Required:** the resume.
- **Optional:** a target job description, seniority level, target companies. *Won't invent achievements or metrics — if a bullet lacks impact, it asks for the number or flags it, never fabricates one.*

## Method (methodology)
1. **Outcome bullets** — rewrite duty/responsibility lines into action → quantified result → impact ("Led X, increasing Y by Z%"). If the metric is missing, prompt for it (never invent).
2. **Signal & structure** — most-impressive-first, scannable, right length; cut filler and clichés.
3. **Keyword/ATS fit** — align language to the target role/JD without keyword-stuffing.
4. **Tailor** (if JD given) — surface the most relevant experience for that role.
5. **Prioritize** — the 3-5 changes that matter most, then the minor ones.

## Output spec
Top priorities first (the few changes that move it most), specific bullet rewrites (before → after), structure/keyword notes, and JD-fit if provided. Honest about gaps; no fabricated achievements.

## Quality checklist (the bar)
- [ ] Weak bullets rewritten to action → metric → impact form (with real or prompted-for numbers — none invented).
- [ ] Structure/length/scannability addressed; filler cut.
- [ ] Keyword/role fit checked without stuffing.
- [ ] Prioritized — the vital few changes first, not an exhaustive nitpick.

## Known gaps / limitations
- Can't verify claims — it strengthens phrasing of true achievements; honesty is the author's.
- ATS behavior varies by employer; keyword fit is heuristic.

## Worked example
**Input:** a PM resume, bullets like "Responsible for the mobile app." → rewrite "Owned mobile app roadmap; grew 30-day retention from 22%→31% [confirm metric]." Top 4 priorities flagged; structure tightened to one page.
