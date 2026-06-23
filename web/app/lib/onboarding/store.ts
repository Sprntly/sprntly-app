import { generateSlug } from "../onboard-helpers"
import { getSupabase } from "../supabase/client"
import {
  clampStep,
  DEFAULT_FEATURE_FLAGS,
  emptyKpiTree,
  ONBOARDING_STEP_COUNT,
  parseDesignSourcePreference,
  parseFeatureFlags,
  parseKpiTree,
  type FeatureFlags,
  type KpiTree,
  type UserProfile,
  type WorkspaceCompany,
  type WorkspaceProduct,
} from "./types"

function rowToProduct(row: Record<string, unknown>): WorkspaceProduct {
  return {
    id: String(row.id),
    company_id: String(row.company_id),
    name: String(row.name),
    website: (row.website as string | null) ?? null,
    description: (row.description as string | null) ?? null,
    is_primary: Boolean(row.is_primary),
  }
}

function rowToCompany(
  row: Record<string, unknown>,
  product: WorkspaceProduct | null = null,
): WorkspaceCompany {
  return {
    id: String(row.id),
    slug: String(row.slug),
    display_name: String(row.display_name),
    product_description: (row.product_description as string | null) ?? null,
    product,
    industry: (row.industry as string | null) ?? null,
    stage: (row.stage as string | null) ?? null,
    business_type: (row.business_type as string | null) ?? null,
    team_size: row.team_size != null ? Number(row.team_size) : null,
    engineering_capacity:
      row.engineering_capacity != null ? Number(row.engineering_capacity) : null,
    pm_engineer_ratio: (row.pm_engineer_ratio as string | null) ?? null,
    competitors: Array.isArray(row.competitors) ? (row.competitors as string[]) : [],
    tech_stack: Array.isArray(row.tech_stack) ? (row.tech_stack as string[]) : [],
    okrs: (row.okrs as string | null) ?? null,
    recent_decisions: (row.recent_decisions as string | null) ?? null,
    dead_ends: Array.isArray(row.dead_ends) ? (row.dead_ends as string[]) : [],
    biggest_risk: (row.biggest_risk as string | null) ?? null,
    kpi_tree: parseKpiTree(row.kpi_tree),
    feature_flags: parseFeatureFlags(row.feature_flags),
    notification_settings:
      row.notification_settings && typeof row.notification_settings === "object"
        ? (row.notification_settings as Record<string, unknown>)
        : {},
    design_source: parseDesignSourcePreference(row.design_source),
    // Clamp the persisted step into the current step range so existing users
    // mid an older, longer flow (e.g. the removed coworkers step) resume on a
    // valid step instead of crashing.
    onboarding_step: clampStep(Number(row.onboarding_step) || 1),
    onboarding_completed_at: (row.onboarding_completed_at as string | null) ?? null,
  }
}

function rowToProfile(row: Record<string, unknown>): UserProfile {
  return {
    id: String(row.id),
    email: (row.email as string | null) ?? null,
    first_name: (row.first_name as string | null) ?? null,
    last_name: (row.last_name as string | null) ?? null,
    role: (row.role as string | null) ?? null,
    onboarding_step: Number(row.onboarding_step) || 0,
    onboarding_completed_at: (row.onboarding_completed_at as string | null) ?? null,
    skipped_fields: Array.isArray(row.skipped_fields) ? (row.skipped_fields as string[]) : [],
  }
}

export async function fetchUserProfile(userId: string): Promise<UserProfile | null> {
  const supabase = getSupabase()
  const { data, error } = await supabase
    .from("profiles")
    .select(
      "id, email, first_name, last_name, role, onboarding_step, onboarding_completed_at, skipped_fields",
    )
    .eq("id", userId)
    .maybeSingle()
  if (error || !data) return null
  return rowToProfile(data as Record<string, unknown>)
}

export async function updateUserProfile(
  userId: string,
  patch: {
    first_name: string
    last_name: string
    role: string | null
  },
): Promise<UserProfile> {
  const supabase = getSupabase()
  const first = patch.first_name.trim()
  const last = patch.last_name.trim()
  const full_name = [first, last].filter(Boolean).join(" ")

  const { data, error } = await supabase
    .from("profiles")
    .update({
      first_name: first,
      last_name: last,
      full_name: full_name || null,
      role: patch.role?.trim() || null,
      updated_at: new Date().toISOString(),
    })
    .eq("id", userId)
    .select(
      "id, email, first_name, last_name, role, onboarding_step, onboarding_completed_at, skipped_fields",
    )
    .single()

  if (error || !data) {
    throw error ?? new Error("Could not update profile")
  }
  return rowToProfile(data as Record<string, unknown>)
}

