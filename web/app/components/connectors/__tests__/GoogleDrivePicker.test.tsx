// @vitest-environment jsdom
//
// Same node-env SSR pattern as the other connector component tests: render the
// pure View with renderToStaticMarkup. The live Picker JS is an external global
// (window.google.picker) and is intentionally not exercised there — but the
// "setAppId wiring" describe block below DOES exercise the hooks-wired
// GoogleDrivePicker wrapper end to end, with window.google.picker mocked, so
// this whole file now needs jsdom rather than the default node environment.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import {
  cleanup,
  fireEvent,
  render as renderDom,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { GoogleDrivePickedFile, GoogleDrivePickerToken } from "../../../lib/api"

const getGoogleDrivePickerTokenMock =
  vi.fn<() => Promise<GoogleDrivePickerToken>>()

vi.mock("../../../lib/api", async () => {
  const actual =
    await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    connectorsApi: {
      ...actual.connectorsApi,
      getGoogleDrivePickerToken: () => getGoogleDrivePickerTokenMock(),
    },
  }
})

import {
  GoogleDrivePicker,
  GoogleDrivePickerView,
  syncFailureMessage,
} from "../GoogleDrivePicker"

const FILES: GoogleDrivePickedFile[] = [
  { id: "file0001", name: "Product Plan" },
  { id: "file0002" }, // no name → falls back to id
]

const noop = () => {}

type Props = React.ComponentProps<typeof GoogleDrivePickerView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    savedFiles: FILES,
    configured: true,
    busy: false,
    error: null,
    onAddFiles: noop,
  }
  return renderToStaticMarkup(
    React.createElement(GoogleDrivePickerView, { ...defaults, ...override }),
  )
}

describe("GoogleDrivePickerView", () => {
  it("renders each saved file (name, or id when unnamed)", () => {
    const html = render()
    expect(html).toContain("Product Plan")
    expect(html).toContain("file0002") // unnamed file falls back to its id
  })

  it("renders the 'Add Drive files' button", () => {
    const html = render()
    expect(html).toContain("Add Drive files")
  })

  it("shows the empty state when there are no saved files", () => {
    const html = render({ savedFiles: [] })
    expect(html).toContain("No Drive files selected yet")
  })

  it("disables the button and shows 'Opening…' while busy", () => {
    const html = render({ busy: true })
    expect(html).toContain("Opening…")
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Opening…<\/button>/)
  })

  it("surfaces an error message when one is set", () => {
    const html = render({ error: "Token fetch failed" })
    expect(html).toContain("Token fetch failed")
  })

  it("renders the 'not configured' message when the API key is absent", () => {
    const html = render({ configured: false })
    expect(html).toContain("isn")
    expect(html.toLowerCase()).toContain("configured")
    // The Add button is not rendered in the unconfigured state.
    expect(html).not.toContain("Add Drive files")
  })

  it("renders the Selected files heading", () => {
    const html = render()
    expect(html).toContain("Selected files")
    expect(html).not.toContain("Synced files")
  })

  it("surfaces a sync failure message in the alert region", () => {
    const html = render({ error: "Xometry: is a folder" })
    expect(html).toContain('role="alert"')
    expect(html).toContain("Xometry: is a folder")
  })
})

describe("syncFailureMessage", () => {
  it("returns null for empty and undefined", () => {
    expect(syncFailureMessage([])).toBeNull()
    expect(syncFailureMessage(undefined)).toBeNull()
  })

  it("formats a single failure", () => {
    expect(syncFailureMessage([{ name: "Xometry", error: "is a folder" }])).toBe(
      "Xometry: is a folder",
    )
  })

  it("appends a count for multiple failures", () => {
    expect(
      syncFailureMessage([
        { name: "Xometry", error: "is a folder" },
        { name: "archive.zip", error: "Unsupported file type (application/zip)" },
      ]),
    ).toBe("Xometry: is a folder (+1 more)")
  })

  it("ignores malformed entries", () => {
    expect(
      syncFailureMessage([
        { name: "a", error: "" },
        { name: "b", error: undefined as unknown as string },
      ]),
    ).toBeNull()
  })
})

// ───────────────────── setAppId wiring (hooks-wired wrapper) ─────────────────────
//
// Under drive.file scope, the Picker only binds a picked file to this app when
// .setAppId() is called on the PickerBuilder before .build() — see
// GoogleDrivePicker.tsx's handleAddFiles. window.google.picker is mocked here
// (never the real Google CDN script); connectorsApi.getGoogleDrivePickerToken is
// mocked via the module-level vi.mock above so no network call happens.
declare global {
  interface Window {
    gapi?: { load: (name: string, cb: () => void) => void }
    google?: {
      picker?: {
        PickerBuilder: new () => unknown
        DocsView: new (viewId?: unknown) => { setMode: (m: unknown) => unknown }
        ViewId: { DOCS: unknown }
        DocsViewMode: { LIST: unknown }
        Feature: { MULTISELECT_ENABLED: unknown }
        Action: { PICKED: string }
        Response: { ACTION: string; DOCUMENTS: string }
      }
    }
  }
}

