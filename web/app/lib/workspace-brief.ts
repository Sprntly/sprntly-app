import { ApiError, briefApi, companiesApi, type Brief, type BriefStatus } from "./api"
import type { WorkspaceCompany } from "./onboarding/types"

const POLL_MS = 2000
const DEFAULT_MAX_MS = 5 * 60 * 1000

export function buildWorkspaceContextMarkdown(workspace: WorkspaceCompany): string {
  const product = workspace.product
  const lines = [
    `# ${workspace.display_name}`,
    "",
    product?.name ? `## Product: ${product.name}` : "",
    product?.website ? `Website: ${product.website}` : "",
    "",
    `Industry: ${workspace.industry ?? "—"}`,
    `Stage: ${workspace.stage ?? "—"}`,
    `Business type: ${workspace.business_type ?? "—"}`,
    workspace.team_size ? `Team size: ${workspace.team_size}` : "",
    workspace.tech_stack?.length ? `Tech stack: ${workspace.tech_stack.join(", ")}` : "",
    "",
    "## KPI tree",
    `North star: ${workspace.kpi_tree.north_star || "—"}${
      workspace.kpi_tree.north_star_description.trim()
        ? ` — ${workspace.kpi_tree.north_star_description.trim()}`
        : ""
    }`,
    ...workspace.kpi_tree.metrics.map(
      (m) =>
        `- ${m.name}${m.description.trim() ? ` — ${m.description.trim()}` : ""}`,
    ),
    "",
    workspace.okrs ? `## OKRs / priorities\n${workspace.okrs}` : "",
    workspace.recent_decisions ? `## Recent decisions\n${workspace.recent_decisions}` : "",
    workspace.dead_ends?.length
      ? `## Known dead ends\n${workspace.dead_ends.join(", ")}`
      : "",
    workspace.biggest_risk ? `## Biggest risk\n${workspace.biggest_risk}` : "",
  ]
  return lines.filter(Boolean).join("\n")
}

/** Register backend dataset for this Supabase workspace slug (idempotent). */
export async function ensureDatasetForWorkspace(workspace: WorkspaceCompany): Promise<void> {
  try {
    await companiesApi.create(workspace.slug, workspace.display_name)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return
    throw e
  }
}

export async function seedWorkspaceContextFiles(workspace: WorkspaceCompany): Promise<void> {
  const md = buildWorkspaceContextMarkdown(workspace)
  const file = new File([md], "sprntly-workspace-context.md", { type: "text/markdown" })
  await companiesApi.uploadFiles(workspace.slug, [file])
}

export async function startBriefGeneration(slug: string): Promise<void> {
  await companiesApi.generate(slug)
}

export async function pollBriefStatus(
  slug: string,
  opts?: { maxMs?: number; onTick?: (status: BriefStatus) => void },
): Promise<BriefStatus> {
  const maxMs = opts?.maxMs ?? DEFAULT_MAX_MS
  const start = Date.now()
  while (Date.now() - start < maxMs) {
    const status = await briefApi.status(slug)
    opts?.onTick?.(status)
    if (status.status === "ready" || status.status === "failed") return status
    await sleep(POLL_MS)
  }
  return { company: slug, status: "generating" }
}

export async function fetchBriefWhenReady(slug: string): Promise<Brief | null> {
  try {
    return await briefApi.current(slug)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null
    throw e
  }
}

export function briefPreviewInsight(brief: Brief): {
  headline: string
  subtitle: string
  tag: string
} | null {
  if (!brief.insights?.length) return null
  const top =
    brief.insights.find((i) => i.is_headline) ??
    [...brief.insights].sort((a, b) => b.confidence - a.confidence)[0]
  return {
    headline: top.headline || top.title,
    subtitle: top.subtitle || top.recommendation?.slice(0, 160) || "",
    tag: top.tag.replace(/_/g, " "),
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
