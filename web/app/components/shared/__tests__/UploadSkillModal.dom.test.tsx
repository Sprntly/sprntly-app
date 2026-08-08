// @vitest-environment jsdom
//
// UploadSkillModal: the custom-skill upload form (PRD 1854 happy path).
// Client-side mirror of the server gates — required name/description with
// touched-empty highlighting, .md/.zip-only file pick, 20 MB pre-check, the
// 50,000-character content pre-check for bare .md files — and the retry
// contract: a failed upload keeps every input intact.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// The GitHub source asks the connectors API whether GitHub is connected and
// which repos the installation can see; discovery and import come in as props.
const connectorsListMock = vi.fn()
const reposMock = vi.fn()
vi.mock("../../../lib/api", () => ({
  connectorsApi: {
    list: (...a: unknown[]) => connectorsListMock(...a),
    listAccessibleGithubRepos: (...a: unknown[]) => reposMock(...a),
  },
}))
vi.mock("../../../lib/generateConnectorRowState", () => ({
  getGenerateConnectorRowState: (c: { status?: string } | undefined) => ({
    connected: c?.status === "connected",
  }),
}))
// SourceConnectHint pulls in the design-agent drawer for its redirect; the
// modal only needs the affordance, so stub it to the label under test.
vi.mock("../../design-agent/SourceConnectHint", () => ({
  SourceConnectHint: () =>
    React.createElement("button", { type: "button" }, "Connect a repo →"),
}))

import {
  MAX_SKILL_CONTENT_CHARS,
  MAX_SKILL_FILE_BYTES,
  UploadSkillModal,
  countLine,
  skillFileError,
  slugifyName,
} from "../UploadSkillModal"

