"use client"

/**
 * The shared next-prompt suggestion host service — the state + retire + fetch-
 * after-settle orchestration behind the presentational `NextPromptSuggestions`
 * strip. Lifted out of `ChatScreen` so every surface owns suggestions the same
 * way instead of the host being the only place that can.
 *
 * State is host-agnostic (a per-KEY map of suggestion strings — main keys by
 * tab id, a project surface keys by its thread/conversation id). The only
 * surface-specific piece is the FETCH, injected via `adapter.fetchSuggestions`
 * (main → `chatSuggestionsApi.next`; a project surface supplies its own thread-
 * scoped fetch).
 *
 * The design is negative on purpose: suggestions are fetched AFTER an answer
 * settles, never awaited by the turn, and every fault (slow / failed / never-
 * returned / synchronous throw) degrades to the ordinary empty state — the
 * absence of chips is a normal, invisible state, so nothing here is ever worth
 * failing a turn over.
 */
import { useCallback, useState } from "react"

export interface NextPromptsAdapter {
  /** Fetch the next-prompt suggestions for a settled conversation. Main passes
   *  `chatSuggestionsApi.next(conversationId, { prdId }).then(r => r.suggestions)`;
   *  a project surface supplies its own thread-scoped fetch. Any rejection is
   *  swallowed by `onSettled` — an empty list and a failure mean the same thing
   *  to the reader. */
  fetchSuggestions: (
    conversationId: number,
    opts: { prdId: number | null },
  ) => Promise<string[]>
}

/** Options for a single fetch-after-settle. `ready` gates the fetch on the
 *  turn's assistant row having landed (the backend reads the thread from the
 *  database — firing early asks "what next?" about a conversation missing the
 *  very exchange it should continue). `shouldApply` is the host's late-arrival
 *  guard, re-checked at apply time (still mounted, tab still open, no newer send
 *  in flight) — if it returns false the fetched chips belong to a superseded
 *  turn and are dropped. */
export interface NextPromptsSettleOptions {
  prdId: number | null
  ready?: Promise<unknown>
  shouldApply?: () => boolean
}

export function useNextPrompts(adapter: NextPromptsAdapter) {
  const [byKey, setByKey] = useState<Record<string, string[]>>({})

  /** Retire the given key's suggestions — synchronous and unconditional; the
   *  instant a key sends again the previous turn's chips are stale. No-op (same
   *  state reference) when the key already has none. */
  const retire = useCallback((key: string) => {
    setByKey((prev) => (prev[key]?.length ? { ...prev, [key]: [] } : prev))
  }, [])

  /** The render input for a key — the exact `(key && byKey[key]) || []`
   *  the host used inline, so a missing/empty key renders nothing. */
  const suggestionsFor = useCallback(
    (key: string | null | undefined): string[] => (key ? byKey[key] ?? [] : []),
    [byKey],
  )

  /** Fetch-after-settle for one turn. Waits on `ready`, fetches via the
   *  adapter, and publishes a NON-EMPTY result under `key` only if the host's
   *  `shouldApply` guard still holds. Every fault degrades to the empty
   *  state. */
  const onSettled = useCallback(
    (key: string, conversationId: number, opts: NextPromptsSettleOptions) => {
      try {
        void Promise.resolve(opts.ready)
          .then(() => adapter.fetchSuggestions(conversationId, { prdId: opts.prdId }))
          .then((suggestions) => {
            if (!suggestions?.length) return
            if (opts.shouldApply && !opts.shouldApply()) return
            setByKey((prev) => ({ ...prev, [key]: suggestions }))
          })
          .catch(() => {
            /* silence is the designed fallback */
          })
      } catch {
        /* same fallback, for a synchronous throw */
      }
    },
    [adapter],
  )

  return { byKey, suggestionsFor, retire, onSettled }
}
