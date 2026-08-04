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
const saveGoogleDriveFilesMock = vi.fn<(body: unknown) => Promise<unknown>>()

vi.mock("../../../lib/api", async () => {
  const actual =
    await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    connectorsApi: {
      ...actual.connectorsApi,
      getGoogleDrivePickerToken: () => getGoogleDrivePickerTokenMock(),
      saveGoogleDriveFiles: (body: unknown) => saveGoogleDriveFilesMock(body),
    },
  }
})

import {
  GoogleDrivePicker,
  GoogleDrivePickerView,
  mergePickedFiles,
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
    onRemoveFile: noop,
    removingId: null,
    folderContents: undefined,
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

  it("renders a remove button per file, labelled by that file", () => {
    const html = render()
    // Labelled by name so the buttons are distinguishable to a screen reader,
    // and by id for a file Drive gave no name for.
    expect(html).toContain('aria-label="Remove Product Plan"')
    expect(html).toContain('aria-label="Remove file0002"')
  })

  it("disables the remove buttons while a save is in flight", () => {
    const html = render({ busy: true })
    expect(html).toMatch(
      /<button[^>]*class="conn-drive-file-remove"[^>]*disabled/,
    )
  })

  it("a delete reports progress on its own row only", () => {
    // The reported bug: deleting flipped the ADD button to "Opening…", as if
    // the Picker were launching. Progress belongs to the control that was
    // clicked; every other control keeps its own label.
    const html = render({ removingId: "file0001" })
    expect(html).toContain('aria-label="Removing Product Plan"')
    // The untouched row keeps its normal label…
    expect(html).toContain('aria-label="Remove file0002"')
    // …and the Add button never speaks for the delete.
    expect(html).toContain("Add Drive files")
    expect(html).not.toContain("Opening…")
  })

  it("blocks a concurrent add while a delete is in flight", () => {
    // Both actions POST the whole list; overlapping them races one save against
    // the other. Disabled, but never relabelled.
    const html = render({ removingId: "file0001" })
    expect(html).toMatch(/<button[^>]*btn-primary[^>]*disabled/)
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

/** The chainable DocsView the most recent installMockPicker() handed out, so a
 *  test can assert which view options were applied. */
let docsView: {
  setMode: ReturnType<typeof vi.fn>
  setIncludeFolders: ReturnType<typeof vi.fn>
  setSelectFolderEnabled: ReturnType<typeof vi.fn>
}

function installMockPicker(): MockBuilder {
  const setVisible = vi.fn()
  const builder = {} as MockBuilder
  const view = {} as typeof docsView
  view.setMode = vi.fn(() => view)
  view.setIncludeFolders = vi.fn(() => view)
  view.setSelectFolderEnabled = vi.fn(() => view)
  docsView = view
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
      // Chainable, because the production builder applies the folder options
      // in sequence after setMode(); a mock that returns undefined from
      // setMode would throw on the next call in the chain rather than
      // exercising it.
      DocsView: vi.fn(() => docsView) as unknown as new (
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

describe("mergePickedFiles", () => {
  // The save endpoint REPLACES the stored list, and the Picker returns only
  // what was chosen this time. Posting that raw made every "Add Drive files"
  // discard everything added before it, with no remove control to put it back.
  it("appends new files instead of replacing the saved ones", () => {
    expect(mergePickedFiles(FILES, [{ id: "file0003", name: "Spec" }])).toEqual([
      { id: "file0001", name: "Product Plan" },
      { id: "file0002", name: undefined },
      { id: "file0003", name: "Spec" },
    ])
  })

  it("keeps a re-picked file single, and refreshes its name", () => {
    // Same id picked again after a rename in Drive: one row, the newer name.
    expect(
      mergePickedFiles(FILES, [{ id: "file0001", name: "Product Plan v2" }]),
    ).toEqual([
      { id: "file0001", name: "Product Plan v2" },
      { id: "file0002", name: undefined },
    ])
  })

  it("keeps the existing name when a re-pick carries none", () => {
    expect(mergePickedFiles(FILES, [{ id: "file0001" }])[0]).toEqual({
      id: "file0001",
      name: "Product Plan",
    })
  })

  it("handles a multiselect of several files at once", () => {
    const picked = [
      { id: "a", name: "A" },
      { id: "b", name: "B" },
      { id: "c", name: "C" },
    ]
    expect(mergePickedFiles([], picked)).toEqual(picked)
    expect(mergePickedFiles(FILES, picked)).toHaveLength(5)
  })

  it("is a no-op on an empty pick and preserves order", () => {
    expect(mergePickedFiles(FILES, [])).toEqual(FILES)
  })
})

describe("GoogleDrivePicker — add merges, remove subtracts", () => {
  const ORIGINAL_API_KEY = process.env.NEXT_PUBLIC_GOOGLE_API_KEY

  beforeEach(() => {
    process.env.NEXT_PUBLIC_GOOGLE_API_KEY = "test-google-api-key"
    getGoogleDrivePickerTokenMock.mockReset().mockResolvedValue({
      access_token: "ya29.mock-access-token",
      expires_in: 3000,
      app_id: "928374651",
    })
    saveGoogleDriveFilesMock.mockReset().mockResolvedValue({ errors: [] })
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

  it("POSTs the saved files PLUS the new pick, not just the new pick", async () => {
    // The reported bug end to end: a second visit to the Picker wiped the
    // first visit's file, because only the fresh selection reached an endpoint
    // that replaces the whole list.
    const builder = installMockPicker()
    renderDom(
      <GoogleDrivePicker
        dataset="acme"
        savedFiles={[{ id: "already-saved", name: "Earlier File" }]}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /add drive files/i }))
    await waitFor(() => expect(builder.build).toHaveBeenCalled())

    const onPicked = builder.setCallback.mock.calls[0][0] as (d: unknown) => void
    onPicked({
      action: "picked",
      docs: [
        { id: "new-1", name: "New One" },
        { id: "new-2", name: "New Two" },
      ],
    })

    await waitFor(() => expect(saveGoogleDriveFilesMock).toHaveBeenCalled())
    expect(saveGoogleDriveFilesMock.mock.calls[0][0]).toEqual({
      files: [
        { id: "already-saved", name: "Earlier File" },
        { id: "new-1", name: "New One" },
        { id: "new-2", name: "New Two" },
      ],
    })
  })

  it("removes one file by saving everything except it", async () => {
    renderDom(
      <GoogleDrivePicker
        dataset="acme"
        savedFiles={[
          { id: "keep-1", name: "Keep One" },
          { id: "drop-me", name: "Drop Me" },
          { id: "keep-2", name: "Keep Two" },
        ]}
      />,
    )

    fireEvent.click(screen.getByLabelText("Remove Drop Me"))

    await waitFor(() => expect(saveGoogleDriveFilesMock).toHaveBeenCalled())
    expect(saveGoogleDriveFilesMock.mock.calls[0][0]).toEqual({
      files: [
        { id: "keep-1", name: "Keep One" },
        { id: "keep-2", name: "Keep Two" },
      ],
    })
  })

  it("removing the last file saves an empty list", async () => {
    // An empty picked list is a graceful no-op server-side, so clearing the set
    // must be expressible — otherwise the last file could never be removed.
    renderDom(
      <GoogleDrivePicker
        dataset="acme"
        savedFiles={[{ id: "only", name: "Only File" }]}
      />,
    )

    fireEvent.click(screen.getByLabelText("Remove Only File"))

    await waitFor(() => expect(saveGoogleDriveFilesMock).toHaveBeenCalled())
    expect(saveGoogleDriveFilesMock.mock.calls[0][0]).toEqual({ files: [] })
  })

  it("a remove never opens the Picker", async () => {
    // Remove and add share an endpoint, not a code path — a delete must not
    // mint a picker token or load the widget.
    renderDom(
      <GoogleDrivePicker
        dataset="acme"
        savedFiles={[{ id: "only", name: "Only File" }]}
      />,
    )

    fireEvent.click(screen.getByLabelText("Remove Only File"))

    await waitFor(() => expect(saveGoogleDriveFilesMock).toHaveBeenCalled())
    expect(getGoogleDrivePickerTokenMock).not.toHaveBeenCalled()
  })
})

describe("GoogleDrivePicker — folders are pickable", () => {
  const ORIGINAL_API_KEY = process.env.NEXT_PUBLIC_GOOGLE_API_KEY

  beforeEach(() => {
    process.env.NEXT_PUBLIC_GOOGLE_API_KEY = "test-google-api-key"
    getGoogleDrivePickerTokenMock.mockReset().mockResolvedValue({
      access_token: "ya29.mock-access-token",
      expires_in: 3000,
      app_id: "928374651",
    })
    saveGoogleDriveFilesMock.mockReset().mockResolvedValue({ errors: [] })
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

  it("shows folders for browsing but does NOT let them be selected", async () => {
    // Both are needed and they are different settings: showing folders only
    // makes them navigable, which is the behaviour we already had. Selecting is
    // what turns a folder into a source — and into a standing one, since the
    // sync re-expands it on every run.
    const builder = installMockPicker()
    renderDom(<GoogleDrivePicker dataset="acme" savedFiles={[]} />)
    fireEvent.click(screen.getByRole("button", { name: /add drive files/i }))

    await waitFor(() => expect(builder.build).toHaveBeenCalled())

    expect(docsView.setIncludeFolders).toHaveBeenCalledWith(true)
    expect(docsView.setSelectFolderEnabled).toHaveBeenCalledWith(false)
  })

  it("a picked folder is saved like any other entry", async () => {
    // The frontend does not care that it is a folder — the sync resolves that
    // from Drive metadata. Sending a `kind` the client guessed would be a
    // second source of truth for something Drive already answers.
    const builder = installMockPicker()
    renderDom(<GoogleDrivePicker dataset="acme" savedFiles={[]} />)
    fireEvent.click(screen.getByRole("button", { name: /add drive files/i }))
    await waitFor(() => expect(builder.build).toHaveBeenCalled())

    const onPicked = builder.setCallback.mock.calls[0][0] as (d: unknown) => void
    onPicked({ action: "picked", docs: [{ id: "folder-1", name: "Specs" }] })

    await waitFor(() => expect(saveGoogleDriveFilesMock).toHaveBeenCalled())
    expect(saveGoogleDriveFilesMock.mock.calls[0][0]).toEqual({
      files: [{ id: "folder-1", name: "Specs" }],
    })
  })
})

describe("GoogleDrivePickerView — a connected folder shows what is inside it", () => {
  const FOLDER: GoogleDrivePickedFile[] = [{ id: "folder1", name: "Specs" }]

  it("lists the folder's files and counts them", () => {
    // Connecting a folder was invisible: the row showed the folder name and
    // nothing else, so there was no way to tell what had actually come in.
    const html = renderToStaticMarkup(
      React.createElement(GoogleDrivePickerView, {
        savedFiles: FOLDER,
        configured: true,
        busy: false,
        error: null,
        onAddFiles: noop,
        onRemoveFile: noop,
        removingId: null,
        folderContents: {
          folder1: [
            { id: "c1", name: "api.md" },
            { id: "c2", name: "auth.md" },
          ],
        },
      }),
    )
    expect(html).toContain("Specs")
    expect(html).toContain("2 files")
    expect(html).toContain("api.md")
    expect(html).toContain("auth.md")
  })

  it("singularises a one-file folder", () => {
    const html = renderToStaticMarkup(
      React.createElement(GoogleDrivePickerView, {
        savedFiles: FOLDER,
        configured: true,
        busy: false,
        error: null,
        onAddFiles: noop,
        onRemoveFile: noop,
        removingId: null,
        folderContents: { folder1: [{ id: "c1", name: "only.md" }] },
      }),
    )
    expect(html).toContain("1 file")
    expect(html).not.toContain("1 files")
  })

  it("says so when a folder expanded to nothing readable", () => {
    // Distinct from "not expanded yet": an empty array is a real answer, and
    // rendering it as a plain file row would hide that the folder is connected
    // but contributing nothing.
    const html = renderToStaticMarkup(
      React.createElement(GoogleDrivePickerView, {
        savedFiles: FOLDER,
        configured: true,
        busy: false,
        error: null,
        onAddFiles: noop,
        onRemoveFile: noop,
        removingId: null,
        folderContents: { folder1: [] },
      }),
    )
    expect(html).toContain("no readable files")
    expect(html).toContain("select the files themselves")
  })

  it("a plain file is not rendered as a folder", () => {
    const html = render()
    expect(html).not.toContain("conn-drive-folder")
    expect(html).toContain("Product Plan")
  })

  it("a folder can still be removed", () => {
    const html = renderToStaticMarkup(
      React.createElement(GoogleDrivePickerView, {
        savedFiles: FOLDER,
        configured: true,
        busy: false,
        error: null,
        onAddFiles: noop,
        onRemoveFile: noop,
        removingId: null,
        folderContents: { folder1: [{ id: "c1", name: "only.md" }] },
      }),
    )
    expect(html).toContain('aria-label="Remove Specs"')
  })
})
