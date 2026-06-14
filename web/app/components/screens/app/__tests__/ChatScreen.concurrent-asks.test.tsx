// Concurrent-asks tests for ChatScreen.
//
// ChatScreen lets multiple parallel chat TABS each have their OWN ask in flight
// at the same time: sending in tab A must not block tab B, their `askApi.ask`
// calls overlap, and each reply routes to its own tab. The per-tab in-flight
// ("asking") guard + busy tracking + result routing live in
// `app/lib/chatAskState.ts` (`runTabAsk`, `isComposerBusy`, the immutable Set
// helpers) — the EXACT module ChatScreen wires into `submitAsk`. These tests
// drive that real logic against a mocked, delay-controllable `askApi.ask`,
// modelling tab state the way ChatScreen does:
//   - `asking`  = the askingTabsRef.current Set (a tab can't double-send).
//   - `busyTabs` = the per-tab busy Set (the composer reads it for the ACTIVE
//     tab via isComposerBusy).
//   - per-tab threads = where replies are routed (onResult writes to the
//     captured targetTabId, never a global slot).
// The assertions therefore hold against the shipped concurrency logic, not a
// re-implementation.
//
// Assertions (from the task):
//   1. Two tabs have concurrent in-flight asks: start A (pending), then B without
//      awaiting A -> askApi.ask called twice (both in flight); B not blocked by A.
//   2. Per-tab double-send guard: a 2nd send in A while A is pending is blocked
//      (askApi not called a 3rd time for A).
//   3. Replies route correctly: A resolves -> lands in A's thread; B -> B's.
//   4. Active-tab busy: composer-busy is true for a tab with an in-flight ask,
//      false for an idle tab; switching active tab flips it.
//   5. Cleanup: after a tab's ask resolves (or errors) it's removed from the
//      asking/busy sets so it can send again.
import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  runTabAsk,
  isComposerBusy,
  isTabAsking,
  addToSet,
  removeFromSet,
} from "../../../../lib/chatAskState"

