// @vitest-environment jsdom
//
// The shared iterate runner polls the prototype row to completion. A bearer
// token can expire mid-poll, returning a transient 401 — previously the outer
// catch treated that as terminal and aborted the run even though the background
// iterate finished, so the canvas never advanced ("iteration isn't working").
//
// These tests drive the hook through a real poll cycle with fake timers and
// assert that a single transient 401 on an in-loop fetch is retried (via the
// shared auth-retry primitive) rather than aborting the run. The primitive
// itself is real here (not mocked) so the end-to-end resilience is exercised.
import * as React from "react"
import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useIterateRun } from "../useIterateRun"
import {
  ApiError,
  designAgentApi,
  setAccessTokenProvider,
  type IterateResponse,
  type PrototypeRecord,
} from "../../../lib/api"

// Sprntly components carry no `import React`; expose it globally (repo convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const PROTOTYPE_ID = 7

function proto(
  status: PrototypeRecord["status"],
  pendingQuestion: PrototypeRecord["pending_question"] = null,
): PrototypeRecord {
  return {
    id: PROTOTYPE_ID,
    status,
    bundle_url: status === "ready" ? "https://bundle.test/v2" : null,
    error: null,
    pending_question: pendingQuestion,
  }
}

function makeApi(get: ReturnType<typeof vi.fn>) {
  const iterate = vi.fn<
    (
      id: number,
      body: { prompt: string; applied_comment_id?: number | null; mode?: "plan" | "execute" },
    ) => Promise<IterateResponse>
  >().mockResolvedValue({
    prototype_id: PROTOTYPE_ID,
    status: "generating",
    queue_position: 0,
  })
  return { iterate, get } as unknown as Pick<
    typeof designAgentApi,
    "iterate" | "get"
  >
}

beforeEach(() => {
  vi.useFakeTimers()
  // No token provider needed; withAuthRetry's re-acquire is a no-op when unset.
  setAccessTokenProvider(() => Promise.resolve(null))
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe("useIterateRun — transient-401 resilience", () => {
  it("test_iterate_poll_survives_transient_401: a 401 mid-poll retries and the run reaches completion instead of aborting", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      // 1) initial poll: still generating → enter the poll loop
      .mockResolvedValueOnce(proto("generating"))
      // 2) in-loop poll: transient 401 (token refresh race)
      .mockRejectedValueOnce(new ApiError(401, { detail: "token expired" }))
      // 3) retry after re-acquire: the iterate has landed → ready
      .mockResolvedValueOnce(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("make the hero blue")
      await vi.runAllTimersAsync()
      await run
    })

    // The run did NOT abort: it completed and handed the ready row to the canvas.
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].status).toBe("ready")
    expect(result.current.error).toBeNull()
    expect(result.current.running).toBe(false)
    // The 401 was retried: initial + failed-attempt + retry = 3 gets.
    expect(get).toHaveBeenCalledTimes(3)
  })

  it("completes a clean run with no 401 (baseline)", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      .mockResolvedValueOnce(proto("ready"))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("tweak the spacing")
      await vi.runAllTimersAsync()
      await run
    })

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].status).toBe("ready")
    expect(result.current.error).toBeNull()
  })

  it("a persistent 401 mid-poll surfaces an error (gives up after the retry budget)", async () => {
    const get = vi
      .fn<(id: number) => Promise<PrototypeRecord>>()
      .mockResolvedValueOnce(proto("generating"))
      // both the in-loop attempt and its single retry 401
      .mockRejectedValue(new ApiError(401, { detail: "really expired" }))

    const onComplete = vi.fn()
    const api = makeApi(get)

    const { result } = renderHook(() =>
      useIterateRun({ prototypeId: PROTOTYPE_ID, onComplete, api }),
    )

    await act(async () => {
      const run = result.current.runIterate("change the copy")
      await vi.runAllTimersAsync()
      await run
    })

    // A genuine persistent auth failure still surfaces (not swallowed).
    expect(onComplete).not.toHaveBeenCalled()
    expect(result.current.error).not.toBeNull()
    expect(result.current.running).toBe(false)
  })
})
