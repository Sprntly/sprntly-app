"""Keep a company's synced GitHub folders current — the half-hourly sweep.

The import route reads a folder once. This module re-reads it on a timer, so a
skill somebody adds to that folder on Tuesday is in the library by Tuesday
without anyone reopening the picker. That is the whole feature.

WHAT COUNTS AS A SKILL IS NOT DECIDED HERE. `github_source.discover_skills` is
the same function the manual picker calls, and it treats every .md file under
the folder as a skill (frontmatter names it, filename stem and first paragraph
as fallbacks). A synced folder therefore imports EVERYTHING markdown inside it,
including a README that happens to live there — the folder the user chose is
the only thing scoping this, which is why the import UI asks for a folder and
refuses to sync a bare repo root. Deliberate, and the reason the picker still
exists for anyone who wants to choose file by file.

Three properties this sweep has to have, because it runs unattended across
every tenant at once:

  - CHEAP WHEN NOTHING CHANGED. `resolve_commit` is one API call; if the head
    sha still matches `last_commit_sha` the source is done. An installation's
    REST budget is 5,000 requests/hour and the Design Agent's codebase map
    spends from the same pocket, so a quiet folder must not cost a tree walk.
  - TENANT-SAFE. Every write is keyed on the `company_id` and `installation_id`
    ON THE SOURCE ROW, both written during an authenticated, company-filtered
    import. The sweep never resolves an installation itself and never takes one
    from anywhere but the row it is processing.
  - INCAPABLE OF FAILING LOUDLY. One company's revoked token, deleted branch or
    GitHub outage must cost that source and nothing else. Every source is
    isolated, the error is recorded on its row, and the sweep continues.

Deletions are NOT mirrored (product decision 2026-08-07): a file removed from
the folder leaves its skill in the library untouched. A background timer that
deletes things people are using is a worse failure than a stale skill, and the
user can delete it themselves. The consequence to know: a renamed skill file
lands as a NEW skill and the old one stays behind under its old name.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app import db
from app.skills import github_source
from app.skills.custom import MAX_SKILL_CONTENT_CHARS, build_skill_archive
from app.skills.loader import list_skills
from app.skills.store import SkillStoreError, store_skill

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """What one source's sync did. `changed` False with no error means the head
    sha was unchanged and nothing needed doing — the common case."""

    source_id: str
    changed: bool = False
    imported: int = 0
    replaced: int = 0
    skipped: list[str] = field(default_factory=list)
    error: str = ""
    commit_sha: str = ""


async def sync_source(source: dict, *, force: bool = False) -> SyncResult:
    """Re-read one synced folder and store whatever it holds now.

    Never raises. Every failure mode — GitHub down, branch deleted, App
    uninstalled, one skill over the character cap — is recorded and returned, so
    the sweep's loop cannot be broken by a single bad source.

    `force` skips the unchanged-SHA short-circuit. The half-hourly sweep never
    sets it — that short-circuit is what makes the sweep cheap — but the "Sync
    now" button does, because a person pressing it after a partial run has no
    other way to make the folder be re-read: the commit has not moved, so the
    cheap path would answer "nothing to do" about a folder they can see is not
    fully imported.
    """
    source_id = str(source.get("id") or "")
    company_id = str(source.get("company_id") or "")
    repo = str(source.get("repo") or "")
    ref = str(source.get("ref") or "")
    path = str(source.get("path") or "")
    installation_id = int(source.get("installation_id") or 0)
    result = SyncResult(source_id=source_id)

    if not (source_id and company_id and repo and installation_id):
        # A row this malformed cannot be synced and cannot be fixed by retrying.
        result.error = "This synced folder is missing its repository details."
        db.record_skill_source_sync(source_id=source_id, error=result.error)
        return result

    # ── the cheap path ──────────────────────────────────────────────────────
    # One request. If the folder's branch head has not moved since the last
    # successful sync there is nothing to discover, and this is what keeps a
    # half-hourly sweep affordable for a tenant with several quiet folders.
    try:
        commit_sha, _branch = github_source.resolve_commit(installation_id, repo, ref or None)
    except github_source.GithubRefNotFound as exc:
        result.error = str(exc)
        db.record_skill_source_sync(source_id=source_id, error=result.error)
        return result
    except github_source.GithubSourceError as exc:
        result.error = str(exc)
        db.record_skill_source_sync(source_id=source_id, error=result.error)
        return result
    except Exception as exc:  # noqa: BLE001 — an unexpected failure is still one source's
        logger.warning("skills.sync resolve failed source=%s", source_id, exc_info=True)
        result.error = f"Couldn't reach GitHub ({type(exc).__name__})."
        db.record_skill_source_sync(source_id=source_id, error=result.error)
        return result

    result.commit_sha = commit_sha
    if not force and commit_sha and commit_sha == str(source.get("last_commit_sha") or ""):
        # Unchanged. Stamp the attempt (so `last_synced_at` shows the folder is
        # being watched, not stalled) and clear any error that has since healed.
        db.record_skill_source_sync(source_id=source_id, commit_sha=commit_sha)
        return result

    # ── the folder actually moved ───────────────────────────────────────────
    try:
        discovery = github_source.discover_skills(
            installation_id, repo, ref or None, subpath=path or ""
        )
    except github_source.GithubSourceError as exc:  # covers GithubRefNotFound
        result.error = str(exc)
        db.record_skill_source_sync(source_id=source_id, error=result.error)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills.sync discover failed source=%s", source_id, exc_info=True)
        result.error = f"Couldn't read the folder from GitHub ({type(exc).__name__})."
        db.record_skill_source_sync(source_id=source_id, error=result.error)
        return result

    result.changed = True
    # Resolved inside the loop-free part so a monkeypatched list_skills in the
    # suite is read once per sync, matching how the route resolves it.
    builtin = set(list_skills())
    workspace_id = source.get("workspace_id") or ""
    actor = str(source.get("created_by") or "")

    for skill in discovery.skills:
        if not skill.importable:
            # Same reasons the picker shows: empty method, no derivable name or
            # description, over the character cap, a file GitHub won't serve.
            result.skipped.append(f"{skill.path or skill.name}: {skill.reason}")
            continue
        data, ext = build_skill_archive(skill.parsed)
        try:
            stored = await store_skill(
                company_id=company_id,
                workspace_id=workspace_id,
                uploader_id=actor,
                uploader_name="GitHub sync",
                name=skill.name,
                description=skill.description,
                parsed=skill.parsed,
                data=data,
                ext=ext,
                builtin_slugs=builtin,
                max_content_chars=MAX_SKILL_CONTENT_CHARS,
                source_id=source_id,
            )
        except SkillStoreError as exc:
            # A per-skill refusal (too large, unusable name, a raced trigger).
            # Costs that skill only — the rest of the folder still lands.
            result.skipped.append(f"{skill.path or skill.name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — a DB/storage blip is one skill's
            logger.warning(
                "skills.sync store failed source=%s path_present=%s",
                source_id, bool(skill.path), exc_info=True,
            )
            result.skipped.append(f"{skill.path or skill.name}: {type(exc).__name__}")
            continue
        if stored.replaced:
            result.replaced += 1
        else:
            result.imported += 1

    # The sha is committed only now. A crash partway through leaves the OLD sha
    # on the row, so the next sweep re-reads the folder and finishes the job —
    # store_skill is create-or-replace, so redoing the skills that already
    # landed is a no-op rather than a duplicate.
    db.record_skill_source_sync(source_id=source_id, commit_sha=commit_sha)
    logger.info(
        "skills_sync_source company_present=%s imported=%s replaced=%s skipped=%s",
        bool(company_id), result.imported, result.replaced, len(result.skipped),
    )
    return result


async def sync_all_sources() -> list[SyncResult]:
    """Sweep every active synced folder, one at a time.

    Sequential on purpose. The work is GitHub I/O against per-installation rate
    limits, and the sweep is not latency-sensitive — a half-hourly job has the
    whole half hour. Running tenants concurrently would buy nothing and would
    make one company's slow repo able to crowd the pool.
    """
    try:
        sources = db.list_active_skill_sources()
    except Exception:  # noqa: BLE001 — the scheduler must survive a DB blip
        logger.warning("skills.sync could not list synced folders", exc_info=True)
        return []

    results: list[SyncResult] = []
    for source in sources or []:
        try:
            results.append(await sync_source(source))
        except Exception:  # noqa: BLE001 — belt and braces; sync_source swallows its own
            logger.exception("skills.sync unexpected failure for source %s", source.get("id"))
    changed = sum(1 for r in results if r.changed)
    if changed:
        logger.info(
            "skills_sync_sweep sources=%s changed=%s imported=%s",
            len(results), changed, sum(r.imported for r in results),
        )
    return results


# ── push-triggered sync ──────────────────────────────────────────────────────
#
# The sweep bounds staleness at half an hour; a push webhook removes most of it.
# When GitHub tells us a branch moved, any synced folder ON that branch can be
# re-read immediately — same sync_source, same tenant rule (every write keyed on
# the source row, the installation id used only to SELECT candidate rows, and it
# comes from a signature-verified webhook payload rather than anything a user
# typed). A folder on a different branch is left to the sweep: its head did not
# move, so an immediate re-read would spend a rate-limited API call to learn
# nothing.


def sources_for_push(
    *,
    installation_id: int,
    repo_full_name: str,
    pushed_branch: str,
    default_branch: str,
) -> list[dict]:
    """The active sources a push event makes stale — the ones to sync NOW.

    A source matches when it lives in the pushed repo AND tracks the pushed
    branch. `ref` empty means "the repo's default branch" (stored untouched at
    import time precisely so a default-branch rename keeps syncing — see the
    migration), so an empty-ref source matches when the push landed on whatever
    GitHub currently calls the default branch. Repo names compare
    case-insensitively (GitHub treats full names that way); branch names
    compare exactly (git refs are case-sensitive).
    """
    if not (installation_id and repo_full_name and pushed_branch):
        return []
    try:
        candidates = db.list_active_skill_sources_for_installation(int(installation_id))
    except Exception:  # noqa: BLE001 — a DB blip costs this push; the sweep still runs
        logger.warning("skills.sync could not list sources for push", exc_info=True)
        return []

    repo_key = repo_full_name.strip().casefold()
    matched: list[dict] = []
    for source in candidates or []:
        if str(source.get("repo") or "").strip().casefold() != repo_key:
            continue
        ref = str(source.get("ref") or "").strip()
        effective_branch = ref or str(default_branch or "").strip()
        if effective_branch and effective_branch == pushed_branch:
            matched.append(source)
    return matched


async def sync_sources_for_push(
    *,
    installation_id: int,
    repo_full_name: str,
    pushed_branch: str,
    default_branch: str,
) -> list[SyncResult]:
    """Sync every folder a push made stale. Sequential, like the sweep, and for
    the same reason — the work draws on one installation's rate budget.

    Never raises: this runs as a fire-and-forget task behind the webhook
    response, where an exception would be an unhandled-task log and nothing
    else. The sweep remains the backstop for anything this misses (a delivery
    GitHub dropped, a push that raced a deploy) — this path only shortens the
    wait, it does not replace the timer.
    """
    results: list[SyncResult] = []
    for source in sources_for_push(
        installation_id=installation_id,
        repo_full_name=repo_full_name,
        pushed_branch=pushed_branch,
        default_branch=default_branch,
    ):
        try:
            results.append(await sync_source(source))
        except Exception:  # noqa: BLE001 — belt and braces; sync_source swallows its own
            logger.exception(
                "skills.sync push-triggered failure for source %s", source.get("id")
            )
    if results:
        logger.info(
            "skills_sync_push sources=%s imported=%s replaced=%s",
            len(results),
            sum(r.imported for r in results),
            sum(r.replaced for r in results),
        )
    return results
