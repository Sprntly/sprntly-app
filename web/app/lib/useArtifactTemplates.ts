"use client"

/**
 * The format library's data layer: one fetch, one poller, one grouping rule.
 *
 * `web/` is a static export — no server component may fetch this and no route
 * handler exists to proxy it, so the compile is watched from the browser. The
 * poller is `lib/poll.ts`'s `pollUntil`, not a new one: it is visibility-aware
 * (a backgrounded tab throttles setTimeout to ~1/min, so a plain loop stalls
 * exactly while a compile finishes) and its budget is wall-clock, so a
 * refocused tab times out when it should rather than after N ticks.
 *
 * Three decisions worth stating because they are not obvious from the types:
 *
 * 1. POLL THE LIST, NOT THE ROW. One request covers every compiling row, and
 *    the list is also what a mid-run refresh re-reads — status lives on the
 *    row server-side, so nothing here is inferred from client state. A reload
 *    with two formats compiling restarts one poll, not two.
 *
 * 2. THE FIRST TWO POLL FAILURES ARE SWALLOWED. A flaky minute on hotel wifi
 *    should not paint an error over a library that is still on screen and still
 *    correct; the third consecutive failure raises `connectionLost` once, and
 *    compiling continues server-side regardless. `navigator.onLine` is
 *    deliberately not consulted — it reports "online" behind a captive portal,
 *    which is the one case that matters.
 *
 * 3. THE FETCH IS KEYED ON `activeWorkspace?.id`. Formats are COMPANY-scoped,
 *    so a workspace switch inside one company re-reads the same library — but a
 *    switch that crosses companies must not leave the previous company's
 *    formats on screen for even one frame, and the id is the only signal that
 *    fires for both. Rows are cleared and in-flight polls cancelled on change.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useWorkspace } from "../context/WorkspaceContext"
import { pollUntil } from "./poll"
import {
  artifactTemplatesApi,
  type ArtifactTemplate,
  type ArtifactTemplateList,
  type ArtifactTemplateType,
  type GenerationEnabled,
} from "./api"
import { ARTIFACT_TYPE_IDS } from "./compileNotes"

/** Statuses that mean "the server is still working on this row". */
const IN_FLIGHT = new Set(["pending", "compiling"])

/** 3s between polls — a compile is tens of seconds, and the row is on screen
 *  the whole time, so a tighter loop buys nothing but requests. */
export const COMPILE_POLL_INTERVAL_MS = 3_000

/** 5 minutes of wall clock. Past that the row keeps its Checking badge but says
 *  so and offers Check again — a spinner that runs forever is the one outcome
 *  that never resolves itself. */
export const COMPILE_POLL_MAX_MS = 5 * 60_000

/** How many consecutive poll failures before we say the connection is gone. */
const CONNECTION_LOST_AFTER = 3

/** Degraded default for `generation_enabled`: used ONLY when the field is
 *  absent from the response (an older backend). A response that carries the
 *  field is authoritative even when every value is false — which is the state
 *  today, and the reason each group renders its "not wired yet" note. */
const DEFAULT_LIVE_TYPES: ReadonlySet<ArtifactTemplateType> = new Set(["prd"])

export type ArtifactTemplateGroups = Record<
  ArtifactTemplateType,
  ArtifactTemplate[]
>

function emptyGroups(): ArtifactTemplateGroups {
  return { prd: [], tickets: [], impl_spec: [] }
}

/** Active first, then newest first — the two things a reader needs from the
 *  order. Rows with no `created_at` sort last within their half rather than
 *  jumping to the top on a null-as-zero comparison. */
export function groupTemplates(
  rows: readonly ArtifactTemplate[],
): ArtifactTemplateGroups {
  const groups = emptyGroups()
  for (const row of rows) {
    const type = row.artifact_type
    if (type in groups) groups[type].push(row)
  }
  for (const type of ARTIFACT_TYPE_IDS) {
    groups[type].sort((a, b) => {
      if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
      const at = a.created_at ? Date.parse(a.created_at) : NaN
      const bt = b.created_at ? Date.parse(b.created_at) : NaN
      const aBad = Number.isNaN(at)
      const bBad = Number.isNaN(bt)
      if (aBad !== bBad) return aBad ? 1 : -1
      if (aBad && bBad) return 0
      return bt - at
    })
  }
  return groups
}

/** Which types honour a custom format today. An explicit all-false response is
 *  respected as-is; only a MISSING field falls back. */
export function liveTypesFrom(
  enabled: GenerationEnabled | null | undefined,
): ReadonlySet<ArtifactTemplateType> {
  if (!enabled || typeof enabled !== "object") return DEFAULT_LIVE_TYPES
  const live = new Set<ArtifactTemplateType>()
  for (const type of ARTIFACT_TYPE_IDS) {
    if (enabled[type] === true) live.add(type)
  }
  return live
}

