"use client"

import { useEffect, useRef } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { evidenceApi } from "../../lib/api"

/**
 * Shell-mounted (AppShell) hook — the single, page-agnostic wiring point for
 * the internal deep-link URL params: `?prd={id}`, `?evidence={id}`,
 * `?ticket={key}`. Per-type params, not a generic `?artifact=type:id` scheme
 * (confirmed choice — see the ticket this shipped under).
 *
 * URL → drawer: on arrival (or a param change), resolves the param and opens
 * the artifact via the SAME `openPrdTab` entry point every existing "view a
 * PRD/Evidence" affordance already uses (brief cards, Artifacts, the Slack
 * "PRD ready" ping's pre-existing `?prd=` convention on ChatScreen — this hook
 * supersedes/centralizes that so it works from ANY `(app)` page, not just `/`;
 * see ChatScreen's now-superseded PRD-deep-link effect). `openPrdTab` always
 * routes to `/` (that's where the content panel lives — an existing,
 * unrelated-to-this-ticket architectural fact, not a limitation introduced
 * here), so "any starting page" is satisfied by construction: paste the URL
 * on `/backlog`, `/settings`, wherever, and it lands you on `/` with the
 * right drawer open.
 *
 * Tenant isolation is NOT this hook's job — it just calls the same
 * authenticated GET routes every other artifact-open path calls
 * (`GET /v1/evidence/{id}` here; `GET /v1/prd/{id}` inside `loadPrdById`,
 * invoked by ChatScreen's `kind: "load"` handling). A foreign-tenant id 404s
 * there exactly as it does for every other caller, and this hook simply does
 * nothing further on that failure — no content is ever synthesized client-side.
 *
 * drawer → URL: reflects the panel's currently-shown artifact id back onto the
 * address bar, so "copy the current URL" reproduces the view. Scoped to the
 * cases where the shown artifact's own id is reliably known (see
 * `content.evidenceId`'s doc comment for the one documented gap: an
 * evidence-first open via a brief/insight click, as opposed to a URL open or
 * an Artifacts-library open, does not currently thread an id into shared
 * state, so the URL simply won't gain `?evidence=` for that specific
 * interaction — never wrong content, just a param that stays absent).
 *
 * Tickets have no distinct "focused ticket" state anywhere in the app (the
 * Tickets tab is a list, not a single-ticket detail view) — see the ticket's
 * write-up for the full reasoning. So: `?ticket={key}` OPENS correctly (the
 * key's embedded prd_id — `ticketKeyFor`'s `prd-{prdId}-{storyId}` format,
 * web/app/components/shared/TicketDetail.tsx — is parsed client-side, no
 * backend round-trip needed) and switches to the Tickets tab; the reflect
 * direction (drawer → URL) for the Tickets tab writes `?prd={id}` rather than
 * a `?ticket=`, since there is no "current ticket" to name. Flagged as a
 * documented, deliberate scope cut, not an oversight.
 */

const PRD_PARAM = "prd"
const EVIDENCE_PARAM = "evidence"
const TICKET_PARAM = "ticket"

/** `prd-{prdId}-{storyId}` — ticketKeyFor's format (TicketDetail.tsx). A
 *  legacy id-less key (`prd-{prdId}-{title-slug}`) still matches (the slug is
 *  just an opaque suffix here) since only the prd_id segment is needed to open
 *  the right PRD + Tickets tab. */
const TICKET_KEY_RE = /^prd-(\d+)-/

function parsePositiveInt(raw: string | null): number | null {
  if (!raw) return null
  const n = Number(raw)
  return Number.isInteger(n) && n > 0 ? n : null
}

