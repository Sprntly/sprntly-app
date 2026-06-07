// @vitest-environment jsdom
//
// P6-22 — ShareMenu radio INTERACTION tests. This is the FIRST jsdom /
// @testing-library DOM test in web/. The global vitest config stays
// `environment: "node"` (the 100+ existing node tests are untouched); this file
// opts into jsdom via the per-file pragma on the first line above — smallest
// blast radius. It drives real click interaction on the share-mode radios and
// asserts the optimistic-select / revert behaviour, which the node-env
// SSR-string tests cannot cover.
//
// Matchers: native DOM only (`el.checked`, `el.disabled`, `el.closest`, …) —
// NO @testing-library/jest-dom (intentionally not a dependency; keeps the new
// devDep footprint to jsdom + the three @testing-library/* packages).
//
// Keyboard nav (AC3): jsdom does NOT implement native radio-group ArrowKey
// selection (it is a browser behaviour), so AC3 is verified STRUCTURALLY — the
// three radios are contiguous with the passcode text field lifted OUT of any
// radio <label>. That DOM contiguity is the exact invariant that makes arrow
// traversal work in a real browser (the live-UX defect this ticket fixes).
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Mock the canonical api module the container imports. ShareMenu.tsx imports
// `../../lib/api` (→ app/lib/api); from this test file that resolves to
// `../../../lib/api`. The mocked `share` is a controllable spy so the
// optimistic-before-resolve and revert-on-reject paths are both assertable.
const { shareMock } = vi.hoisted(() => ({ shareMock: vi.fn() }))
vi.mock("../../../lib/api", () => ({ designAgentApi: { share: shareMock } }))

import { ShareMenu, ShareMenuView } from "../ShareMenu"

afterEach(() => {
  // Explicit RTL cleanup — vitest globals are off (the global config is
  // node-env), so @testing-library/react's auto-afterEach is not registered;
  // without this, renders accumulate in the document and queries get ambiguous.
  cleanup()
  vi.clearAllMocks()
})

type ShareResult = {
  prototype_id: number
  share_mode: string
  share_token: string | null
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe("ShareMenuView — radio a11y association + click (AC1/AC2)", () => {
  it("each radio has an explicit id with a matching label[htmlFor] (AC2 — Regression)", () => {
    // Regression: the unfixed implicit-label markup gives the radios NO id, so
    // no `label[for=...]` exists. The fix adds explicit id + htmlFor.
    render(React.createElement(ShareMenuView, { mode: "private", passcode: "" }))
    for (const name of [/private/i, /public/i, /passcode/i]) {
      const radio = screen.getByRole("radio", { name }) as HTMLInputElement
      expect(radio.id).toBeTruthy()
      expect(document.querySelector(`label[for="${radio.id}"]`)).not.toBeNull()
    }
  })

  it("clicking a radio by accessible name fires onSelectMode with that mode (AC1)", async () => {
    const user = userEvent.setup()
    const onSelectMode = vi.fn()
    render(
      React.createElement(ShareMenuView, { mode: "private", passcode: "", onSelectMode }),
    )
    await user.click(screen.getByRole("radio", { name: /passcode/i }))
    expect(onSelectMode).toHaveBeenCalledWith("passcode")
    await user.click(screen.getByRole("radio", { name: /public/i }))
    expect(onSelectMode).toHaveBeenCalledWith("public")
  })
})

describe("ShareMenuView — contiguous radio group, passcode field lifted out (AC3)", () => {
  it("the passcode input is NOT inside any radio label and sits after the three radios (Regression)", () => {
    render(React.createElement(ShareMenuView, { mode: "passcode", passcode: "" }))
    const passcodeInput = screen.getByTestId("passcode-input") as HTMLInputElement
    // Regression: unfixed markup nests the passcode input inside the passcode
    // radio's <label>; the fix lifts it out so closest("label") is null.
    expect(passcodeInput.closest("label")).toBeNull()
    // The three radios are contiguous, in order, with the passcode text field
    // AFTER them — no foreign focusable element interleaved in the group.
    const inputs = Array.from(
      document.querySelectorAll<HTMLInputElement>(".share-menu input"),
    )
    const radios = inputs.filter((el) => el.type === "radio")
    expect(radios.map((r) => r.value)).toEqual(["private", "public", "passcode"])
    expect(inputs.indexOf(passcodeInput)).toBeGreaterThan(inputs.indexOf(radios[2]))
  })
})

describe("ShareMenuView — progressive-disclosure passcode field", () => {
  it("absent unless passcode mode; present + enabled in passcode mode; disabled while busy", () => {
    // not passcode mode → the passcode field is not mounted at all (progressive
    // disclosure). This does not change radio traversal: the field was already
    // lifted OUT of the radio focus order, so mounting/unmounting it leaves the
    // three contiguous radios untouched.
    const r1 = render(
      React.createElement(ShareMenuView, { mode: "public", passcode: "" }),
    )
    expect(screen.queryByTestId("passcode-input")).toBeNull()
    r1.unmount()
    // passcode mode + not busy → present + enabled
    const r2 = render(
      React.createElement(ShareMenuView, { mode: "passcode", passcode: "", busy: false }),
    )
    expect((screen.getByTestId("passcode-input") as HTMLInputElement).disabled).toBe(false)
    r2.unmount()
    // passcode mode + busy → present but disabled (the `busy` term keeps the
    // field gated through the optimistic window).
    render(
      React.createElement(ShareMenuView, { mode: "passcode", passcode: "", busy: true }),
    )
    expect((screen.getByTestId("passcode-input") as HTMLInputElement).disabled).toBe(true)
  })
})

describe("ShareMenu (container) — optimistic mode select (AC4)", () => {
  it("reflects the selected mode before api.share resolves (optimistic)", async () => {
    const user = userEvent.setup()
    const d = deferred<ShareResult>()
    shareMock.mockReturnValue(d.promise)
    render(React.createElement(ShareMenu, { prototypeId: 7, initialMode: "private" }))
    const publicRadio = screen.getByRole("radio", { name: /public/i }) as HTMLInputElement
    await user.click(publicRadio)
    // Optimistic: checked BEFORE the deferred share resolves.
    expect(publicRadio.checked).toBe(true)
    expect(shareMock).toHaveBeenCalledWith(7, { mode: "public" })
    // Resolve → stays public (server-confirmed).
    d.resolve({ prototype_id: 7, share_mode: "public", share_token: "tok-1" })
    await waitFor(() => expect(publicRadio.checked).toBe(true))
  })

  it("reverts to the prior mode when api.share rejects (AC4 — Regression)", async () => {
    const user = userEvent.setup()
    const d = deferred<ShareResult>()
    shareMock.mockReturnValue(d.promise)
    render(React.createElement(ShareMenu, { prototypeId: 7, initialMode: "private" }))
    const publicRadio = screen.getByRole("radio", { name: /public/i }) as HTMLInputElement
    const privateRadio = screen.getByRole("radio", { name: /private/i }) as HTMLInputElement
    await user.click(publicRadio)
    expect(publicRadio.checked).toBe(true) // optimistic flip
    d.reject(new Error("network boom"))
    // After the rejection settles, the mode reverts to the prior value.
    await waitFor(() => expect(privateRadio.checked).toBe(true))
    expect(publicRadio.checked).toBe(false)
    expect(screen.getByTestId("share-menu-error").textContent).toContain("network boom")
  })
})
