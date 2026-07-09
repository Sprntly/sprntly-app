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

// Mock useCompany so the container's context-sourced company DISPLAY name is
// controllable per test (the real hook needs a WorkspaceProvider chain). Tests
// that pass an explicit companyDisplaySlug prop are unaffected (the prop wins).
const { companyMock } = vi.hoisted(() => ({ companyMock: { value: "Lab X" } }))
vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({
    activeCompany: "asurion",
    setActiveCompany: () => {},
    activeCompanyDisplayName: companyMock.value,
  }),
}))

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

function stubClipboard() {
  const writeText = vi.fn(async (_: string) => {})
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  return writeText
}

describe("ShareMenuView — radio a11y association + click (AC1/AC2)", () => {
  it("each radio has an explicit id with a matching label[htmlFor] (AC2 — Regression)", () => {
    // Regression: the unfixed implicit-label markup gives the radios NO id, so
    // no `label[for=...]` exists. The fix adds explicit id + htmlFor.
    render(React.createElement(ShareMenuView, { mode: "private", passcode: "" }))
    for (const name of [/private/i, /public/i]) {
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
    // Starting mode is "private" (its radio is checked), so clicking the unchecked
    // public radio fires the onChange → onSelectMode("public"). Clicking an
    // already-checked radio is a jsdom no-op, so private (the active mode) is not
    // re-asserted here.
    await user.click(screen.getByRole("radio", { name: /public/i }))
    expect(onSelectMode).toHaveBeenCalledWith("public")
  })
})

describe("ShareMenuView — contiguous radio group (AC3)", () => {
  it("the two radios are contiguous and in order, with no passcode field interleaved", () => {
    render(React.createElement(ShareMenuView, { mode: "public", passcode: "" }))
    // Passcode mode is not surfaced → no passcode input is ever mounted.
    expect(screen.queryByTestId("passcode-input")).toBeNull()
    // The two radios are contiguous, in order — no foreign focusable element
    // interleaved in the group.
    const inputs = Array.from(
      document.querySelectorAll<HTMLInputElement>(".share-menu input"),
    )
    const radios = inputs.filter((el) => el.type === "radio")
    expect(radios.map((r) => r.value)).toEqual(["private", "public"])
  })
})

describe("ShareMenuView — passcode field is never surfaced", () => {
  it("no passcode input mounts in any mode (passcode mode is hidden from the UI)", () => {
    const r1 = render(
      React.createElement(ShareMenuView, { mode: "public", passcode: "" }),
    )
    expect(screen.queryByTestId("passcode-input")).toBeNull()
    r1.unmount()
    // Even when the (hidden) passcode value is the active mode, no input renders.
    render(
      React.createElement(ShareMenuView, { mode: "passcode", passcode: "" }),
    )
    expect(screen.queryByTestId("passcode-input")).toBeNull()
  })
})

describe("ShareMenu — private internal link", () => {
  it("renders the internal link and member caption in private mode", () => {
    render(
      React.createElement(ShareMenu, {
        prototypeId: 785,
        prdId: 785,
        initialMode: "private",
      }),
    )
    const link = screen.getByTestId("share-link")
    expect(link.textContent).toContain(`${window.location.origin}/prototype?prd=785`)
    expect(link.textContent).toContain(
      "Only signed-in workspace members can open this link.",
    )
    expect(shareMock).not.toHaveBeenCalled()
  })

  it("copies exactly the private internal link", async () => {
    const user = userEvent.setup()
    const writeText = stubClipboard()
    render(
      React.createElement(ShareMenu, {
        prototypeId: 785,
        prdId: 785,
        initialMode: "private",
      }),
    )
    await user.click(screen.getByTestId("copy-link-btn"))
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/prototype?prd=785`)
  })

  it("renders no link when private mode has no prototype query id", () => {
    render(React.createElement(ShareMenu, { prototypeId: 785, initialMode: "private" }))
    expect(screen.queryByTestId("share-link")).toBeNull()
    expect(document.body.textContent).not.toContain("prototype?prd=undefined")
  })

  it("builds the public token link with both cosmetic segments when a prototype query id is available", () => {
    render(
      React.createElement(ShareMenu, {
        prototypeId: 785,
        prdId: 785,
        initialMode: "public",
        initialToken: "tok-public",
        companyDisplaySlug: "sprntly",
        prdTitle: "Onboarding Revamp",
      }),
    )
    const link = screen.getByTestId("share-link")
    expect(link.textContent).toContain(
      `${window.location.origin}/p/sprntly/onboarding-revamp/tok-public`,
    )
    expect(link.textContent).not.toContain("/prototype?prd=")
  })

  it("builds the passcode token link with both cosmetic segments when a prototype query id is available", () => {
    render(
      React.createElement(ShareMenu, {
        prototypeId: 785,
        prdId: 785,
        initialMode: "passcode",
        initialToken: "tok-pass",
        companyDisplaySlug: "sprntly",
        prdTitle: "Onboarding Revamp",
      }),
    )
    const link = screen.getByTestId("share-link")
    expect(link.textContent).toContain(
      `${window.location.origin}/p/sprntly/onboarding-revamp/tok-pass`,
    )
    expect(link.textContent).not.toContain("/prototype?prd=")
  })
})

// ─── SHARE-URL cosmetic segments: display-derived, not the raw opaque slug ────
// The container computes both cosmetic URL segments from human-readable data —
// the company DISPLAY name (via useCompany, mocked here) and the PRD title (via
// the prdTitle prop) — never the opaque companies.slug. These assert the shape
// of the live rendered/copied link.
describe("ShareMenu (container) — display-derived cosmetic slugs (AC13/AC14)", () => {
  it("renders a link built from the slugified display name + PRD title, not a raw opaque slug", () => {
    companyMock.value = "Lab X"
    render(
      React.createElement(ShareMenu, {
        prototypeId: 785,
        prdId: 785,
        initialMode: "public",
        initialToken: "tok-public",
        // No companyDisplaySlug prop → self-sources the display name from context.
        prdTitle: "Customer Onboarding Revamp",
      }),
    )
    const link = screen.getByTestId("share-link")
    expect(link.textContent).toContain(
      `${window.location.origin}/p/lab-x/customer-onboarding-revamp/tok-public`,
    )
    // ...and never the opaque, name-independent companies.slug shape (c + 11 chars).
    expect(link.textContent).not.toMatch(/\/p\/c[a-z0-9]{11}\//)
  })

  it("falls back to the 'prototype' feature segment when prdTitle is missing, without throwing (AC14)", () => {
    companyMock.value = "Lab X"
    render(
      React.createElement(ShareMenu, {
        prototypeId: 785,
        prdId: 785,
        initialMode: "public",
        initialToken: "tok-public",
        // prdTitle omitted.
      }),
    )
    const link = screen.getByTestId("share-link")
    // Still exactly 3 path segments + token, using the feature fallback.
    expect(link.textContent).toContain(
      `${window.location.origin}/p/lab-x/prototype/tok-public`,
    )
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
