"""Background PRD generation. Triggered when a user clicks 'Generate PRD';
the HTTP request returns immediately with a prd_id and status='generating',
the actual Claude call runs in a worker thread, and the prds row gets
updated to status='ready' (or 'failed') when done.

Mirrors evidence_runner; both consume a brief insight + corpus + template
and produce a markdown document via call_md.
"""
import asyncio
import json
import logging
import uuid

from app.corpus import load_corpus, load_prd_template
from app.db import complete_prd, fail_prd, get_brief_by_id
from app.llm import call_md
from app.prompts import PRD_SYSTEM, PRD_USER_TEMPLATE
from app.synthesis import kg_hooks

logger = logging.getLogger(__name__)


# Required semantic blocks the v2 PRD template must populate. The block
# names map onto the spec's required fields:
#   :::problem            -> problem_statement (§2 + user_story narrative)
#   :::requirements       -> functional_requirements (§4 rows)
#   :::acceptance-criteria-> acceptance_criteria (§5 rows)
# `user_stories` is read out of the :::problem block's user_story field
# rather than its own semantic block — see _validate_required_blocks.
_REQUIRED_BLOCKS = (
    "problem",
    "requirements",
    "acceptance-criteria",
)


def _validate_required_blocks(md: str) -> None:
    """Smoke-check that the LLM produced the required semantic blocks
    populated with content. Raises RuntimeError on a missing or empty
    block so the caller marks the PRD failed with a clear error.

    The PRD generator returns markdown with `:::name ... :::` blocks
    (see data/sprntly_prd_template.md). We don't parse the JSON inside
    each block — that's a downstream renderer concern — but we do
    confirm the block opens, has content, and closes.
    """
    missing: list[str] = []
    for name in _REQUIRED_BLOCKS:
        opener = f":::{name}"
        idx = md.find(opener)
        if idx < 0:
            missing.append(name)
            continue
        # Block must close after the opener with some content in between.
        tail = md[idx + len(opener):]
        close = tail.find(":::")
        if close < 0 or not tail[:close].strip():
            missing.append(name)
    # `user_stories` lives inside :::problem.user_story — flag if the
    # problem block exists but never names a user_story field.
    if "problem" not in missing and '"user_story"' not in md:
        missing.append("user_stories")
    if missing:
        raise RuntimeError(
            f"PRD missing required template fields: {', '.join(missing)}"
        )


def _run_sync(
    prd_id: int,
    brief_id: int,
    insight_index: int,
    *,
    decision_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
    insights = brief.get("insights") or []
    if not (0 <= insight_index < len(insights)):
        raise RuntimeError(
            f"insight_index={insight_index} out of range (0..{len(insights) - 1})"
        )
    insight = insights[insight_index]
    dataset = brief.get("dataset", "asurion")
    corpus = load_corpus(dataset)
    template = load_prd_template()
    user = PRD_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        corpus=corpus.joined(),
        template=template,
    )
    md = call_md(system=PRD_SYSTEM, user=user)
    _validate_required_blocks(md)
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_prd(prd_id=prd_id, title=title, md=md)

    # KG write event §5.6 — fire the no-op hook so the wire-up PR has a
    # call site to replace. decision_id is not in the data model yet;
    # synthesize a placeholder until briefs carry one.
    if decision_id is None:
        decision_id = f"placeholder-decision-{uuid.uuid4()}"
        logger.info(
            "TODO(KG): no decision_id on brief_id=%s; using placeholder %s",
            brief_id,
            decision_id,
        )
    ws = workspace_id or dataset
    kg_hooks.write_prd_generated(
        decision_id,
        {"prd_id": prd_id, "title": title, "payload_md": md, "variant": "v2"},
        workspace_id=ws,
    )


async def generate_prd(
    prd_id: int,
    brief_id: int,
    insight_index: int,
    *,
    decision_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    """Run PRD generation in a worker thread; update DB with result."""
    logger.info(
        "PRD generation starting prd_id=%s brief_id=%s insight_index=%s",
        prd_id,
        brief_id,
        insight_index,
    )
    try:
        await asyncio.to_thread(
            _run_sync,
            prd_id,
            brief_id,
            insight_index,
            decision_id=decision_id,
            workspace_id=workspace_id,
        )
        logger.info("PRD generation succeeded prd_id=%s", prd_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("PRD generation failed prd_id=%s", prd_id)
        fail_prd(prd_id, msg)