export function useArtifactUrlSync() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const { openPrdTab, contentPanelTab } = useNavigation()
  const { content, setContent } = useContent()

  // ── URL → drawer (one-shot per distinct param value; re-arms when the
  // param is removed — mirrors the existing `?new=1` / legacy `?prd=`
  // consumption latch in ChatScreen) ──────────────────────────────────────
  const consumedRef = useRef<{ prd: string | null; evidence: string | null; ticket: string | null }>(
    { prd: null, evidence: null, ticket: null },
  )
  // True from the moment a URL param triggers an open until the panel
  // actually lands (contentPanelTab becomes non-null) — guards the drawer→URL
  // effect below from stripping the very param that's mid-open (the panel
  // open is deferred past the `/` navigation; see NavigationContext.openPrdTab
  // + ChatScreen's `prdPanelPending`), so a `?prd=` link never gets its own
  // param yanked out from under it before it visibly opens.
  const pendingUrlOpenRef = useRef(false)
  // The (briefId,insightIndex) an in-flight `?evidence=` open resolved to —
  // lets the id we stamp on `content.evidenceId` be trusted only while
  // `content.prdMeta` still matches (see that field's doc comment).
  const urlEvidenceMetaRef = useRef<string | null>(null)

  useEffect(() => {
    const raw = searchParams.get(PRD_PARAM)
    if (!raw) {
      consumedRef.current.prd = null
      return
    }
    if (consumedRef.current.prd === raw) return
    consumedRef.current.prd = raw
    const prdId = parsePositiveInt(raw)
    if (prdId == null) return
    pendingUrlOpenRef.current = true
    openPrdTab({ title: "PRD", source: { kind: "load", prdId, meta: null } })
  }, [searchParams, openPrdTab])

  useEffect(() => {
    const raw = searchParams.get(EVIDENCE_PARAM)
    if (!raw) {
      consumedRef.current.evidence = null
      return
    }
    if (consumedRef.current.evidence === raw) return
    consumedRef.current.evidence = raw
    const evidenceId = parsePositiveInt(raw)
    if (evidenceId == null) return
    pendingUrlOpenRef.current = true
    void evidenceApi
      .get(evidenceId)
      .then((row) => {
        urlEvidenceMetaRef.current = `${row.brief_id}:${row.insight_index}`
        setContent({ evidenceId: row.id })
        openPrdTab({
          title: row.title || "Evidence",
          source: {
            kind: "evidence",
            meta: { briefId: row.brief_id, insightIndex: row.insight_index },
            detail: null,
          },
        })
      })
      .catch(() => {
        // 404 (missing id) or cross-tenant (same 404 — no existence
        // disclosure, per require_owned_evidence): no content, no crash.
        pendingUrlOpenRef.current = false
      })
  }, [searchParams, openPrdTab, setContent])

  // Once the ticket's PRD is loaded, switch the panel to the Tickets tab.
  const { openContentPanel } = useNavigation()
  const pendingTicketPrdRef = useRef<number | null>(null)

  useEffect(() => {
    const raw = searchParams.get(TICKET_PARAM)
    if (!raw) {
      consumedRef.current.ticket = null
      return
    }
    if (consumedRef.current.ticket === raw) return
    consumedRef.current.ticket = raw
    const m = TICKET_KEY_RE.exec(raw)
    const prdId = m ? Number(m[1]) : null
    if (prdId == null || !Number.isInteger(prdId) || prdId <= 0) return
    pendingUrlOpenRef.current = true
    pendingTicketPrdRef.current = prdId
    openPrdTab({ title: "PRD", source: { kind: "load", prdId, meta: null } })
  }, [searchParams, openPrdTab])

  useEffect(() => {
    if (pendingTicketPrdRef.current == null) return
    if (content.prd?.prd_id !== pendingTicketPrdRef.current) return
    pendingTicketPrdRef.current = null
    openContentPanel("tickets")
  }, [content.prd?.prd_id, openContentPanel])

  // Clear a stale `content.evidenceId` once the shown insight has moved on
  // from the one our URL-driven open resolved (an interactive "View Evidence"
  // click on a DIFFERENT insight after a `?evidence=` open must not leave the
  // OLD id reflected back onto the URL for the NEW doc).
  useEffect(() => {
    const metaKey = content.prdMeta ? `${content.prdMeta.briefId}:${content.prdMeta.insightIndex}` : null
    if (content.evidenceId != null && metaKey !== urlEvidenceMetaRef.current) {
      setContent({ evidenceId: null })
    }
  }, [content.prdMeta, content.evidenceId, setContent])

  // ── drawer → URL ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (contentPanelTab != null) pendingUrlOpenRef.current = false
    if (pendingUrlOpenRef.current) return // mid-open from a URL param — don't fight it

    const params = new URLSearchParams(searchParams.toString())
    const had = {
      prd: params.get(PRD_PARAM),
      evidence: params.get(EVIDENCE_PARAM),
      ticket: params.get(TICKET_PARAM),
    }

    if (contentPanelTab == null) {
      if (!had.prd && !had.evidence && !had.ticket) return
      params.delete(PRD_PARAM)
      params.delete(EVIDENCE_PARAM)
      params.delete(TICKET_PARAM)
      router.replace(`${pathname}${params.toString() ? `?${params}` : ""}`, { scroll: false })
      return
    }

    // Open on some tab: reflect exactly the tab's own artifact id. `tickets`
    // has no distinct focused-ticket state (see the module doc comment) so it
    // reflects the PRD id, same as the `prd` tab.
    const want =
      contentPanelTab === "evidence" && content.evidenceId != null
        ? { key: EVIDENCE_PARAM, value: String(content.evidenceId) }
        : (contentPanelTab === "prd" || contentPanelTab === "tickets") && content.prd?.prd_id
          ? { key: PRD_PARAM, value: String(content.prd.prd_id) }
          : null

    // Nothing resolved yet (e.g. still generating, id not known client-side) —
    // leave the URL exactly as-is rather than stripping an in-flight param.
    if (!want) return

    // Already exactly right (this one param set, the other two absent)? Skip
    // the replace so we don't fight the URL-driven consumption effects above
    // (which also run off `searchParams` and would otherwise see churn).
    const alreadyCorrect =
      had[want.key as "prd" | "evidence" | "ticket"] === want.value
      && Object.entries(had).every(([k, v]) => k === want.key || v == null)
    if (alreadyCorrect) return

    params.delete(PRD_PARAM)
    params.delete(EVIDENCE_PARAM)
    params.delete(TICKET_PARAM)
    params.set(want.key, want.value)
    router.replace(`${pathname}?${params}`, { scroll: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentPanelTab, content.prd?.prd_id, content.evidenceId, pathname, router])
}