export async function fetchPrimaryProduct(
  companyId: string,
): Promise<WorkspaceProduct | null> {
  const supabase = getSupabase()
  const { data, error } = await supabase
    .from("products")
    .select("id, company_id, name, website, description, is_primary")
    .eq("company_id", companyId)
    .eq("is_primary", true)
    .maybeSingle()
  if (error || !data) return null
  return rowToProduct(data as Record<string, unknown>)
}

export async function upsertPrimaryProduct(
  companyId: string,
  input: { name: string; website: string | null; description?: string | null },
): Promise<WorkspaceProduct> {
  const supabase = getSupabase()
  const name = input.name.trim()
  const existing = await fetchPrimaryProduct(companyId)

  if (existing) {
    const { data, error } = await supabase
      .from("products")
      .update({
        name,
        website: input.website,
        description: input.description?.trim() || null,
        updated_at: new Date().toISOString(),
      })
      .eq("id", existing.id)
      .select("id, company_id, name, website, description, is_primary")
      .single()
    if (error || !data) throw error ?? new Error("Could not update product")
    return rowToProduct(data as Record<string, unknown>)
  }

  const { data, error } = await supabase
    .from("products")
    .insert({
      company_id: companyId,
      name,
      website: input.website,
      description: input.description?.trim() || null,
      is_primary: true,
    })
    .select("id, company_id, name, website, description, is_primary")
    .single()
  if (error || !data) throw error ?? new Error("Could not create product")
  return rowToProduct(data as Record<string, unknown>)
}

export async function fetchWorkspaceForUser(
  userId: string,
): Promise<WorkspaceCompany | null> {
  const supabase = getSupabase()
  const { data: membership, error: memErr } = await supabase
    .from("company_members")
    .select("company_id")
    .eq("user_id", userId)
    .limit(1)
    .maybeSingle()
  if (memErr || !membership) return null

  const { data, error } = await supabase
    .from("companies")
    .select("*")
    .eq("id", membership.company_id)
    .maybeSingle()
  if (error || !data) return null
  const companyId = String(data.id)
  const product = await fetchPrimaryProduct(companyId)
  return rowToCompany(data as Record<string, unknown>, product)
}

export async function createWorkspace(input: {
  companyName: string
  productName: string
  productWebsite?: string | null
  /** Optional on create — Claude infers it from the website; confirmed later. */
  industry?: string | null
  /** No longer collected in onboarding — captured later via business context. */
  stage?: string | null
  /** Optional on create — Claude infers it from the website; confirmed later. */
  businessType?: string | null
  teamSize?: number | null
  engineeringCapacity?: number | null
  pmEngineerRatio?: string | null
  competitors?: string[]
  techStack?: string[]
  userId: string
}): Promise<WorkspaceCompany> {
  const supabase = getSupabase()

  // The slug is an opaque, name-independent token that always satisfies the
  // backend slug format. The company name flows into display_name only. On a
  // UNIQUE collision (23505) we regenerate a fresh token and retry.
  for (let i = 0; i < 5; i++) {
    const trySlug = generateSlug()
    const { data: company, error: companyErr } = await supabase
      .from("companies")
      .insert({
        created_by: input.userId,
        slug: trySlug,
        display_name: input.companyName.trim(),
        industry: input.industry ?? null,
        stage: input.stage ?? null,
        business_type: input.businessType ?? null,
        team_size: input.teamSize ?? null,
        engineering_capacity: input.engineeringCapacity ?? null,
        pm_engineer_ratio: input.pmEngineerRatio?.trim() || null,
        competitors: input.competitors ?? [],
        tech_stack: input.techStack ?? [],
        kpi_tree: emptyKpiTree(),
        feature_flags: DEFAULT_FEATURE_FLAGS,
        onboarding_step: 2,
      })
      .select("*")
      .single()
    if (!companyErr && company) {
      const { error: memberErr } = await supabase.from("company_members").insert({
        company_id: company.id,
        user_id: input.userId,
        role: "owner",
      })
      if (memberErr) throw memberErr
      const product = await upsertPrimaryProduct(String(company.id), {
        name: input.productName,
        website: input.productWebsite ?? null,
      })
      await supabase
        .from("profiles")
        .update({ onboarding_step: 2 })
        .eq("id", input.userId)
      return rowToCompany(company as Record<string, unknown>, product)
    }
    if (companyErr?.code !== "23505") throw companyErr ?? new Error("Could not create workspace")
  }
  throw new Error("Could not create workspace — please try again.")
}