type MockBuilder = {
  setDeveloperKey: ReturnType<typeof vi.fn>
  setOAuthToken: ReturnType<typeof vi.fn>
  addView: ReturnType<typeof vi.fn>
  enableFeature: ReturnType<typeof vi.fn>
  setCallback: ReturnType<typeof vi.fn>
  setAppId: ReturnType<typeof vi.fn>
  build: ReturnType<typeof vi.fn>
}

function installMockPicker(): MockBuilder {
  const setVisible = vi.fn()
  const builder = {} as MockBuilder
  builder.setDeveloperKey = vi.fn(() => builder)
  builder.setOAuthToken = vi.fn(() => builder)
  builder.addView = vi.fn(() => builder)
  builder.enableFeature = vi.fn(() => builder)
  builder.setCallback = vi.fn(() => builder)
  builder.setAppId = vi.fn(() => builder)
  builder.build = vi.fn(() => ({ setVisible }))

  window.google = {
    picker: {
      // Called with `new` in production code; returning an object from a
      // plain function invoked via `new` overrides the implicit `this`, so
      // this stands in for a real constructor.
      PickerBuilder: vi.fn(() => builder) as unknown as new () => unknown,
      DocsView: vi.fn(() => ({ setMode: vi.fn() })) as unknown as new (
        viewId?: unknown,
      ) => { setMode: (m: unknown) => unknown },
      ViewId: { DOCS: "DOCS" },
      DocsViewMode: { LIST: "LIST" },
      Feature: { MULTISELECT_ENABLED: "MULTISELECT_ENABLED" },
      Action: { PICKED: "picked" },
      Response: { ACTION: "action", DOCUMENTS: "docs" },
    },
  }

  return builder
}

describe("GoogleDrivePicker — setAppId wiring", () => {
  const ORIGINAL_API_KEY = process.env.NEXT_PUBLIC_GOOGLE_API_KEY

  beforeEach(() => {
    process.env.NEXT_PUBLIC_GOOGLE_API_KEY = "test-google-api-key"
    getGoogleDrivePickerTokenMock.mockReset()
  })

  afterEach(() => {
    cleanup()
    delete window.google
    delete window.gapi
    if (ORIGINAL_API_KEY === undefined) {
      delete process.env.NEXT_PUBLIC_GOOGLE_API_KEY
    } else {
      process.env.NEXT_PUBLIC_GOOGLE_API_KEY = ORIGINAL_API_KEY
    }
  })

  it("T4 — calls setAppId with the token's app_id before build() (RED-first: fails on unfixed code, which never calls setAppId at all)", async () => {
    // A distinctive value sourced from the mocked token response — not a
    // literal the pre-fix code could stumble into matching by coincidence.
    const DISTINCTIVE_APP_ID = "928374651-mocked-cloud-project"
    getGoogleDrivePickerTokenMock.mockResolvedValue({
      access_token: "ya29.mock-access-token",
      expires_in: 3000,
      app_id: DISTINCTIVE_APP_ID,
    })
    const builder = installMockPicker()

    renderDom(<GoogleDrivePicker dataset="acme" savedFiles={[]} />)
    fireEvent.click(screen.getByRole("button", { name: /add drive files/i }))

    await waitFor(() => expect(builder.build).toHaveBeenCalled())

    expect(builder.setAppId).toHaveBeenCalledWith(DISTINCTIVE_APP_ID)
    // setAppId must be called BEFORE build(), not after — both mock call
    // orders are tracked on a shared vi mock invocation counter.
    const setAppIdOrder = builder.setAppId.mock.invocationCallOrder[0]
    const buildOrder = builder.build.mock.invocationCallOrder[0]
    expect(setAppIdOrder).toBeLessThan(buildOrder)
  })

  it("T5 — skips setAppId when app_id is absent, but still opens the picker", async () => {
    getGoogleDrivePickerTokenMock.mockResolvedValue({
      access_token: "ya29.mock-access-token",
      expires_in: 3000,
      // app_id deliberately omitted.
    })
    const builder = installMockPicker()

    renderDom(<GoogleDrivePicker dataset="acme" savedFiles={[]} />)
    fireEvent.click(screen.getByRole("button", { name: /add drive files/i }))

    await waitFor(() => expect(builder.build).toHaveBeenCalled())

    expect(builder.setAppId).not.toHaveBeenCalled()
  })

  it("T5b — skips setAppId when app_id is the empty string", async () => {
    getGoogleDrivePickerTokenMock.mockResolvedValue({
      access_token: "ya29.mock-access-token",
      expires_in: 3000,
      app_id: "",
    })
    const builder = installMockPicker()

    renderDom(<GoogleDrivePicker dataset="acme" savedFiles={[]} />)
    fireEvent.click(screen.getByRole("button", { name: /add drive files/i }))

    await waitFor(() => expect(builder.build).toHaveBeenCalled())

    expect(builder.setAppId).not.toHaveBeenCalled()
  })
})
