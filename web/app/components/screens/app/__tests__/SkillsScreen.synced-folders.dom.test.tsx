// @vitest-environment jsdom
//
// Synced GitHub folders on the Skills screen.
//
// Two surfaces, one rule between them: a skill whose text lives in a repo is
// not editable here. The card drops its pencil and trash for a "Synced" badge,
// and the panel below the library is where the folder itself is managed —
// last sync, any error, "Sync now", "Stop syncing".
//
// Stopping is the interesting one, because it is the opposite of destructive:
// the folder stops being watched and its skills STAY, released back to the
// company and editable again. The toast has to say so, since "stop syncing"
// reads like it might take the skills with it.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const customListMock = vi.fn()
const listSourcesMock = vi.fn()
const syncSourceMock = vi.fn()
const stopSyncingMock = vi.fn()
const showToastMock = vi.fn()

vi.mock("../../../../lib/api", () => ({
  skillsApi: {
    list: (...a: unknown[]) => customListMock(...a),
    upload: vi.fn(),
    remove: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    discoverGithub: vi.fn(),
    importGithub: vi.fn(),
    listSources: (...a: unknown[]) => listSourcesMock(...a),
    syncSource: (...a: unknown[]) => syncSourceMock(...a),
    stopSyncingSource: (...a: unknown[]) => stopSyncingMock(...a),
  },
  connectorsApi: {
    list: vi.fn().mockResolvedValue({ connections: [] }),
    listAccessibleGithubRepos: vi.fn().mockResolvedValue({ repositories: [] }),
  },
  isMultiSkillUpload: (r: { skills?: unknown }) => Array.isArray(r?.skills),
}))

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    goTo: vi.fn(),
    setPendingOndemandDraft: vi.fn(),
    showToast: showToastMock,
  }),
}))

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("../../../design-agent/SourceConnectHint", () => ({
  SourceConnectHint: () =>
    React.createElement("button", { type: "button" }, "Connect a repo →"),
}))

vi.mock("../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", null, children),
}))

import { SkillsScreen, folderLabel, syncedAgo } from "../SkillsScreen"

const SYNCED_SKILL = {
  id: "b8f3a1c2-0000-0000-0000-000000000001",
  slug: "sprint-planner",
  trigger: "/sprint-planner",
  name: "Sprint planner",
  description: "Plans a sprint.",
  uploader_name: "GitHub sync",
  created_at: "2026-08-07T10:00:00+00:00",
  has_file: true,
  name_conflict: false,
  synced: true,
}

const OWN_SKILL = {
  ...SYNCED_SKILL,
  id: "b8f3a1c2-0000-0000-0000-000000000002",
  slug: "estimation-helper",
  trigger: "/estimation-helper",
  name: "Estimation helper",
  description: "Scores features.",
  uploader_name: "Fortune Tede",
  synced: false,
}

const FOLDER = {
  id: "src-1",
  repo: "octocat/methods",
  ref: "main",
  path: "skills",
  active: true,
  last_synced_at: "2026-08-07T10:00:00+00:00",
  last_commit_sha: "c0ffee1",
  last_error: "",
}

