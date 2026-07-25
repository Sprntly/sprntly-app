import type { LlmContextFields } from "../api"
import {
  saveWorkspaceOwnedFields,
  serializeKpiTree,
  updateWorkspace,
  upsertPrimaryProduct,
  type WorkspaceOwnedFields,
} from "./store"
import type { WorkspaceCompany } from "./types"

/**
 * Write an imported context onto the workspace as a PREFILL.
 *
 * Shared by both readers of an uploaded context file — the deterministic
 * heading parse that returns with the upload, and the background LLM
 * extraction that lands while the user is on the connectors step — so an
 * imported value reaches the workspace through exactly one code path no matter
 * which pass found it.
 *
 * THE ONE RULE: an import never overwrites the user. Every field is written
 * only when the workspace has left it empty, which is what makes it safe to
 * run this a second time when the LLM pass finishes — anything the user typed
 * on a step they have already passed stands, and anything the faster
 * deterministic parse already wrote stands too.
 *
 * THREE DESTINATIONS, one call: the company row (PostgREST), the primary
 * product row, and the default `workspaces` row — the last via the onboarding
 * API, because the workspace step's six fields moved there in 2026-07 and a
 * companies patch would land on the dormant columns nothing reads.
 *
 * Prefill, not commitment: later steps seed their inputs from `workspace`, so
 * every value written here is reviewed and editable on the step that owns it.
 * Nothing here is a silently-committed answer.
 *
 * Returns the updated workspace (the same object when there was nothing to
 * write), so callers can push it straight onto onboarding context.
 */
export async function applyImportedContext(
  workspace: WorkspaceCompany,
  fields: LlmContextFields,
): Promise<WorkspaceCompany> {
  const patch: Record<string, unknown> = {}
  const empty = (current: unknown) =>
    current === null || current === undefined || current === ""

  if (fields.company_name && empty(workspace.display_name))
    patch.display_name = fields.company_name
  if (fields.mission && empty(workspace.mission)) patch.mission = fields.mission
  if (fields.strategy && empty(workspace.strategy)) patch.strategy = fields.strategy
  if (fields.portfolio && empty(workspace.portfolio))
    patch.portfolio = fields.portfolio
  if (fields.planning_cycle && empty(workspace.planning_cycle))
    patch.planning_cycle = fields.planning_cycle
  if (fields.prioritization_framework && empty(workspace.prioritization_framework))
    patch.prioritization_framework = fields.prioritization_framework
  if (fields.competitors?.length && !workspace.competitors?.length)
    patch.competitors = fields.competitors

  // The workspace step's block. These five live on the `workspaces` row, NOT on
  // companies — see saveWorkspaceOwnedFields for why a `patch` entry here would
  // write a dormant column and read back the old value.
  const wsFields: WorkspaceOwnedFields = {}
  if (fields.team_scope && empty(workspace.team_scope))
    wsFields.team_scope = fields.team_scope
  if (fields.sizing_methodology && empty(workspace.sizing_methodology))
    wsFields.sizing_methodology = fields.sizing_methodology
  // The company's strategy IS what the workspace step's strategy/roadmap box
  // wants — the same substitution the upload banner on that step already makes.
  if (fields.strategy && empty(workspace.team_strategy))
    wsFields.team_strategy = fields.strategy
  // "Anything else" is the export's catch-all section, capped to what the
  // textarea accepts so an imported value can still be edited in place.
  if (fields.notes && empty(workspace.additional_context))
    wsFields.additional_context = fields.notes.slice(0, 2000)
  // The workspace NAME is mandatory on its step, so leaving it to be typed was
  // the biggest hole in "the rest of setup arrives pre-filled".
  const teamName =
    fields.team_name && empty(workspace.team_name) ? fields.team_name : null

  // Metrics land in the KPI tree (companies.kpi_tree), the same column the
  // metrics step and Settings → KPIs read — otherwise the import extracts them
  // and the "we pre-filled your metrics" promise silently drops them. Only when
  // the tree is still empty, so a user who already picked metrics is untouched.
  const hasKpis =
    (workspace.kpi_tree?.metrics ?? []).some((m) => m.name.trim().length > 0) ||
    Boolean(workspace.kpi_tree?.north_star?.trim())
  if (fields.metrics?.length && !hasKpis) {
    patch.kpi_tree = serializeKpiTree({
      // The prompt names the north star first; treat it as such, and keep the
      // whole set as the pickable metrics the metrics step reads back.
      north_star: fields.metrics[0],
      north_star_description: "",
      metrics: fields.metrics.map((name) => ({ name, description: "" })),
    })
  }

  const product = workspace.product
  // Only the product keys that actually change. `upsertPrimaryProduct` requires
  // a name, so an unconditional call would write a row on every poll even when
  // the import found nothing — build the patch first and skip the call when
  // there is nothing in it.
  const productPatch = {
    ...(fields.product_name && empty(product?.name)
      ? { name: fields.product_name }
      : {}),
    // In this data model the company's site IS the product website (the company
    // step seeds it there), so fall back to company_website when the export
    // only carried that one.
    ...((fields.product_website || fields.company_website) && empty(product?.website)
      ? { website: fields.product_website || fields.company_website }
      : {}),
    ...(fields.surfaces?.length && !product?.surfaces?.length
      ? { surfaces: fields.surfaces }
      : {}),
    ...(fields.monetization && !product?.monetization?.length
      ? { monetization: [fields.monetization] }
      : {}),
    ...(fields.users_description && empty(product?.users_description)
      ? { usersDescription: fields.users_description }
      : {}),
  }

  const hasWorkspaceWrite = Boolean(teamName) || Object.keys(wsFields).length > 0
  if (
    !Object.keys(patch).length &&
    !Object.keys(productPatch).length &&
    !hasWorkspaceWrite
  ) {
    return workspace
  }

  const updated = Object.keys(patch).length
    ? await updateWorkspace(workspace.id, patch)
    : workspace

  // `products_name_nonempty` rejects a blank name, so a product patch with
  // nothing to name the row is deferred to the product step rather than thrown.
  const productName = (
    productPatch.name ??
    product?.name ??
    // The company name the import just wrote, before falling back to the one
    // already on the row — an import-first workspace is created unnamed, so
    // the stored value is usually the blank one.
    (patch.display_name as string | undefined) ??
    workspace.display_name
  ).trim()
  const nextProduct =
    Object.keys(productPatch).length && productName
      ? await upsertPrimaryProduct(workspace.id, {
          // The company name is the same seed the company step uses for a new
          // product.
          name: productName,
          website: productPatch.website ?? product?.website ?? null,
          ...productPatch,
        })
      : workspace.product

  if (!hasWorkspaceWrite) return { ...updated, product: nextProduct }

  // Last, because it is the write most likely to fail (a round-trip through the
  // API rather than PostgREST) and everything above is already safely stored.
  // The name is mandatory server-side; keep whatever the workspace already has
  // when the import didn't name a team.
  await saveWorkspaceOwnedFields(
    teamName ?? workspace.team_name ?? "Default",
    wsFields,
  )
  // Merged locally rather than re-fetched: `updated` was read before that write.
  return {
    ...updated,
    ...(teamName ? { team_name: teamName } : {}),
    ...wsFields,
    product: nextProduct,
  }
}
