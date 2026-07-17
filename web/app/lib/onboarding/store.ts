import { orgInviteApi } from "../api"
import { generateSlug } from "../onboard-helpers"
import { getSupabase } from "../supabase/client"
import {
  clampStep,
  DEFAULT_FEATURE_FLAGS,
  emptyKpiTree,
  ONBOARDING_STEP_COUNT,
  parseAccountType,
  parseCompanyIcp,
  parseCompanyToneVoice,
  parseDesignSourcePreference,
  parseFeatureFlags,
  parseKpiTree,
  parseMetricDefinitions,
  type AccountType,
  type FeatureFlags,
  type KpiTree,
  type MetricDefinition,
  type UserProfile,
  type WorkspaceCompany,
  type WorkspaceProduct,
} from "./types"

const PRODUCT_COLUMNS =
  "id, company_id, name, website, description, is_primary, surfaces, personas, positioning, monetization, users_description, maturity"

function rowToProduct(row: Record<string, unknown>): WorkspaceProduct {
  const strArr = (v: unknown) =>
    Array.isArray(v) ? v.map(String).filter(Boolean) : []
  return {
    id: String(row.id),
    company_id: String(row.company_id),
    name: String(row.name),
    website: (row.website as string | null) ?? null,
    description: (row.description as string | null) ?? null,
    is_primary: Boolean(row.is_primary),
    surfaces: strArr(row.surfaces),
    personas: strArr(row.personas),
    positioning: (row.positioning as string | null) ?? null,
    monetization: strArr(row.monetization),
    users_description: (row.users_description as string | null) ?? null,
    maturity: (row.maturity as string | null) ?? null,
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
    account_type: parseAccountType(row.account_type),
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
    mission: (row.mission as string | null) ?? null,
    strategy: (row.strategy as string | null) ?? null,
    portfolio: (row.portfolio as string | null) ?? null,
    icp: parseCompanyIcp(row.icp),
    tone_voice: parseCompanyToneVoice(row.tone_voice),
    planning_cycle: (row.planning_cycle as string | null) ?? null,
    team_name: (row.team_name as string | null) ?? null,
    team_scope: (row.team_scope as string | null) ?? null,
    prioritization_framework: (row.prioritization_framework as string | null) ?? null,
    sizing_methodology: (row.sizing_methodology as string | null) ?? null,
    team_strategy: (row.team_strategy as string | null) ?? null,
    team_roadmap: (row.team_roadmap as string | null) ?? null,
    decision_process: (row.decision_process as string | null) ?? null,
    additional_context: (row.additional_context as string | null) ?? null,
    business_context_summary:
      (row.business_context_summary as string | null) ?? null,
    business_context_accepted_at:
      (row.business_context_accepted_at as string | null) ?? null,
    metric_definitions: parseMetricDefinitions(row.metric_definitions),
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
    use_platform_key: row.use_platform_key === true,
  }
}

const PROFILE_COLUMNS =
  "id, email, first_name, last_name, role, priorities, timezone, account_type, onboarding_step, onboarding_completed_at, skipped_fields"

function rowToProfile(row: Record<string, unknown>): UserProfile {
  return {
    id: String(row.id),
    email: (row.email as string | null) ?? null,
    first_name: (row.first_name as string | null) ?? null,
    last_name: (row.last_name as string | null) ?? null,
    role: (row.role as string | null) ?? null,
    priorities: (row.priorities as string | null) ?? null,
    timezone: (row.timezone as string | null) ?? null,
    account_type: parseAccountType(row.account_type),
    onboarding_step: Number(row.onboarding_step) || 0,
    onboarding_completed_at: (row.onboarding_completed_at as string | null) ?? null,
    skipped_fields: Array.isArray(row.skipped_fields) ? (row.skipped_fields as string[]) : [],
  }
}

export async function fetchUserProfile(userId: string): Promise<UserProfile | null> {
  const supabase = getSupabase()
  const { data, error } = await supabase
    .from("profiles")
    .select(PROFILE_COLUMNS)
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
    /** "Your priorities" free text; omit to leave unchanged, pass null to clear. */
    priorities?: string | null
    /** IANA timezone; omit to leave unchanged, pass null to clear. */
    timezone?: string | null
    /** Legacy signup choice; omit to leave unchanged. The v6 flow always
     *  writes "company". */
    account_type?: AccountType
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
      ...(patch.priorities !== undefined
        ? { priorities: patch.priorities?.trim() || null }
        : {}),
      ...(patch.timezone !== undefined
        ? { timezone: patch.timezone?.trim() || null }
        : {}),
      ...(patch.account_type !== undefined ? { account_type: patch.account_type } : {}),
      updated_at: new Date().toISOString(),
    })
    .eq("id", userId)
    .select(PROFILE_COLUMNS)
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
    .select(PRODUCT_COLUMNS)
    .eq("company_id", companyId)
    .eq("is_primary", true)
    .maybeSingle()
  if (error || !data) return null
  return rowToProduct(data as Record<string, unknown>)
}