beforeEach(() => {
  customListMock.mockResolvedValue({ skills: [SYNCED_SKILL, OWN_SKILL] })
  listSourcesMock.mockResolvedValue({ sources: [FOLDER] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderScreen() {
  await act(async () => {
    render(React.createElement(SkillsScreen))
  })
  await waitFor(() => expect(customListMock).toHaveBeenCalled())
  await waitFor(() => expect(listSourcesMock).toHaveBeenCalled())
}

describe("syncedAgo", () => {
  const now = Date.parse("2026-08-07T12:00:00+00:00")

  it("says so plainly when a folder has never synced", () => {
    expect(syncedAgo(null, now)).toBe("Never synced")
    // A malformed timestamp is the same story, not a crash or an "NaNm ago".
    expect(syncedAgo("not-a-date", now)).toBe("Never synced")
  })

  it("scales the unit to the age", () => {
    expect(syncedAgo("2026-08-07T11:59:30+00:00", now)).toBe("Synced just now")
    expect(syncedAgo("2026-08-07T11:20:00+00:00", now)).toBe("Synced 40m ago")
    expect(syncedAgo("2026-08-07T09:00:00+00:00", now)).toBe("Synced 3h ago")
    expect(syncedAgo("2026-08-05T12:00:00+00:00", now)).toBe("Synced 2d ago")
  })
})

describe("folderLabel", () => {
  it("reads repo · folder · branch", () => {
    expect(folderLabel({ repo: "octocat/methods", path: "skills", ref: "main" })).toBe(
      "octocat/methods · skills · main",
    )
  })

  it("omits a branch the folder doesn't pin", () => {
    // An empty ref means "the repo's default branch"; printing a resolved name
    // would claim a pin the row doesn't have.
    expect(folderLabel({ repo: "octocat/methods", path: "skills", ref: "" })).toBe(
      "octocat/methods · skills",
    )
  })
})

describe("SkillsScreen — synced skills", () => {
  it("gives a synced skill a badge instead of edit and delete", async () => {
    await renderScreen()
    // The company's own skill keeps both controls...
    expect(screen.getByLabelText("Edit Estimation helper")).toBeTruthy()
    expect(screen.getByLabelText("Delete Estimation helper")).toBeTruthy()
    // ...and the synced one has neither, because the repo owns its text.
    expect(screen.queryByLabelText("Edit Sprint planner")).toBeNull()
    expect(screen.queryByLabelText("Delete Sprint planner")).toBeNull()
    expect(screen.getByText("Synced")).toBeTruthy()
  })
})

describe("SkillsScreen — the synced folders panel", () => {
  it("lists each folder with when it last ran", async () => {
    await renderScreen()
    expect(screen.getByRole("heading", { name: /Synced folders/ })).toBeTruthy()
    expect(screen.getByText("octocat/methods · skills · main")).toBeTruthy()
    expect(screen.getByText(/Synced .* ago|Synced just now/)).toBeTruthy()
  })

  it("is absent entirely when nothing is synced", async () => {
    listSourcesMock.mockResolvedValue({ sources: [] })
    await renderScreen()
    expect(screen.queryByRole("heading", { name: /Synced folders/ })).toBeNull()
  })

  it("hides a stopped folder — it has nothing left to manage", async () => {
    listSourcesMock.mockResolvedValue({ sources: [{ ...FOLDER, active: false }] })
    await renderScreen()
    expect(screen.queryByRole("heading", { name: /Synced folders/ })).toBeNull()
  })

  it("shows a failure instead of a reassuring timestamp", async () => {
    // "Synced 4m ago" next to a failed sync would read as though it worked,
    // which is the one thing this row must never imply.
    listSourcesMock.mockResolvedValue({
      sources: [{ ...FOLDER, last_error: "We couldn't find “skills” in octocat/methods." }],
    })
    await renderScreen()
    expect(screen.getByText(/We couldn't find/)).toBeTruthy()
    expect(screen.queryByText(/Synced .* ago/)).toBeNull()
  })

  it("syncs a folder on demand and reports what landed", async () => {
    syncSourceMock.mockResolvedValue({
      source: FOLDER,
      imported: 2,
      replaced: 0,
      skipped: [],
      error: "",
    })
    await renderScreen()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Sync now/ }))
    })
    expect(syncSourceMock).toHaveBeenCalledWith("src-1")
    expect(showToastMock).toHaveBeenCalledWith(
      "Folder synced",
      expect.stringContaining("2 skills imported"),
    )
    // The library is re-read, because the sync may have added skills.
    expect(customListMock).toHaveBeenCalledTimes(2)
  })

  it("says nothing changed rather than claiming an import", async () => {
    syncSourceMock.mockResolvedValue({
      source: FOLDER,
      imported: 0,
      replaced: 0,
      skipped: [],
      error: "",
    })
    await renderScreen()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Sync now/ }))
    })
    expect(showToastMock).toHaveBeenCalledWith(
      "Folder synced",
      expect.stringContaining("already in your library"),
    )
  })

  it("surfaces a sync that failed, even though the call succeeded", async () => {
    // The route answers 200 carrying the error, so a GitHub outage is something
    // the user reads rather than an exception the button can't explain.
    syncSourceMock.mockResolvedValue({
      source: FOLDER,
      imported: 0,
      replaced: 0,
      skipped: [],
      error: "Couldn't reach GitHub. Please try again.",
    })
    await renderScreen()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Sync now/ }))
    })
    expect(showToastMock).toHaveBeenCalledWith(
      "Couldn't sync that folder",
      "Couldn't reach GitHub. Please try again.",
    )
  })

  it("stops syncing, drops the row, and promises the skills stayed", async () => {
    stopSyncingMock.mockResolvedValue({ stopped: true, id: "src-1", released: 2 })
    customListMock.mockResolvedValue({
      skills: [{ ...SYNCED_SKILL, synced: false }, OWN_SKILL],
    })
    await renderScreen()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Stop syncing/ }))
    })
    expect(stopSyncingMock).toHaveBeenCalledWith("src-1")
    // The panel goes, because the folder is no longer watched.
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: /Synced folders/ })).toBeNull(),
    )
    // And the toast has to say the skills survived — "stop syncing" reads like
    // it might have taken them along.
    expect(showToastMock).toHaveBeenCalledWith(
      "Stopped syncing",
      expect.stringContaining("2 skills stayed in your library"),
    )
    // Released means editable: the re-read list gives the card its pencil back.
    await waitFor(() =>
      expect(screen.getByLabelText("Edit Sprint planner")).toBeTruthy(),
    )
  })
})
