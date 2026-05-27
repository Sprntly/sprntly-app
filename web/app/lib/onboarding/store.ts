import { suggestedSlug } from "../onboard-helpers"
import { getSupabase } from "../supabase/client"
import {
  DEFAULT_FEATURE_FLAGS,
  emptyKpiTree,
  parseFeatureFlags,
  parseKpiTree,
  type FeatureFlags,
  type KpiTree,
  type UserProfile,
  type WorkspaceCompany,
} from "./types"

function rowToCompany(row: Record<string, unknown>): WorkspaceCompany {
  return {
    id: String(row.id),
    slug: String(row.slug),
    display_name: String(row.display_name),
    product_description: (row.product_description as string | null) ?? null,
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
    onboarding_step: Number(row.onboarding_step) || 1,
    onboarding_completed_at: (row.onboarding_completed_at as string | null) ?? null,
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
  return {
    id: data.id,
    email: data.email,
    first_name: data.first_name,
    last_name: data.last_name,
    role: data.role,
    onboarding_step: data.onboarding_step ?? 0,
    onboarding_completed_at: data.onboarding_completed_at,
    skipped_fields: Array.isArray(data.skipped_fields) ? data.skipped_fields : [],
  }
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
  return rowToCompany(data as Record<string, unknown>)
}

export async function createWorkspace(input: {
  companyName: string
  productDescription: string
  industry: string
  stage: string
  businessType: string
  teamSize?: number | null
  engineeringCapacity?: number | null
  pmEngineerRatio?: string | null
  competitors?: string[]
  techStack?: string[]
  userId: string
}): Promise<WorkspaceCompany> {
  const supabase = getSupabase()
  let slug = suggestedSlug(input.companyName)
  if (slug.length < 2) slug = "workspace"

  for (let i = 0; i < 5; i++) {
    const trySlug = i === 0 ? slug : `${slug}-${i + 1}`
    const { data: company, error: companyErr } = await supabase
      .from("companies")
      .insert({
        slug: trySlug,
        display_name: input.companyName.trim(),
        product_description: input.productDescription.trim(),
        industry: input.industry,
        stage: input.stage,
        business_type: input.businessType,
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
      await supabase
        .from("profiles")
        .update({ onboarding_step: 2 })
        .eq("id", input.userId)
      return rowToCompany(company as Record<string, unknown>)
    }
    if (companyErr?.code !== "23505") throw companyErr ?? new Error("Could not create workspace")
  }
  throw new Error("Could not create workspace — try a different company name.")
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
  return rowToCompany(data as Record<string, unknown>)
}

export async function saveKpiTree(companyId: string, tree: KpiTree, nextStep = 3) {
  return updateWorkspace(companyId, { kpi_tree: tree, onboarding_step: nextStep })
}

export async function saveStrategicContext(
  companyId: string,
  input: {
    okrs?: string | null
    recent_decisions?: string | null
    dead_ends?: string[]
    biggest_risk?: string | null
  },
  nextStep = 4,
) {
  return updateWorkspace(companyId, {
    okrs: input.okrs?.trim() || null,
    recent_decisions: input.recent_decisions?.trim() || null,
    dead_ends: input.dead_ends ?? [],
    biggest_risk: input.biggest_risk?.trim() || null,
    onboarding_step: nextStep,
  })
}

export async function saveFeatureFlags(
  companyId: string,
  flags: FeatureFlags,
  nextStep = 6,
) {
  return updateWorkspace(companyId, { feature_flags: flags, onboarding_step: nextStep })
}

export async function markSkippedFields(userId: string, fields: string[]) {
  if (!fields.length) return
  const supabase = getSupabase()
  const profile = await fetchUserProfile(userId)
  const merged = [...new Set([...(profile?.skipped_fields ?? []), ...fields])]
  await supabase.from("profiles").update({ skipped_fields: merged }).eq("id", userId)
}

export async function advanceOnboardingStep(companyId: string, step: number) {
  return updateWorkspace(companyId, { onboarding_step: step })
}

export async function completeOnboarding(companyId: string, userId: string) {
  const supabase = getSupabase()
  const now = new Date().toISOString()
  await supabase
    .from("companies")
    .update({ onboarding_step: 8, onboarding_completed_at: now })
    .eq("id", companyId)
  await supabase
    .from("profiles")
    .update({ onboarding_step: 8, onboarding_completed_at: now })
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
