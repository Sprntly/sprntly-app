// Autosave for a shared team document.
//
// This module is the part that can LOSE SOMEONE'S WRITING, so the tests are
// written as "what would have to go wrong for text to disappear" rather than
// as a walk through the happy path. The document is shared and saves are
// debounced, which makes two people typing in one paragraph an ordinary event.
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import {
  createSaveScheduler,
  SaveConflict,
  type SaveState,
  type SavePayload,
} from "../documentSave"

/** A save function whose resolution the test controls, so an "in flight"
 *  request is a state the test can hold open rather than a race to win. */
function deferredSave() {
  const calls: (SavePayload & { base_version: number })[] = []
  let resolveNext: ((v: { version: number }) => void) | null = null
  let rejectNext: ((e: unknown) => void) | null = null
  const save = vi.fn((payload: SavePayload & { base_version: number }) => {
    calls.push(payload)
    return new Promise<{ version: number }>((res, rej) => {
      resolveNext = res
      rejectNext = rej
    })
  })
  return {
    save,
    calls,
    resolve: (version: number) => { resolveNext?.({ version }); resolveNext = null },
    reject: (e: unknown) => { rejectNext?.(e); rejectNext = null },
  }
}

function states() {
  const seen: SaveState[] = []
  return { seen, onState: (s: SaveState) => seen.push(s), last: () => seen[seen.length - 1] }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe("save scheduling", () => {
  it("debounces a burst of keystrokes into one request", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 100 })

    s.schedule({ body_html: "<p>a</p>" })
    s.schedule({ body_html: "<p>ab</p>" })
    s.schedule({ body_html: "<p>abc</p>" })
    expect(d.save).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(100)
    expect(d.save).toHaveBeenCalledTimes(1)
    // The LAST text, not the first — an intermediate save would be immediately
    // stale and would burn a version for nothing.
    expect(d.calls[0].body_html).toBe("<p>abc</p>")
    s.dispose()
  })

  it("sends the version it started from, and adopts the one it is given back", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 7, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>x</p>" })
    await vi.advanceTimersByTimeAsync(10)
    expect(d.calls[0].base_version).toBe(7)

    d.resolve(8)
    await vi.advanceTimersByTimeAsync(0)

    s.schedule({ body_html: "<p>y</p>" })
    await vi.advanceTimersByTimeAsync(10)
    // Writing against 7 again would conflict with our own previous save.
    expect(d.calls[1].base_version).toBe(8)
    s.dispose()
  })

  it("does not overlap two writes", async () => {
    // A slow save followed by a fast keystroke: if both were in flight, the
    // loser could land last and the user's newest text would be overwritten by
    // their older text.
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>first</p>" })
    await vi.advanceTimersByTimeAsync(10)
    expect(d.save).toHaveBeenCalledTimes(1)

    s.schedule({ body_html: "<p>second</p>" })
    await vi.advanceTimersByTimeAsync(50)
    // Still one: the first has not resolved.
    expect(d.save).toHaveBeenCalledTimes(1)

    d.resolve(2)
    await vi.advanceTimersByTimeAsync(0)
    // Now the queued text goes, without waiting for another keystroke.
    expect(d.save).toHaveBeenCalledTimes(2)
    expect(d.calls[1].body_html).toBe("<p>second</p>")
    s.dispose()
  })

  it("keeps text typed DURING a request instead of marking it saved", async () => {
    // The subtle one. If pending were cleared after the await, text typed while
    // the request was in flight would be folded into the response's "saved"
    // and never sent — silently lost the moment the user stopped typing.
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>sent</p>" })
    await vi.advanceTimersByTimeAsync(10)
    s.schedule({ body_html: "<p>typed during</p>" })

    d.resolve(2)
    await vi.advanceTimersByTimeAsync(0)

    expect(d.calls[1].body_html).toBe("<p>typed during</p>")
    s.dispose()
  })

  it("merges a title change and a body change into one write", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })
    s.schedule({ title: "T" })
    s.schedule({ body_html: "<p>b</p>" })
    await vi.advanceTimersByTimeAsync(10)
    expect(d.calls[0]).toMatchObject({ title: "T", body_html: "<p>b</p>" })
    s.dispose()
  })

  it("flush sends immediately without waiting out the debounce", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 5000 })
    s.schedule({ body_html: "<p>x</p>" })
    void s.flush()
    await vi.advanceTimersByTimeAsync(0)
    expect(d.save).toHaveBeenCalledTimes(1)
    s.dispose()
  })

  it("flush with nothing pending sends nothing", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState })
    await s.flush()
    expect(d.save).not.toHaveBeenCalled()
    s.dispose()
  })
})

