// Part C — closes the reload gap: a page reload mid-generation kills
// runGenerateFlow's own in-memory .then() chain, orphaning a `pending`
// sessionStorage entry forever (the shell replay only ever reads
// pendingCompleted(), which excludes `pending`). resumePendingNotifications
// resumes each still-pending id by reusing runDesignAgentGeneration's
// existing "poll a prototype whose generation was already kicked off
// elsewhere" behaviour (dependency-injected here so tests substitute a fake).
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  resumePendingNotifications,
  runGenerateFlow,
} from "../DesignAgentDrawer"
import {
  __resetPageLoadGuards,
  markPending,
  pendingCompleted,
  pendingPendingIds,
} from "../notificationStore"

function makeSessionStorage(): Storage {
  let store: Record<string, string> = {}
  return {
    get length(): number {
      return Object.keys(store).length
    },
    getItem: (k: string): string | null => (k in store ? store[k] : null),
    setItem: (k: string, v: string): void => {
      store[k] = String(v)
    },
    removeItem: (k: string): void => {
      delete store[k]
    },
    clear: (): void => {
      store = {}
    },
    key: (i: number): string | null => Object.keys(store)[i] ?? null,
  }
}

const testGlobal = globalThis as unknown as {
  window?: { sessionStorage: Storage }
}
function installStorage() {
  testGlobal.window = { sessionStorage: makeSessionStorage() }
}
function removeWindow() {
  testGlobal.window = undefined
}

afterEach(() => {
  removeWindow()
  __resetPageLoadGuards()
})

describe("resumePendingNotifications (Part C)", () => {
  it("test_resume_pending_notifications_resolves_ready_entry — a pending entry resolves to completed with the polled prd_id, and toasts (AC11)", async () => {
    installStorage()
    markPending(555)
    const poll = vi.fn().mockResolvedValue({
      ok: true,
      prototype: { id: 555, status: "ready", bundle_url: null, error: null, prd_id: 5 },
    })
    const showToast = vi.fn()

    await resumePendingNotifications(showToast, poll)

    expect(poll).toHaveBeenCalledWith({ prototypeId: 555 })
    expect(pendingCompleted()).toEqual([
      { prototypeId: 555, sub: "Your prototype finished generating.", prdId: 5 },
    ])
    expect(showToast).toHaveBeenCalledWith(
      "Prototype ready",
      "Your prototype finished generating.",
      "Open",
      expect.objectContaining({ onAction: expect.any(Function) }),
    )
  })

  it("test_reload_orphaned_pending_entry_gets_resolved — a bare pending entry (simulated reload, no live JS chain) gets resolved (regression, AC11)", async () => {
    installStorage()
    // Simulate a reload mid-generation: a pending entry with no completing
    // in-memory chain (unlike a real run, nothing else will ever flip this).
    markPending(777, 9)
    const poll = vi.fn().mockResolvedValue({
      ok: true,
      prototype: { id: 777, status: "ready", bundle_url: null, error: null, prd_id: 9 },
    })
    const showToast = vi.fn()

    // Fails on unfixed code: today nothing ever polls a bare pending entry —
    // pendingCompleted() would stay permanently empty and showToast would
    // never fire for this id.
    await resumePendingNotifications(showToast, poll)

    expect(pendingCompleted().some((e) => e.prototypeId === 777)).toBe(true)
    expect(showToast).toHaveBeenCalled()
  })

  it("test_resume_pending_notifications_drops_failed_entry — a failed/timed-out poll acknowledges (drops) the entry, no toast (AC12)", async () => {
    installStorage()
    markPending(556)
    const poll = vi.fn().mockResolvedValue({ ok: false, message: "timed out" })
    const showToast = vi.fn()

    await resumePendingNotifications(showToast, poll)

    expect(pendingCompleted()).toEqual([])
    expect(showToast).not.toHaveBeenCalled()
  })

  it("test_resume_pending_notifications_retries_a_timed_out_entry — AC5: a timedOut poll result does NOT acknowledge/drop the entry, and does NOT toast", async () => {
    installStorage()
    markPending(559)
    const poll = vi.fn().mockResolvedValue({
      ok: false,
      timedOut: true,
      message: "Generation timed out (6 minutes)",
    })
    const showToast = vi.fn()

    // Fails on unfixed code: today ANY !result.ok calls acknowledge(id),
    // dropping a still-running entry forever.
    await resumePendingNotifications(showToast, poll)

    expect(showToast).not.toHaveBeenCalled()
    expect(pendingPendingIds()).toContain(559)
  })

  it("test_resume_pending_notifications_timed_out_leaves_pending_pending_ids — AC5 edge: explicit pendingPendingIds() check", async () => {
    installStorage()
    markPending(560)
    const poll = vi.fn().mockResolvedValue({
      ok: false,
      timedOut: true,
      message: "Generation timed out (6 minutes)",
    })

    await resumePendingNotifications(vi.fn(), poll)

    expect(pendingPendingIds()).toEqual([560])
  })

  it("test_resume_pending_notifications_drops_genuine_failure_unchanged — AC6: a genuine (timedOut absent) failure still acknowledges", async () => {
    installStorage()
    markPending(561)
    const poll = vi.fn().mockResolvedValue({ ok: false, message: "boom" })

    await resumePendingNotifications(vi.fn(), poll)

    expect(pendingPendingIds()).not.toContain(561)
  })

  it("test_resume_pending_notifications_dedupes_within_page_load — a second call in the same page-load is a no-op for already-resolving/resolved ids (AC13)", async () => {
    installStorage()
    markPending(558)
    const poll = vi.fn().mockResolvedValue({
      ok: true,
      prototype: { id: 558, status: "ready", bundle_url: null, error: null, prd_id: null },
    })
    const showToast = vi.fn()

    await resumePendingNotifications(showToast, poll)
    expect(poll).toHaveBeenCalledTimes(1)

    // A second call within the SAME simulated page-load (no
    // __resetPageLoadGuards): the entry is now completed (excluded from
    // pendingPendingIds), so poll is still not called again.
    await resumePendingNotifications(showToast, poll)
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it("test_resume_pending_notifications_noop_when_nothing_pending — an empty pending list never calls poll", async () => {
    installStorage()
    const poll = vi.fn()
    await resumePendingNotifications(vi.fn(), poll)
    expect(poll).not.toHaveBeenCalled()
  })

  it("does not touch a completed entry seeded via the normal runGenerateFlow path (sanity — the two flows compose)", async () => {
    installStorage()
    const genResult = Promise.resolve({ ok: true as const, prototype: {} as never })
    await runGenerateFlow({
      params: {
        prd_id: 61,
        target_platform: "desktop" as const,
        instructions: "",
        figma_file_key: null,
      },
      generate: vi.fn().mockResolvedValue({ prototype_id: 900, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast: vi.fn(),
      setSubmitting: vi.fn(),
      notifyOnReady: false,
    })
    await genResult
    await Promise.resolve()

    const poll = vi.fn()
    await resumePendingNotifications(vi.fn(), poll)
    // 900 is already completed (not pending) — resumePendingNotifications
    // must not re-poll it.
    expect(poll).not.toHaveBeenCalled()
  })
})