beforeEach(() => {
  connectorsListMock.mockResolvedValue({
    connections: [{ provider: "github", status: "connected" }],
  })
  reposMock.mockResolvedValue({
    repositories: [
      { full_name: "octocat/methods", default_branch: "trunk" },
      { full_name: "octocat/other", default_branch: "main" },
    ],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function pickFile(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
}

const MD_FILE = () => new File(["# method"], "skill.md", { type: "text/markdown" })

describe("skillFileError", () => {
  it("accepts .md and .zip (case-insensitive), rejects everything else", () => {
    expect(skillFileError(new File(["x"], "a.md"))).toBeNull()
    expect(skillFileError(new File(["x"], "a.zip"))).toBeNull()
    expect(skillFileError(new File(["x"], "A.MD"))).toBeNull()
    expect(skillFileError(new File(["x"], "a.pdf"))).toMatch(/Only \.md files and \.zip/)
    expect(skillFileError(new File(["x"], "noext"))).toMatch(/Only \.md files and \.zip/)
  })

  it("rejects a file over 20 MB (boundary is inclusive)", () => {
    const atLimit = new File(["x"], "a.md")
    Object.defineProperty(atLimit, "size", { value: MAX_SKILL_FILE_BYTES })
    expect(skillFileError(atLimit)).toBeNull()

    const over = new File(["x"], "a.md")
    Object.defineProperty(over, "size", { value: MAX_SKILL_FILE_BYTES + 1 })
    expect(skillFileError(over)).toMatch(/20 MB/)
  })
})

describe("countLine", () => {
  it("counts additions and updates, singular where it matters", () => {
    expect(countLine(4, 1)).toBe("4 skills added, 1 updated.")
    expect(countLine(1, 0)).toBe("1 skill added.")
    expect(countLine(0, 3)).toBe("3 updated.")
  })
})

describe("slugifyName", () => {
  it("mirrors the backend slugify (lowercase kebab, no edge hyphens)", () => {
    expect(slugifyName("PRD Author")).toBe("prd-author")
    expect(slugifyName("  Roadmap!! v2 ")).toBe("roadmap-v2")
    expect(slugifyName("!!!")).toBe("")
  })
})

describe("UploadSkillModal", () => {
  it("previews the trigger — without blocking — when a built-in has the name", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload,
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
      }),
    )
    // The pick comes first: only a .md brings up the name/description fields.
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "PRD Author" },
      })
    })
    // Informational status, not an alert: nothing is replaced — the upload
    // proceeds and just gets its own trigger, which the notice names.
    const notice = screen.getByRole("status").textContent ?? ""
    expect(notice).toMatch(/both stay in your library/i)
    expect(notice).toContain("/prd-author-2")

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Our own PRD flow." },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })
    await waitFor(() => expect(onUpload).toHaveBeenCalled())
  })

  it("packs a picked folder into a zip and uploads it through the same path", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload,
        onClose: vi.fn(),
      }),
    )
    const folderInput = container.querySelectorAll(
      'input[type="file"]',
    )[1] as HTMLInputElement
    // The folder input is the directory picker, not a second plain file pick.
    expect(folderInput.hasAttribute("webkitdirectory")).toBe(true)

    const md = new File(["# Gov"], "governance-skill.md", { type: "text/markdown" })
    Object.defineProperty(md, "webkitRelativePath", {
      value: "My Skills/governance-skill.md",
    })
    await act(async () => {
      fireEvent.change(folderInput, { target: { files: [md] } })
    })
    // The packed zip takes the file slot, named after the folder itself.
    await screen.findByText("My Skills.zip")

    // An archive names its skills from their own content, so the name and
    // description fields leave the form — no fields to fill, straight to
    // upload.
    expect(screen.queryByLabelText(/skill name/i)).toBeNull()
    expect(screen.queryByLabelText(/what does this skill do/i)).toBeNull()

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })
    await waitFor(() => expect(onUpload).toHaveBeenCalled())
    const [sent, sentName, sentDescription] = onUpload.mock.calls[0] as [
      File,
      string,
      string,
    ]
    expect(sent.name).toBe("My Skills.zip")
    expect(sent.type).toBe("application/zip")
    // Nothing typed can shadow the archive's own naming.
    expect(sentName).toBe("")
    expect(sentDescription).toBe("")
  })

  it("keeps the folder pick inside the picker: no .md files is an inline error", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
      }),
    )
    const folderInput = container.querySelectorAll(
      'input[type="file"]',
    )[1] as HTMLInputElement
    const png = new File(["png"], "logo.png", { type: "image/png" })
    await act(async () => {
      fireEvent.change(folderInput, { target: { files: [png] } })
    })
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(/no \.md files/),
    )
  })

  it("previewed trigger skips a slug the company already handed out", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
        customSkills: [{ slug: "prd-author-2", name: "PRD Author 2" }],
      }),
    )
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "PRD Author" },
      })
    })
    expect(screen.getByRole("status").textContent).toContain("/prd-author-3")
  })

  it("warns that the company's OWN same-named skill will be replaced", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload,
        onClose: vi.fn(),
        customSkills: [{ slug: "estimation-helper-2", name: "Estimation Helper" }],
      }),
    )
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      // Same name once slugified — the equivalence the server replaces on.
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "estimation  helper!" },
      })
    })
    // Announced as an alert (it overwrites something the team has) and it
    // names the trigger the replacement keeps — the STORED slug, which may
    // have been disambiguated away from the name's plain slug.
    const notice = screen.getByRole("alert").textContent ?? ""
    expect(notice).toMatch(/replaces it with this version/i)
    expect(notice).toContain("/estimation-helper-2")

    // It is a warning, not a block: the upload still goes through.
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Ours, v2." },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })
    await waitFor(() => expect(onUpload).toHaveBeenCalled())
  })

  it("shows no name notice for a free name", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
        builtinSlugs: ["prd-author"],
        customSkills: [{ slug: "estimation-helper", name: "Estimation Helper" }],
      }),
    )
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), {
        target: { value: "My Own Skill" },
      })
    })
    expect(screen.queryByRole("status")).toBeNull()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("opens with just the pickers — the fields wait for a .md pick", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
      }),
    )
    // Nothing picked: no name, no description — the pick is the first step.
    expect(screen.queryByLabelText(/skill name/i)).toBeNull()
    expect(screen.queryByLabelText(/what does this skill do/i)).toBeNull()

    await act(async () => {
      pickFile(container, MD_FILE())
    })
    // A bare .md can't name itself, so now the fields appear.
    expect(screen.getByLabelText(/skill name/i)).toBeTruthy()
    expect(screen.getByLabelText(/what does this skill do/i)).toBeTruthy()
  })

  it("gates submit until the .md pick has a name and description", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    const submit = screen.getByRole("button", { name: /upload skill/i }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)

    await act(async () => {
      pickFile(container, MD_FILE())
    })
    expect(submit.disabled).toBe(true)

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "My skill" } })
    })
    expect(submit.disabled).toBe(true)

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Does things." },
      })
    })
    expect(submit.disabled).toBe(false)
  })

  it("a zip pick needs no fields: submit is ready at once", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload: vi.fn(), onClose: vi.fn() }),
    )
    await act(async () => {
      pickFile(container, new File(["zip"], "skills.zip", { type: "application/zip" }))
    })
    // The archive names its skills itself — no fields, nothing to fill.
    expect(screen.queryByLabelText(/skill name/i)).toBeNull()
    const submit = screen.getByRole("button", { name: /upload skill/i }) as HTMLButtonElement
    expect(submit.disabled).toBe(false)
  })

  it("highlights a required field that was touched and left empty", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, {
        open: true,
        onUpload: vi.fn(),
        onClose: vi.fn(),
      }),
    )
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    const name = screen.getByLabelText(/skill name/i)
    await act(async () => {
      fireEvent.change(name, { target: { value: "x" } })
      fireEvent.change(name, { target: { value: "" } })
    })
    expect(screen.getByText("Skill name is required.")).toBeTruthy()
    expect((name as HTMLInputElement).getAttribute("aria-invalid")).toBe("true")
  })

  it("shows the type error immediately on a bad pick", async () => {
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload: vi.fn(), onClose: vi.fn() }),
    )
    await act(async () => {
      pickFile(container, new File(["%PDF"], "skill.pdf"))
    })
    expect(screen.getByText(/Only \.md files and \.zip archives/)).toBeTruthy()
  })

  it("rejects a .md over the character cap on submit, without uploading", async () => {
    const onUpload = vi.fn()
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    await act(async () => {
      pickFile(
        container,
        new File(["x".repeat(MAX_SKILL_CONTENT_CHARS + 1)], "big.md", {
          type: "text/markdown",
        }),
      )
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Big" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() =>
      expect(screen.getByText(/50,000 character limit/)).toBeTruthy(),
    )
    expect(onUpload).not.toHaveBeenCalled()
    // Inputs survive so the user can trim and retry (same retry contract as
    // a server rejection).
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe("Big")
  })

  it("accepts a .md exactly at the character cap (inclusive boundary)", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    await act(async () => {
      pickFile(
        container,
        new File(["x".repeat(MAX_SKILL_CONTENT_CHARS)], "atcap.md", {
          type: "text/markdown",
        }),
      )
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "At cap" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() => expect(onUpload).toHaveBeenCalled())
  })

  it("keeps inputs intact when the upload fails, so the user can retry", async () => {
    // Any server rejection — this one is the concurrent-upload 409 the route
    // still returns when two uploads race for the same free trigger.
    const onUpload = vi
      .fn()
      .mockRejectedValue(new Error("Another upload just took this skill's trigger. Please try again."))
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose: vi.fn() }),
    )
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Dupe" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() =>
      expect(screen.getByText(/just took this skill's trigger/)).toBeTruthy(),
    )
    expect((screen.getByLabelText(/skill name/i) as HTMLInputElement).value).toBe("Dupe")
    expect(screen.getByText("skill.md")).toBeTruthy()
  })

  // A .zip holding a folder per SKILL.md imports as several skills at once.
  // The upload resolves with the list instead of nothing, and the modal owes
  // the user the outcome: which skills exist now, under which triggers, which
  // were updated rather than added, and what it couldn't import.
  describe("a multi-skill archive", () => {
    const MULTI = {
      skills: [
        {
          id: "s1", slug: "sprint-planner", trigger: "/sprint-planner",
          name: "Sprint Planner", description: "Plans a sprint.",
          uploader_name: "Dana Whitfield", created_at: null, has_file: true,
          name_conflict: false, replaced: false,
        },
        {
          id: "s2", slug: "pricing-review-2", trigger: "/pricing-review-2",
          name: "Pricing Review", description: "Reviews pricing.",
          uploader_name: "Dana Whitfield", created_at: null, has_file: true,
          name_conflict: true, replaced: true,
        },
      ],
      skipped: [
        { path: "bloated", name: "Bloated", reason: "it is over the 50,000 character limit" },
      ],
    }

    async function uploadMulti(result: typeof MULTI) {
      const onUpload = vi.fn().mockResolvedValue(result)
      const onClose = vi.fn()
      const { container } = render(
        React.createElement(UploadSkillModal, { open: true, onUpload, onClose }),
      )
      // A zip shows no name/description fields at all — pick and go.
      await act(async () => {
        pickFile(container, new File(["zip"], "skills.zip", { type: "application/zip" }))
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
      })
      await waitFor(() => expect(onUpload).toHaveBeenCalled())
      return { onClose }
    }

    it("stays open and reports every skill it created, with its trigger", async () => {
      const { onClose } = await uploadMulti(MULTI)

      // Not closed — a toast cannot carry per-skill triggers.
      expect(onClose).not.toHaveBeenCalled()
      expect(screen.getByRole("dialog", { name: /skills imported/i })).toBeTruthy()
      expect(screen.getByText("/sprint-planner")).toBeTruthy()
      expect(screen.getByText("/pricing-review-2")).toBeTruthy()
      // One added, one updated — counted, not conflated.
      expect(screen.getByRole("status").textContent).toMatch(/1 skill added, 1 updated\./)
      expect(screen.getByText("updated")).toBeTruthy()
      // …and it says the typed name/description were not used, because the
      // archive named its own skills.
      expect(screen.getByRole("status").textContent).toMatch(/named from its own SKILL\.md/i)
    })

    it("lists every folder it could not import, with the reason", async () => {
      await uploadMulti(MULTI)
      expect(screen.getByRole("alert").textContent).toMatch(/One folder wasn’t imported/)
      expect(screen.getByText(/Bloated — it is over the 50,000 character limit/)).toBeTruthy()
    })

    it("closes from Done", async () => {
      const { onClose } = await uploadMulti(MULTI)
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /^done$/i }))
      })
      expect(onClose).toHaveBeenCalled()
    })

    it("says nothing about skipped folders when there were none", async () => {
      await uploadMulti({ ...MULTI, skipped: [] })
      expect(screen.queryByRole("alert")).toBeNull()
    })
  })

  // Importing from a connected repo: same modal, second source. The file
  // branch above must keep working untouched, so every test here goes through
  // the source toggle first.
  describe("the GitHub source", () => {
    const DISCOVERY = {
      repo: "octocat/methods",
      ref: "trunk",
      commit_sha: "c0ffee",
      truncated: false,
      notes: [] as string[],
      skills: [
        {
          path: "skills/sprint-planner", name: "Sprint Planner",
          description: "Plans a sprint.", slug_preview: "sprint-planner",
          trigger_preview: "/sprint-planner", file_count: 2, char_count: 900,
          status: "new" as const, reason: "",
        },
        {
          path: "skills/pricing-review", name: "Pricing Review",
          description: "Reviews pricing.", slug_preview: "pricing-review",
          trigger_preview: "/pricing-review", file_count: 1, char_count: 400,
          status: "replaces" as const, reason: "",
        },
        {
          path: "skills/bare", name: "Bare", description: "",
          slug_preview: "bare", trigger_preview: "/bare", file_count: 1,
          char_count: 20, status: "invalid" as const,
          reason: "we couldn’t work out a description for it",
        },
      ],
    }

    async function openGithub(overrides: Record<string, unknown> = {}) {
      const onDiscoverGithub = vi.fn().mockResolvedValue(DISCOVERY)
      const onImportGithub = vi.fn().mockResolvedValue(undefined)
      const onClose = vi.fn()
      render(
        React.createElement(UploadSkillModal, {
          open: true,
          onUpload: vi.fn(),
          onClose,
          onDiscoverGithub,
          onImportGithub,
          ...overrides,
        }),
      )
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /from github/i }))
      })
      return { onDiscoverGithub, onImportGithub, onClose }
    }

    async function search() {
      await act(async () => {
        fireEvent.change(screen.getByLabelText(/repository/i), {
          target: { value: "octocat/methods" },
        })
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /find skills/i }))
      })
    }

    it("offers the connect path when GitHub isn't connected", async () => {
      connectorsListMock.mockResolvedValue({ connections: [] })
      await openGithub()
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /connect a repo/i })).toBeTruthy(),
      )
      // No repo picker to tease with — there is nothing to pick from yet.
      expect(screen.queryByLabelText(/repository/i)).toBeNull()
    })

    it("defaults the branch to the picked repo's own default", async () => {
      await openGithub()
      await waitFor(() => expect(reposMock).toHaveBeenCalled())
      await act(async () => {
        fireEvent.change(screen.getByLabelText(/repository/i), {
          target: { value: "octocat/methods" },
        })
      })
      // Not "main": plenty of repos still ship something else, and a wrong
      // default reads to the user as "no skills found".
      expect((screen.getByLabelText(/branch/i) as HTMLInputElement).value).toBe("trunk")
    })

    it("lists what the repo holds, with triggers, replace badges and reasons", async () => {
      const { onDiscoverGithub } = await openGithub()
      await waitFor(() => expect(reposMock).toHaveBeenCalled())
      await search()

      expect(onDiscoverGithub).toHaveBeenCalledWith("octocat/methods", {
        ref: "trunk",
        path: "",
      })
      expect(screen.getByText("/sprint-planner")).toBeTruthy()
      expect(screen.getByText("replaces yours")).toBeTruthy()
      // The unimportable one is SHOWN with its reason and cannot be ticked —
      // hiding it would just look like the repo has fewer skills.
      const bare = screen.getByRole("checkbox", { name: /bare/i }) as HTMLInputElement
      expect(bare.disabled).toBe(true)
      expect(screen.getByText(/couldn’t work out a description/)).toBeTruthy()
    })

    it("ticks nothing by default and gates import on a selection", async () => {
      const { onImportGithub } = await openGithub()
      await waitFor(() => expect(reposMock).toHaveBeenCalled())
      await search()

      // Every custom skill's description rides the router on every ask, so a
      // bulk import is opt-in, never the default.
      const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[]
      expect(boxes.every((b) => !b.checked)).toBe(true)
      const importBtn = screen.getByRole("button", { name: /import skills/i }) as HTMLButtonElement
      expect(importBtn.disabled).toBe(true)

      await act(async () => {
        fireEvent.click(screen.getByRole("checkbox", { name: /sprint planner/i }))
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /import 1 skill/i }))
      })
      expect(onImportGithub).toHaveBeenCalledWith({
        repo: "octocat/methods",
        ref: "trunk",
        path: "",
        paths: ["skills/sprint-planner"],
        // No folder named, so syncing is off and never offered: a whole
        // repository's markdown is not a skill library, and the server refuses
        // a synced root anyway.
        sync: false,
      })
    })

    it("reports the imported skills in the same panel an archive uses", async () => {
      const imported = {
        skills: [
          {
            id: "g1", slug: "sprint-planner", trigger: "/sprint-planner",
            name: "Sprint Planner", description: "Plans a sprint.",
            uploader_name: "Dana Whitfield", created_at: null, has_file: true,
            name_conflict: false, replaced: false,
          },
        ],
        skipped: [{ path: "skills/bare", name: "Bare", reason: "no description" }],
      }
      const { onClose } = await openGithub({
        onImportGithub: vi.fn().mockResolvedValue(imported),
      })
      await waitFor(() => expect(reposMock).toHaveBeenCalled())
      await search()
      await act(async () => {
        fireEvent.click(screen.getByRole("checkbox", { name: /sprint planner/i }))
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /import 1 skill/i }))
      })

      expect(screen.getByRole("dialog", { name: /skills imported/i })).toBeTruthy()
      expect(screen.getByRole("status").textContent).toMatch(/1 skill added\./)
      expect(screen.getByText(/Bare — no description/)).toBeTruthy()
      expect(onClose).not.toHaveBeenCalled()
    })

    it("keeps the search inputs and shows the reason when discovery fails", async () => {
      await openGithub({
        onDiscoverGithub: vi
          .fn()
          .mockRejectedValue(new Error("That repository isn't connected to Sprntly.")),
      })
      await waitFor(() => expect(reposMock).toHaveBeenCalled())
      await search()
      expect(screen.getByRole("alert").textContent).toMatch(/isn't connected/)
      expect((screen.getByLabelText(/branch/i) as HTMLInputElement).value).toBe("trunk")
    })

    it("leaves the file flow exactly where it was", async () => {
      const onUpload = vi.fn().mockResolvedValue(undefined)
      const { container } = render(
        React.createElement(UploadSkillModal, {
          open: true,
          onUpload,
          onClose: vi.fn(),
          onDiscoverGithub: vi.fn(),
          onImportGithub: vi.fn(),
        }),
      )
      // The file source is the default, and nothing about it moved.
      await act(async () => {
        pickFile(container, MD_FILE())
      })
      await act(async () => {
        fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Good" } })
        fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
          target: { value: "Desc" },
        })
      })
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /^upload skill$/i }))
      })
      await waitFor(() =>
        expect(onUpload).toHaveBeenCalledWith(expect.any(File), "Good", "Desc"),
      )
      // The connector lookups are lazy — the file path never asked GitHub
      // anything.
      expect(connectorsListMock).not.toHaveBeenCalled()
    })
  })

  it("resets and closes on success", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    const { container } = render(
      React.createElement(UploadSkillModal, { open: true, onUpload, onClose }),
    )
    await act(async () => {
      pickFile(container, MD_FILE())
    })
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/skill name/i), { target: { value: "Good" } })
      fireEvent.change(screen.getByLabelText(/what does this skill do/i), {
        target: { value: "Desc" },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload skill/i }))
    })

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(expect.any(File), "Good", "Desc"))
    expect(onClose).toHaveBeenCalled()
  })
})