describe("conflict", () => {
  it("stops saving and reports the other version", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>mine</p>" })
    await vi.advanceTimersByTimeAsync(10)
    d.reject(new SaveConflict({
      id: 1, title: "T", body_html: "<p>theirs</p>", version: 2, updated_by: "u2",
    }))
    await vi.advanceTimersByTimeAsync(0)

    const last = st.last()
    expect(last.kind).toBe("conflict")
    expect(last.kind === "conflict" && last.theirs?.body_html).toBe("<p>theirs</p>")
  })

  it("REFUSES to keep saving while conflicted", async () => {
    // The whole point of the state. Retrying would overwrite the colleague the
    // server's check just protected — turning one lost paragraph into a fight
    // between two editors.
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>mine</p>" })
    await vi.advanceTimersByTimeAsync(10)
    d.reject(new SaveConflict(null))
    await vi.advanceTimersByTimeAsync(0)
    expect(d.save).toHaveBeenCalledTimes(1)

    s.schedule({ body_html: "<p>still typing</p>" })
    await vi.advanceTimersByTimeAsync(1000)
    await s.flush()
    expect(d.save).toHaveBeenCalledTimes(1) // never again
    s.dispose()
  })

  it("KEEPS the refused text so 'keep mine' has something to keep", async () => {
    // Dropping it here would be data loss stacked on top of a conflict.
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>mine</p>" })
    await vi.advanceTimersByTimeAsync(10)
    d.reject(new SaveConflict(null))
    await vi.advanceTimersByTimeAsync(0)

    expect(s.pendingKeys()).toContain("body_html")
    s.dispose()
  })

  it("resets onto a new base version and saves again", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>mine</p>" })
    await vi.advanceTimersByTimeAsync(10)
    d.reject(new SaveConflict({
      id: 1, title: "T", body_html: "<p>theirs</p>", version: 5, updated_by: null,
    }))
    await vi.advanceTimersByTimeAsync(0)

    s.reset(5)
    s.schedule({ body_html: "<p>mine again</p>" })
    await vi.advanceTimersByTimeAsync(10)

    expect(d.save).toHaveBeenCalledTimes(2)
    expect(d.calls[1].base_version).toBe(5)
    s.dispose()
  })
})

describe("retryable failures", () => {
  it("keeps the text and retries on the next change", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })

    s.schedule({ body_html: "<p>x</p>" })
    await vi.advanceTimersByTimeAsync(10)
    d.reject(new Error("network down"))
    await vi.advanceTimersByTimeAsync(0)

    expect(st.last().kind).toBe("error")
    expect(s.pendingKeys()).toContain("body_html")

    s.schedule({ body_html: "<p>xy</p>" })
    await vi.advanceTimersByTimeAsync(10)
    expect(d.save).toHaveBeenCalledTimes(2)
    s.dispose()
  })

  it("a network failure is NOT reported as a conflict", async () => {
    // Telling someone a colleague overwrote them when the wifi dropped would
    // send them looking for a change nobody made.
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })
    s.schedule({ body_html: "<p>x</p>" })
    await vi.advanceTimersByTimeAsync(10)
    d.reject(new Error("Failed to fetch"))
    await vi.advanceTimersByTimeAsync(0)
    expect(st.last().kind).toBe("error")
    s.dispose()
  })
})

describe("teardown", () => {
  it("dispose stops a pending debounce from firing", async () => {
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 50 })
    s.schedule({ body_html: "<p>x</p>" })
    s.dispose()
    await vi.advanceTimersByTimeAsync(200)
    expect(d.save).not.toHaveBeenCalled()
  })

  it("does not report state after dispose", async () => {
    // A resolved save landing after unmount would setState on a dead component.
    const d = deferredSave()
    const st = states()
    const s = createSaveScheduler({ baseVersion: 1, save: d.save, onState: st.onState, debounceMs: 10 })
    s.schedule({ body_html: "<p>x</p>" })
    await vi.advanceTimersByTimeAsync(10)
    const before = st.seen.length
    s.dispose()
    d.resolve(2)
    await vi.advanceTimersByTimeAsync(0)
    expect(st.seen.length).toBe(before)
  })
})