export async function updateWorkspace(
  companyId: string,
  patch: Record<string, unknown>,
): Promise<WorkspaceCompany> {
  const supabase = getSupabase()
  const { data, error } = await supabase
    .from("companies")
    .update(patch)
    .eq("id", companyId)
    .select("*")
    .single()
  if (error || !data) throw error ?? new Error("Update failed")
  const product = await fetchPrimaryProduct(companyId)
  return rowToCompany(data as Record<string, unknown>, product)
}

/**
 * Serialize the workspace KpiTree (north_star + flat metrics) into the backend
 * canonical `companies.kpi_tree` shape: a `{ metric, description }` north star
 * plus primary_metrics (first ≤4) and secondary_signals (remainder). Each
 * metric is `{ metric, description }` only — no weights / current / target.
 */
export function serializeKpiTree(tree: KpiTree): Record<string, unknown> {
  const named = tree.metrics.filter((m) => m.name.trim().length > 0)
  const toEntry = (m: { name: string; description: string }) => ({
    metric: m.name.trim(),
    description: m.description.trim(),
  })
  return {
    north_star: {
      metric: tree.north_star.trim(),
      description: tree.north_star_description.trim(),
    },
    primary_metrics: named.slice(0, 4).map(toEntry),
    secondary_signals: named.slice(4, 10).map(toEntry),
  }
}

// `nextStep` defaults to connectors (index 2 — the step after the combined
// product+metrics step, index 1), but every caller currently passes the current
// step explicitly to avoid moving the resume marker during a Settings edit.
export async function saveKpiTree(companyId: string, tree: KpiTree, nextStep = 2) {
  return updateWorkspace(companyId, {
    kpi_tree: serializeKpiTree(tree),
    onboarding_step: clampStep(nextStep),
  })
}

export async function saveStrategicContext(
  companyId: string,
  input: {
    okrs?: string | null
    recent_decisions?: string | null
    dead_ends?: string[]
    biggest_risk?: string | null
  },
  // The strategic-context onboarding page was removed; this only persists from
  // the (dormant) Settings pane now, which passes the current step explicitly.
  nextStep: number = ONBOARDING_STEP_COUNT,
) {
  return updateWorkspace(companyId, {
    okrs: input.okrs?.trim() || null,
    recent_decisions: input.recent_decisions?.trim() || null,
    dead_ends: input.dead_ends ?? [],
    biggest_risk: input.biggest_risk?.trim() || null,
    onboarding_step: clampStep(nextStep),
  })
}

export async function saveFeatureFlags(
  companyId: string,
  flags: FeatureFlags,
  nextStep: number = ONBOARDING_STEP_COUNT,
) {
  return updateWorkspace(companyId, {
    feature_flags: flags,
    onboarding_step: clampStep(nextStep),
  })
}

export async function markSkippedFields(userId: string, fields: string[]) {
  if (!fields.length) return
  const supabase = getSupabase()
  const profile = await fetchUserProfile(userId)
  const merged = [...new Set([...(profile?.skipped_fields ?? []), ...fields])]
  await supabase.from("profiles").update({ skipped_fields: merged }).eq("id", userId)
}

export async function advanceOnboardingStep(companyId: string, step: number) {
  // `step` is a 1-based index into ONBOARDING_STEP_SLUGS; clamp so a caller can
  // never persist an out-of-range marker the resume logic would then crash on.
  return updateWorkspace(companyId, { onboarding_step: clampStep(step) })
}

export async function completeOnboarding(companyId: string, userId: string) {
  const supabase = getSupabase()
  const now = new Date().toISOString()
  // Park the step at the final numbered step on completion (the new flow's last
  // index). `onboarding_completed_at` is what actually gates entry into the app.
  await supabase
    .from("companies")
    .update({ onboarding_step: ONBOARDING_STEP_COUNT, onboarding_completed_at: now })
    .eq("id", companyId)
  await supabase
    .from("profiles")
    .update({ onboarding_step: ONBOARDING_STEP_COUNT, onboarding_completed_at: now })
    .eq("id", userId)
}

export async function sendWorkspaceInvites(
  companyId: string,
  invites: { email: string; role: string }[],
  invitedBy: string,
) {
  if (!invites.length) return
  const supabase = getSupabase()
  const rows = invites.map((i) => ({
    company_id: companyId,
    email: i.email.trim().toLowerCase(),
    role: i.role === "Admin" ? "admin" : "member",
    invited_by: invitedBy,
  }))
  const { error } = await supabase.from("workspace_invites").upsert(rows, {
    onConflict: "company_id,email",
  })
  if (error) throw error
}
