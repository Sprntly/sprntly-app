import type { LlmContextFields } from "../api"
import { serializeKpiTree, updateWorkspace, upsertPrimaryProduct } from "./store"
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
  if (fields.team_scope && empty(workspace.team_scope))
    patch.team_scope = fields.team_scope
  if (fields.competitors?.length && !workspace.competitors?.length)
    patch.competitors = fields.competitors

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

  if (!Object.keys(patch).length && !Object.keys(productPatch).length) {
    return workspace
  }

  const updated = Object.keys(patch).length
    ? await updateWorkspace(workspace.id, patch)
    : workspace
  if (!Object.keys(productPatch).length) {
    return { ...updated, product: workspace.product }
  }

  const nextProduct = await upsertPrimaryProduct(workspace.id, {
    // Required by the upsert, so fall back through what we already have. The
    // company name is the same seed the company step uses for a new product.
    name: productPatch.name ?? product?.name ?? workspace.display_name,
    website: productPatch.website ?? product?.website ?? null,
    ...productPatch,
  })
  return { ...updated, product: nextProduct }
}
