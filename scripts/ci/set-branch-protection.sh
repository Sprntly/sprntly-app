#!/usr/bin/env bash
#
# Make green tests a HARD requirement to merge into main.
#
# The deploy workflows have in-workflow test backstops (a red suite can't reach
# prod even on a direct push), but the PRIMARY gate is branch protection: red
# code should never land on main in the first place. That's a GitHub setting,
# not a file in the repo — this script applies it via the API.
#
# Requires: gh CLI authenticated as a repo admin (`gh auth status`).
# Idempotent: re-running just re-applies the same settings.
#
# Usage:
#   ./scripts/ci/set-branch-protection.sh                 # Sprntly/sprntly-app, main
#   REPO=Sprntly/sprntly-app BRANCH=main ./scripts/ci/set-branch-protection.sh
#
set -euo pipefail

REPO="${REPO:-Sprntly/sprntly-app}"
BRANCH="${BRANCH:-main}"
# enforce_admins: apply the rules to admins too (no bypassing the checks).
# Default on — the whole point is that gates can't be skipped. Set to false to
# keep an admin escape hatch for hotfixes.
ENFORCE_ADMINS="${ENFORCE_ADMINS:-true}"
# Require an approving PR review before merge. Default OFF: you often merge your
# own PRs solo, and GitHub won't let an author approve their own PR — requiring
# a review would deadlock those. Set to 1 once the team reviews each other's PRs.
REQUIRE_REVIEWS="${REQUIRE_REVIEWS:-0}"

# Required status-check contexts = the JOB NAMES that run on EVERY PR.
# These workflows have had their pull_request paths filter removed precisely so
# these checks always report (a required check that never runs blocks the PR
# forever). pytest-integration self-gates to PRs targeting main via a job `if`.
#
# NOTE: prototype-runtime's `test` job is intentionally NOT required — it is
# still path-filtered, so requiring it would deadlock PRs that don't touch
# prototype-runtime/. Add it here only if you also drop its pull_request paths.
CONTEXTS=(
  "pytest-fast"          # test-backend.yml  — backend fast lane
  "pytest-integration"   # test-backend.yml  — real-build lane (PRs→main)
  "vitest"               # test-web.yml      — vitest + typecheck + build
  "pytest-agent"         # test-agent.yml    — ds-agent server tests
  "malware-scan"         # security-guard.yml
)

echo "Applying branch protection to ${REPO}@${BRANCH}"
printf '  required check: %s\n' "${CONTEXTS[@]}"

# Build the JSON payload.
checks_json=$(printf '%s\n' "${CONTEXTS[@]}" | jq -R . | jq -s '{strict: true, contexts: .}')

if [ "$REQUIRE_REVIEWS" -gt 0 ] 2>/dev/null; then
  reviews_json="{\"required_approving_review_count\": ${REQUIRE_REVIEWS}, \"dismiss_stale_reviews\": true}"
else
  reviews_json="null"
fi

gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<JSON
{
  "required_status_checks": ${checks_json},
  "enforce_admins": ${ENFORCE_ADMINS},
  "required_pull_request_reviews": ${reviews_json},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false,
  "required_conversation_resolution": true
}
JSON

echo "Done. Verify: gh api repos/${REPO}/branches/${BRANCH}/protection | jq '.required_status_checks.contexts'"