export async function upsertPrimaryProduct(
  companyId: string,
  input: {
    name: string
    website: string | null
    description?: string | null
    /** Registration-spec product fields; omit any to leave it unchanged. */
    surfaces?: string[]
    personas?: string[]
    positioning?: string | null
    monetization?: string[]
    usersDescription?: string | null
    maturity?: string | null
  },
): Promise<WorkspaceProduct> {
  const supabase = getSupabase()
  const name = input.name.trim()
  const existing = await fetchPrimaryProduct(companyId)

  const specFields = {
    ...(input.surfaces !== undefined ? { surfaces: input.surfaces } : {}),
    ...(input.personas !== undefined ? { personas: input.personas } : {}),
    ...(input.positioning !== undefined
      ? { positioning: input.positioning?.trim() || null }
      : {}),
    ...(input.monetization !== undefined ? { monetization: input.monetization } : {}),
    ...(input.usersDescription !== undefined
      ? { users_description: input.usersDescription?.trim() || null }
      : {}),
    ...(input.maturity !== undefined ? { maturity: input.maturity || null } : {}),
  }

  if (existing) {
    const { data, error } = await supabase
      .from("products")
      .update({
        name,
        website: input.website,
        description: input.description?.trim() || null,
        ...specFields,
        updated_at: new Date().toISOString(),
      })
      .eq("id", existing.id)
      .select(PRODUCT_COLUMNS)
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
      ...specFields,
      is_primary: true,
    })
    .select(PRODUCT_COLUMNS)
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
  /** The signup choice, denormalized from profiles.account_type so
   *  company-scoped reads never need a join. */
  accountType?: AccountType | null
  /** Company mission / strategy (optional onboarding fields). */
  mission?: string | null
  strategy?: string | null
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
        account_type: input.accountType ?? "company",
        mission: input.mission?.trim() || null,
        strategy: input.strategy?.trim() || null,
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
      // Staff org invite: if Sprntly invited this email, apply the invite's
      // entitlements (modules, seat limit, prototype, key mode) to the new
      // company. Best-effort — 404 just means self-serve signup (no invite),
      // and any failure must never break onboarding (staff can re-apply from
      // the admin panel).
      try {
        await orgInviteApi.claim()
      } catch {
        /* no pending invite, or transient — onboarding proceeds regardless */
      }
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

/**
 * Set only the weekly-brief weekday inside companies.notification_settings,
 * merge-writing over the CURRENT stored object so the other keys (brief_hour,
 * email_enabled, timezone, Slack config…) are never clobbered. Used by the
 * onboarding team step; the full schedule editor lives in Settings → Comms.
 */
export async function saveNotificationBriefDay(
  companyId: string,
  weekday: number,
): Promise<WorkspaceCompany> {
  const supabase = getSupabase()
  const { data, error } = await supabase
    .from("companies")
    .select("notification_settings")
    .eq("id", companyId)
    .single()
  if (error || !data) throw error ?? new Error("Could not load notification settings")
  const current =
    data.notification_settings && typeof data.notification_settings === "object"
      ? (data.notification_settings as Record<string, unknown>)
      : {}
  return updateWorkspace(companyId, {
    notification_settings: { ...current, brief_weekday: weekday },
  })
}

/**
 * Persist the confirmed metric definitions (define-metrics sub-flow / the
 * Settings KPI pane). Normalized through parseMetricDefinitions so only
 * well-formed `{metric, definition, mapping, baseline}` entries are stored.
 */
export async function saveMetricDefinitions(
  companyId: string,
  definitions: MetricDefinition[],
) {
  return updateWorkspace(companyId, {
    metric_definitions: parseMetricDefinitions(definitions),
  })
}

/**
 * Persist the accepted business-context prose (step 9 "Here's what we
 * learned"). Stamps business_context_accepted_at when `accepted`.
 */
export async function saveBusinessContextSummary(
  companyId: string,
  summary: string,
  accepted: boolean,
) {
  return updateWorkspace(companyId, {
    business_context_summary: summary.trim() || null,
    ...(accepted ? { business_context_accepted_at: new Date().toISOString() } : {}),
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

  // Backfill the brief-delivery timezone from the browser if we never captured
  // one (Google signups carry no timezone in their OAuth metadata, unlike email
  // signups which set it via supabase.auth.signUp). The `.is(null)` guard means
  // we only fill the gap — an explicitly-captured/edited timezone is never
  // clobbered. Best-effort: a failure here must not block entering the app.
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone?.trim()
    if (tz) {
      await supabase
        .from("profiles")
        .update({ timezone: tz })
        .eq("id", userId)
        .is("timezone", null)
    }
  } catch {
    /* no Intl / transient write error — leave timezone null → backend UTC */
  }
}

/**
 * Direct `workspace_invites` upsert. No longer wired into onboarding — team
 * invites now live in Settings → Team (`teamApi.invite` → POST /v1/team/invites,
 * which both stores the invite AND sends the email). Kept for any remaining
 * callers; prefer `teamApi.invite` for new invite flows.
 */
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