// ── A controllable askApi.ask mock ─────────────────────────────────────────
// Each call returns a pending promise plus a resolve/reject handle, so a test can
// assert two asks are simultaneously in flight before settling either.
type Deferred = {
  promise: Promise<string>
  resolve: (v: string) => void
  reject: (e: unknown) => void
}
function deferred(): Deferred {
  let resolve!: (v: string) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<string>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

// ── In-memory model of ChatScreen's per-tab state ──────────────────────────
function makeHarness() {
  // askingTabsRef.current — the authoritative double-send guard Set.
  const asking = new Set<string>()
  // busyTabs — held in React state; we mirror the immutable-Set update pattern.
  let busyTabs: ReadonlySet<string> = new Set<string>()
  const setBusy = (updater: (prev: ReadonlySet<string>) => ReadonlySet<string>) => {
    busyTabs = updater(busyTabs)
  }
  // Per-tab threads — replies/errors land here, keyed by the captured targetTabId.
  const threads = new Map<string, Array<{ reply?: string; error?: string }>>()
  const openTab = (id: string) => threads.set(id, [])

  // One controllable ask per call; `ask` is the mocked askApi.ask.
  const pending: Deferred[] = []
  const ask = vi.fn(() => {
    const d = deferred()
    pending.push(d)
    return d.promise
  })

  // Mirror ChatScreen.submitAsk's call into runTabAsk for a given tab.
  const submit = (tabId: string) =>
    runTabAsk<string>({
      targetTabId: tabId,
      asking,
      setBusy,
      ask,
      onResult: (id, res) => {
        const t = threads.get(id)
        if (t) t.push({ reply: res })
      },
      onError: (id, e) => {
        const t = threads.get(id)
        if (t) t.push({ error: e instanceof Error ? e.message : String(e) })
      },
    })

  return {
    asking, ask, threads, openTab, submit, pending,
    getBusyTabs: () => busyTabs,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("ChatScreen concurrent asks (runTabAsk + per-tab state)", () => {
  // Assertion 1: two tabs can have concurrent in-flight asks; B is not blocked by A.
  it("lets tab A and tab B have concurrent in-flight asks", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    // Start A's ask, do NOT await it (its askApi.ask stays pending).
    const aStarted = h.submit("tab-A")
    // Start B's ask WITHOUT awaiting A.
    const bStarted = h.submit("tab-B")

    // Both asks are simultaneously in flight.
    expect(h.ask).toHaveBeenCalledTimes(2)
    expect(isTabAsking(h.asking, "tab-A")).toBe(true)
    expect(isTabAsking(h.asking, "tab-B")).toBe(true)
    // Both runTabAsk calls reported "started" (true), i.e. B was not blocked by A.
    expect(await Promise.all([
      // Resolve both so the awaited started-flags settle.
      (async () => { h.pending[0].resolve("reply A"); return aStarted })(),
      (async () => { h.pending[1].resolve("reply B"); return bStarted })(),
    ])).toEqual([true, true])
  })

  // Assertion 2: per-tab double-send guard — a 2nd send in A while A is pending is blocked.
  it("blocks a second send in the same tab while its ask is in flight", async () => {
    const h = makeHarness()
    h.openTab("tab-A")

    const first = h.submit("tab-A")        // A's ask now pending
    expect(h.ask).toHaveBeenCalledTimes(1)

    // Second send in A before the first settles -> guarded, runs nothing.
    const second = await h.submit("tab-A")
    expect(second).toBe(false)
    expect(h.ask).toHaveBeenCalledTimes(1) // NOT called a 2nd/3rd time for A

    // A different tab is unaffected.
    h.openTab("tab-B")
    h.submit("tab-B")
    expect(h.ask).toHaveBeenCalledTimes(2)

    h.pending[0].resolve("A")
    h.pending[1].resolve("B")
    await first
  })

  // Assertion 3: replies route to the correct tab (no cross-routing).
  it("routes each reply to its own tab when both resolve", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    const a = h.submit("tab-A")
    const b = h.submit("tab-B")

    // Resolve in REVERSE order to prove routing is by captured targetTabId, not order.
    h.pending[1].resolve("reply for B")
    h.pending[0].resolve("reply for A")
    await Promise.all([a, b])

    expect(h.threads.get("tab-A")).toEqual([{ reply: "reply for A" }])
    expect(h.threads.get("tab-B")).toEqual([{ reply: "reply for B" }])
    // A's reply never leaked into B's thread.
    expect(h.threads.get("tab-B")!.some((t) => t.reply === "reply for A")).toBe(false)
  })

  // Assertion 4: composer-busy is derived from the ACTIVE tab; switching flips it.
  it("derives composer busy from the active tab only", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    // A is asking; B is idle.
    const a = h.submit("tab-A")
    const busyTabs = h.getBusyTabs()

    // Active tab = A -> composer busy. Active tab = B -> composer NOT busy.
    expect(isComposerBusy(busyTabs, "tab-A")).toBe(true)
    expect(isComposerBusy(busyTabs, "tab-B")).toBe(false)
    // Switching the active tab to the idle one flips the composer to enabled.
    expect(isComposerBusy(busyTabs, "tab-B")).toBe(false)
    // No active tab at all -> not busy.
    expect(isComposerBusy(busyTabs, null)).toBe(false)

    h.pending[0].resolve("done")
    await a
    // After A resolves, neither tab is busy.
    expect(isComposerBusy(h.getBusyTabs(), "tab-A")).toBe(false)
  })

  // Assertion 5: after resolve OR error, the tab is removed from asking/busy sets
  // so it can send again.
  it("clears asking/busy after resolve so the tab can send again", async () => {
    const h = makeHarness()
    h.openTab("tab-A")

    const a = h.submit("tab-A")
    expect(isTabAsking(h.asking, "tab-A")).toBe(true)
    expect(isComposerBusy(h.getBusyTabs(), "tab-A")).toBe(true)

    h.pending[0].resolve("first reply")
    await a

    // Cleared on resolve.
    expect(isTabAsking(h.asking, "tab-A")).toBe(false)
    expect(isComposerBusy(h.getBusyTabs(), "tab-A")).toBe(false)

    // It can now send again (a fresh ask is started).
    const again = h.submit("tab-A")
    expect(h.ask).toHaveBeenCalledTimes(2)
    h.pending[1].resolve("second reply")
    await again
    expect(h.threads.get("tab-A")).toEqual([
      { reply: "first reply" }, { reply: "second reply" },
    ])
  })

  it("clears asking/busy after an error too, and routes the error to its tab", async () => {
    const h = makeHarness()
    h.openTab("tab-A")
    h.openTab("tab-B")

    const a = h.submit("tab-A")
    const b = h.submit("tab-B")

    // A errors, B resolves — concurrently in flight.
    h.pending[0].reject(new Error("boom A"))
    h.pending[1].resolve("ok B")
    await Promise.all([a, b])

    // Error routed to A's thread only; B got its reply.
    expect(h.threads.get("tab-A")).toEqual([{ error: "boom A" }])
    expect(h.threads.get("tab-B")).toEqual([{ reply: "ok B" }])
    // Both cleared from asking/busy after settling.
    expect(isTabAsking(h.asking, "tab-A")).toBe(false)
    expect(isTabAsking(h.asking, "tab-B")).toBe(false)
    expect(h.getBusyTabs().size).toBe(0)

    // A can send again after the error (a fresh ask starts; resolve it so no
    // promise dangles).
    const retry = h.submit("tab-A")
    expect(h.ask).toHaveBeenCalledTimes(3)
    h.pending[2].resolve("retry ok")
    await expect(retry).resolves.toBe(true)
  })

  // The cleanup must not throw if the tab was closed mid-flight (Set delete on a
  // missing key is a no-op; removeFromSet bails when the key is absent).
  it("does not throw on cleanup when the tab was closed mid-flight", async () => {
    const h = makeHarness()
    h.openTab("tab-A")

    const a = h.submit("tab-A")
    // Simulate the tab being closed: drop its thread + pre-clear its sets the way
    // unrelated state churn might. runTabAsk's finally must tolerate this.
    h.threads.delete("tab-A")
    h.asking.delete("tab-A")

    h.pending[0].resolve("late reply")
    await expect(a).resolves.toBe(true) // no throw
    expect(isTabAsking(h.asking, "tab-A")).toBe(false)
  })
})

// Direct unit coverage of the immutable Set helpers the component uses for
// setBusyTabs updates (add returns a new set; remove bails when absent).
describe("chatAskState Set helpers", () => {
  it("addToSet returns a new Set containing the value", () => {
    const a = new Set(["x"])
    const b = addToSet(a, "y")
    expect(b).not.toBe(a)
    expect([...b].sort()).toEqual(["x", "y"])
    expect([...a]).toEqual(["x"]) // original untouched
  })
  it("removeFromSet returns the SAME ref when the value is absent (React bail-out)", () => {
    const a = new Set(["x"])
    expect(removeFromSet(a, "missing")).toBe(a)
  })
  it("removeFromSet returns a new Set without the value when present", () => {
    const a = new Set(["x", "y"])
    const b = removeFromSet(a, "x")
    expect(b).not.toBe(a)
    expect([...b]).toEqual(["y"])
  })
})
