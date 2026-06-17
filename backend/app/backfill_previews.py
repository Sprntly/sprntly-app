"""Preview-image backfill — `python -m app.backfill_previews`.

Repairs prototype preview thumbnails that captured the un-hydrated SPA shell
(or never captured at all). The original capture navigated Chromium to the
signed Supabase bundle URL, where the SPA's relative `./assets/*` module scripts
could not resolve, so React never mounted and the screenshot only ever showed
the empty `#root` shell; older rows have a null `preview_image_url`. The capture
path now renders the bundle from a local loopback server (screenshot.py); this
one-off re-runs that corrected capture against the ALREADY-STAGED dist/ bundle —
no LLM call and no vite rebuild.

For each target it:
  1. loads the prototype's `current_checkpoint_id`,
  2. reads the staged dist/ files back from storage
     (`read_bundle_files_for_checkpoint`),
  3. re-renders locally (`capture_bundle_screenshot`) → PNG,
  4. stages the PNG (`stage_preview_image`, which upserts in place at
     `prototypes/<pid>/<cid>/_preview/preview.png`),
  5. updates the row's `preview_image_url` (`set_preview_image_url`).

Idempotent + prod-safe: `stage_preview_image` upserts (overwrites the existing
object in place) and `set_preview_image_url` rewrites a single column, so a
re-run only corrects the thumbnail — it never changes status, bundle_url, or
completed_at. A capture that honest-degrades (no Chromium / bundle absent /
never hydrates) is logged and skipped; the row is left as-is, never blanked.

Usage:
    python -m app.backfill_previews <prototype_id>
    python -m app.backfill_previews all

`<prototype_id>` repairs one prototype; `all` walks every ready prototype that
has a current_checkpoint across all workspaces. Run with the same environment
(Supabase + DESIGN_AGENT_ANTHROPIC_API_KEY not required; storage + Chromium are)
the API uses, e.g. via `--env-file .env` semantics already in your shell.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.backfill_previews")


async def backfill_one(prototype_id: int, workspace_id: str, checkpoint_id: int) -> bool:
    """Re-render + re-stage the preview for one prototype. Returns True on a write.

    Reads the staged dist/ back, re-renders locally, and on a real screenshot
    stages it (upsert in place) and updates the row. An honest-degrade capture
    (None) is logged and skipped — the existing thumbnail is left untouched.
    Never raises: one bad prototype must not abort an `all` run.
    """
    from app.design_agent.screenshot import capture_bundle_screenshot
    from app.design_agent.storage import (
        read_bundle_files_for_checkpoint,
        stage_preview_image,
    )
    from app.db.prototypes import set_preview_image_url

    try:
        files = await read_bundle_files_for_checkpoint(prototype_id, checkpoint_id)
        if not files or "index.html" not in files:
            logger.warning(
                "backfill_skip_no_bundle prototype_id=%s checkpoint_id=%s",
                prototype_id, checkpoint_id,
            )
            return False

        png = await capture_bundle_screenshot(files)
        if png is None:
            # Honest-degrade: no Chromium runtime / never hydrated / nav error.
            logger.warning(
                "backfill_capture_degraded prototype_id=%s checkpoint_id=%s",
                prototype_id, checkpoint_id,
            )
            return False

        url = await stage_preview_image(
            prototype_id=prototype_id,
            checkpoint_id=checkpoint_id,
            png_bytes=png,
        )
        set_preview_image_url(
            prototype_id=prototype_id,
            workspace_id=workspace_id,
            preview_image_url=url,
        )
        logger.info(
            "backfill_preview_updated prototype_id=%s checkpoint_id=%s",
            prototype_id, checkpoint_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — one bad row must not abort an `all` run.
        logger.warning(
            "backfill_preview_failed prototype_id=%s checkpoint_id=%s error_class=%s",
            prototype_id, checkpoint_id, type(exc).__name__,
        )
        return False


async def backfill_all() -> tuple[int, int]:
    """Re-render previews for every ready prototype with a current_checkpoint.

    Returns (updated, total) counts. Sequential — the capture pins a CPU/IO-bound
    headless Chromium, so concurrency would only contend on a small host.
    """
    from app.db.prototypes import list_ready_prototypes_for_backfill

    rows = list_ready_prototypes_for_backfill()
    logger.info("backfill_all_start candidate_count=%s", len(rows))
    updated = 0
    for row in rows:
        ok = await backfill_one(
            prototype_id=row["id"],
            workspace_id=row["workspace_id"],
            checkpoint_id=row["current_checkpoint_id"],
        )
        updated += 1 if ok else 0
    logger.info("backfill_all_done updated=%s total=%s", updated, len(rows))
    return updated, len(rows)


async def backfill_target(prototype_id: int) -> tuple[int, int]:
    """Re-render the preview for a single prototype id. Returns (updated, total).

    Resolves the row across workspaces (operator-run repair) to read its
    workspace_id + current_checkpoint_id, then re-renders. (0, 0) when the row is
    absent or has no current_checkpoint.
    """
    from app.db.client import require_client

    resp = (
        require_client()
        .table("prototypes")
        .select("id, workspace_id, current_checkpoint_id")
        .eq("id", prototype_id)
        .limit(1)
        .execute()
    )
    row = resp.data[0] if resp.data else None
    if not row or row.get("current_checkpoint_id") is None:
        logger.warning("backfill_target_no_checkpoint prototype_id=%s", prototype_id)
        return 0, 0
    ok = await backfill_one(
        prototype_id=row["id"],
        workspace_id=row["workspace_id"],
        checkpoint_id=row["current_checkpoint_id"],
    )
    return (1 if ok else 0), 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.backfill_previews")
    parser.add_argument(
        "target",
        help="a prototype id (e.g. 42) or the literal 'all'",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.target == "all":
        updated, total = await backfill_all()
    else:
        try:
            pid = int(args.target)
        except ValueError:
            logger.error("invalid target %r — pass a prototype id or 'all'", args.target)
            return 2
        updated, total = await backfill_target(pid)
    logger.info("backfill_complete updated=%s total=%s", updated, total)
    return 0


if __name__ == "__main__":  # pragma: no cover — process entrypoint
    sys.exit(asyncio.run(_main(sys.argv[1:])))
