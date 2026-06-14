"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  designAgentApi,
  withAuthRetry,
  type BriefPrototypeMapEntry,
} from "../../lib/api"

export type BriefPrototypeMapResult = {
  entriesByInsight: Map<number, BriefPrototypeMapEntry>
  loading: boolean
  error: boolean
  refetch: () => void
}

export function useBriefPrototypeMap(
  briefId: number | null,
): BriefPrototypeMapResult {
  const [entriesByInsight, setEntriesByInsight] = useState<
    Map<number, BriefPrototypeMapEntry>
  >(new Map())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  // Incrementing this triggers a re-fetch via the useEffect dependency.
  const [fetchKey, setFetchKey] = useState(0)

  const cancelledRef = useRef(false)

  const refetch = useCallback(() => {
    setFetchKey((k) => k + 1)
  }, [])

  useEffect(() => {
    if (briefId === null) {
      setEntriesByInsight(new Map())
      setLoading(false)
      setError(false)
      return
    }

    let cancelled = false
    cancelledRef.current = false

    setLoading(true)
    setError(false)

    withAuthRetry(() => designAgentApi.briefPrototypeMap(briefId))
      .then((data) => {
        if (cancelled) return
        const map = new Map<number, BriefPrototypeMapEntry>()
        for (const entry of data.entries) {
          map.set(entry.insight_index, entry)
        }
        setEntriesByInsight(map)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // Swallow 401 (token refresh is handled inside withAuthRetry; a
        // residual 401 means the session expired — don't surface as an error
        // state, just leave the map empty, matching sibling hook behaviour).
        const status =
          err != null &&
          typeof err === "object" &&
          "status" in err &&
          typeof (err as { status: unknown }).status === "number"
            ? (err as { status: number }).status
            : null
        if (status === 401) {
          setLoading(false)
          return
        }
        setError(true)
        setLoading(false)
      })

    return () => {
      cancelled = true
      cancelledRef.current = true
    }
    // fetchKey is the refetch trigger; briefId re-runs naturally on change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [briefId, fetchKey])

  return { entriesByInsight, loading, error, refetch }
}
