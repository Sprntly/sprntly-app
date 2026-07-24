// Per-user insight-type preferences — which insight types a member wants as
// their Top Insights. Read/written straight through PostgREST (same path
// onboarding uses for companies.notification_settings); RLS on
// user_insight_prefs scopes every row to its owner. Backed by the
// user_insight_prefs table (migration 20260723140000).
//
// Empty selection ([] / no row) means "surface everything" — the reader
// default — so the caller shows the workspace's default top 3 unfiltered.
import { getSupabase } from "../supabase/client"
import { cleanInsightTypes, type InsightTypeSlug } from "../insight-types"

export interface InsightPrefs {
  insightTypes: InsightTypeSlug[]
  /** Free-text override, mirroring the company-wide brief_insight_note. */
  note: string | null
}

const EMPTY_PREFS: InsightPrefs = { insightTypes: [], note: null }

/** The current member's saved selection for a company. Returns empty prefs when
 *  they've never picked (no row) — never null, so callers don't special-case. */
export async function fetchInsightPrefs(
  companyId: string,
  userId: string,
): Promise<InsightPrefs> {
  const supabase = getSupabase()
  const { data, error } = await supabase
    .from("user_insight_prefs")
    .select("insight_types, note")
    .eq("company_id", companyId)
    .eq("user_id", userId)
    .maybeSingle()
  if (error || !data) return EMPTY_PREFS
  return {
    insightTypes: cleanInsightTypes((data as { insight_types?: unknown }).insight_types),
    note: ((data as { note?: string | null }).note ?? null) || null,
  }
}

/** Upsert the member's selection. `insightTypes` is cleaned to known slugs
 *  before write so a stale client can't violate the DB check constraint. */
export async function saveInsightPrefs(
  companyId: string,
  userId: string,
  prefs: { insightTypes: string[]; note?: string | null },
): Promise<InsightPrefs> {
  const supabase = getSupabase()
  const insight_types = cleanInsightTypes(prefs.insightTypes)
  const note = prefs.note?.trim() || null
  const { data, error } = await supabase
    .from("user_insight_prefs")
    .upsert(
      {
        company_id: companyId,
        user_id: userId,
        insight_types,
        note,
        updated_at: new Date().toISOString(),
      },
      { onConflict: "company_id,user_id" },
    )
    .select("insight_types, note")
    .single()
  if (error || !data) {
    throw error ?? new Error("Could not save insight preferences")
  }
  return {
    insightTypes: cleanInsightTypes((data as { insight_types?: unknown }).insight_types),
    note: ((data as { note?: string | null }).note ?? null) || null,
  }
}