export type UseArtifactTemplates = {
  groups: ArtifactTemplateGroups
  /** First load only — the screen shows skeletons. */
  loading: boolean
  /** A later refresh: rows stay on screen, the section is `aria-busy`. */
  refreshing: boolean
  error: string | null
  liveTypes: ReadonlySet<ArtifactTemplateType>
  /** Rows the server is still checking. */
  pollingIds: ReadonlySet<string>
  /** Rows still in flight when the poll budget ran out. */
  stalledIds: ReadonlySet<string>
  connectionLost: boolean
  /** One section-level live-region string; transitions only. */
  announcement: string | null
  /** Background refresh — never blanks the list. */
  refresh: () => Promise<void>
  /** Retry after a failed FIRST load: back to the skeleton state. */
  retry: () => void
  /** Merge one row the caller just created or changed, and restart the poll if
   *  it is still compiling. */
  upsert: (row: ArtifactTemplate) => void
  /** Apply an activation locally before the refetch lands: every other row in
   *  the group loses `is_active`, this one gains it. Patched, never guessed —
   *  the caller only calls this on a 200. */
  applyActivation: (row: ArtifactTemplate) => void
  /** Drop a row the server confirmed is gone (delete, or a 404 on any action). */
  drop: (id: string) => void
}

export function useArtifactTemplates(opts?: {
  /** Fired once per row when it finishes compiling clean, so the screen can
   *  toast — the user may have navigated away from the section by then. */
  onReady?: (row: ArtifactTemplate) => void
}): UseArtifactTemplates {
  const { activeWorkspace } = useWorkspace()
  const workspaceId = activeWorkspace?.id ?? null

  const [templates, setTemplates] = useState<ArtifactTemplate[]>([])
  const [liveTypes, setLiveTypes] =
    useState<ReadonlySet<ArtifactTemplateType>>(DEFAULT_LIVE_TYPES)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [connectionLost, setConnectionLost] = useState(false)
  const [stalledIds, setStalledIds] = useState<ReadonlySet<string>>(new Set())
  const [announcement, setAnnouncement] = useState<string | null>(null)
  // `retryTick` re-arms the load effect after a failed first load; `epochRef`
  // is the value every async continuation compares before touching state. It is
  // bumped by the load effect itself, so a WORKSPACE change cancels the running
  // poll for free — which is the case that matters, since a switch crossing
  // companies must not paint the previous company's rows for even one frame.
  const [retryTick, setRetryTick] = useState(0)

  const epochRef = useRef(0)
  const pollingRef = useRef(false)
  // Last seen status per row id — the diff that decides what to announce. Held
  // in a ref, not state: it must not itself trigger a render.
  const statusRef = useRef<Map<string, string>>(new Map())
  const onReadyRef = useRef(opts?.onReady)
  onReadyRef.current = opts?.onReady

  /** Fold a list response into state and announce anything that just finished.
   *  Returns the rows so the poller can decide whether to keep going. */
  const applyList = useCallback(
    (payload: ArtifactTemplateList): ArtifactTemplate[] => {
      const rows = Array.isArray(payload?.templates) ? payload.templates : []
      const previous = statusRef.current
      const next = new Map<string, string>()
      const becameReady: ArtifactTemplate[] = []
      for (const row of rows) {
        next.set(row.id, row.compile_status)
        const before = previous.get(row.id)
        if (
          before != null &&
          IN_FLIGHT.has(before) &&
          row.compile_status === "ready"
        ) {
          becameReady.push(row)
        }
      }
      statusRef.current = next
      setTemplates(rows)
      setLiveTypes(liveTypesFrom(payload?.generation_enabled))
      // A row that finished is no longer stalled, whatever it finished as.
      setStalledIds((prev) => {
        if (prev.size === 0) return prev
        const kept = new Set<string>()
        for (const row of rows) {
          if (prev.has(row.id) && IN_FLIGHT.has(row.compile_status)) {
            kept.add(row.id)
          }
        }
        return kept.size === prev.size ? prev : kept
      })
      if (becameReady.length > 0) {
        // ONE live region for the section, so N finishing rows are one
        // announcement rather than a wall of them.
        setAnnouncement(
          becameReady.length === 1
            ? `${becameReady[0].name} is ready to activate.`
            : `${becameReady.length} formats are ready to activate.`,
        )
        for (const row of becameReady) onReadyRef.current?.(row)
      }
      return rows
    },
    [],
  )

  /** Watch the list until nothing is in flight. Only ever one loop at a time —
   *  a second upload while the first is compiling joins the existing poll,
   *  because the poll reads the whole list anyway. */
  const startPolling = useCallback(() => {
    if (pollingRef.current) return
    pollingRef.current = true
    const mine = epochRef.current
    let failures = 0
    void pollUntil<ArtifactTemplate[] | null>({
      intervalMs: COMPILE_POLL_INTERVAL_MS,
      maxMs: COMPILE_POLL_MAX_MS,
      isCancelled: () => epochRef.current !== mine,
      fetchStatus: async () => {
        try {
          const payload = await artifactTemplatesApi.list()
          if (epochRef.current !== mine) return null
          failures = 0
          setConnectionLost(false)
          return applyList(payload)
        } catch {
          // null means "we don't know", so `isDone` stays false and the loop
          // retries at the interval instead of declaring the compile finished.
          failures += 1
          if (failures >= CONNECTION_LOST_AFTER && epochRef.current === mine) {
            setConnectionLost(true)
          }
          return null
        }
      },
      isDone: (rows) =>
        rows != null && rows.every((r) => !IN_FLIGHT.has(r.compile_status)),
    })
      .then((rows) => {
        if (epochRef.current !== mine) return
        // Budget exhausted with rows still compiling: keep the badge honest and
        // hand the user a Check again button rather than an eternal spinner.
        if (rows != null) {
          const stuck = rows
            .filter((r) => IN_FLIGHT.has(r.compile_status))
            .map((r) => r.id)
          if (stuck.length > 0) setStalledIds(new Set(stuck))
        }
      })
      .finally(() => {
        pollingRef.current = false
      })
  }, [applyList])

  // First load (and every workspace change). Clears rows FIRST so a company
  // switch cannot show the previous company's library while the fetch is out.
  useEffect(() => {
    epochRef.current += 1
    const mine = epochRef.current
    let cancelled = false
    setLoading(true)
    setError(null)
    setConnectionLost(false)
    setAnnouncement(null)
    setTemplates([])
    setStalledIds(new Set())
    statusRef.current = new Map()
    artifactTemplatesApi
      .list()
      .then((payload) => {
        if (cancelled || epochRef.current !== mine) return
        const rows = applyList(payload)
        if (rows.some((r) => IN_FLIGHT.has(r.compile_status))) startPolling()
      })
      .catch(() => {
        if (cancelled || epochRef.current !== mine) return
        // One string, not the server's — a failed library read has exactly one
        // useful next step and the reason never changes it.
        setError(
          "We couldn't load your document formats. Check your connection and try again.",
        )
      })
      .finally(() => {
        if (!cancelled && epochRef.current === mine) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // `retryTick` re-arms this after a failed first load; applyList and
    // startPolling are stable.
  }, [workspaceId, retryTick, applyList, startPolling])

  // Cancel any in-flight poll when the section unmounts. The loop compares
  // `epochRef`, so moving it past every live value is the cancel.
  useEffect(
    () => () => {
      epochRef.current = -1
    },
    [],
  )

  const refresh = useCallback(async () => {
    const mine = epochRef.current
    setRefreshing(true)
    try {
      const payload = await artifactTemplatesApi.list()
      if (epochRef.current !== mine) return
      setConnectionLost(false)
      const rows = applyList(payload)
      if (rows.some((r) => IN_FLIGHT.has(r.compile_status))) startPolling()
    } catch {
      // A background refresh that fails leaves the rows alone: they are the
      // last thing the server told us, and blanking them would be a lie about
      // what the company has.
    } finally {
      if (epochRef.current === mine) setRefreshing(false)
    }
  }, [applyList, startPolling])

  const retry = useCallback(() => {
    setRetryTick((n) => n + 1)
  }, [])

  const upsert = useCallback(
    (row: ArtifactTemplate) => {
      setTemplates((prev) =>
        prev.some((r) => r.id === row.id)
          ? prev.map((r) => (r.id === row.id ? row : r))
          : [row, ...prev],
      )
      statusRef.current.set(row.id, row.compile_status)
      if (IN_FLIGHT.has(row.compile_status)) startPolling()
    },
    [startPolling],
  )

  const applyActivation = useCallback((row: ArtifactTemplate) => {
    setTemplates((prev) =>
      prev.map((r) =>
        r.id === row.id
          ? row
          : r.artifact_type === row.artifact_type
            ? { ...r, is_active: false }
            : r,
      ),
    )
  }, [])

  const drop = useCallback((id: string) => {
    setTemplates((prev) => prev.filter((r) => r.id !== id))
    statusRef.current.delete(id)
  }, [])

  const groups = useMemo(() => groupTemplates(templates), [templates])
  const pollingIds = useMemo(
    () =>
      new Set(
        templates.filter((r) => IN_FLIGHT.has(r.compile_status)).map((r) => r.id),
      ),
    [templates],
  )

  return {
    groups,
    loading,
    refreshing,
    error,
    liveTypes,
    pollingIds,
    stalledIds,
    connectionLost,
    announcement,
    refresh,
    retry,
    upsert,
    applyActivation,
    drop,
  }
}
