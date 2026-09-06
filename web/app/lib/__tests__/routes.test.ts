import { describe, expect, it } from "vitest"
import { existsSync } from "node:fs"
import { join } from "node:path"
import {
  pathForScreen,
  screenIdFromPathname,
  prototypePath,
  PROTOTYPE_PATH,
  projectPath,
  PROJECTS_PATH,
} from "../routes"

describe("routes — standalone connectors removed (commit A)", () => {
  it("does not map any ScreenId to the /connectors path", () => {
    // pathForScreen previously returned "/connectors" for screen "connectors".
    // After commit A there is no route entry, so the lookup should return
    // undefined (or a fallback) rather than the deleted /connectors URL.
    // Cast through `as never` so the test compiles even after the ScreenId
    // union narrows; the runtime check is what we care about.
    expect(pathForScreen("connectors" as never)).not.toBe("/connectors")
  })

  it("does not resolve /connectors to any active screen", () => {
    // PATH_TO_SCREEN previously mapped "/connectors" → "connectors".
    // After commit A it should fall through to the default ("chat").
    expect(screenIdFromPathname("/connectors")).toBe("chat")
  })
})

describe("routes — Artifacts is its own left-nav surface", () => {
  it("maps the artifacts screen to /artifacts", () => {
    expect(pathForScreen("artifacts")).toBe("/artifacts")
  })

  it("resolves /artifacts back to the artifacts screen", () => {
    expect(screenIdFromPathname("/artifacts")).toBe("artifacts")
  })

  it("keeps History (/history) as its own chats-only screen", () => {
    expect(pathForScreen("chats")).toBe("/history")
    expect(screenIdFromPathname("/history")).toBe("chats")
  })
})

describe("routes — prototypePath generate-intent option", () => {
  it("appends &generate=1 after the prd param when generate intent is set", () => {
    expect(prototypePath(42, { generate: true })).toBe("/prototype?prd=42&generate=1")
  })

  it("appends ?generate=1 on the bare path when there is no prd", () => {
    expect(prototypePath(undefined, { generate: true })).toBe("/prototype?generate=1")
    expect(prototypePath(null, { generate: true })).toBe("/prototype?generate=1")
  })

  it("does NOT append generate when the option is absent or false (default callers)", () => {
    expect(prototypePath(42)).toBe("/prototype?prd=42")
    expect(prototypePath(42, {})).toBe("/prototype?prd=42")
    expect(prototypePath(42, { generate: false })).toBe("/prototype?prd=42")
    expect(prototypePath()).toBe(PROTOTYPE_PATH)
    expect(prototypePath(null, { generate: false })).toBe(PROTOTYPE_PATH)
  })
})

describe("routes — projectPath chat-param (fork-to-private-chat nav)", () => {
  it("test_projectPath_chat_param — appends &chat= when a project id AND opts.chat are both present", () => {
    expect(projectPath(7, { chat: "individual" })).toBe("/projects?id=7&chat=individual")
    expect(projectPath(7, { chat: "group" })).toBe("/projects?id=7&chat=group")
    expect(projectPath("7", { chat: "individual" })).toBe("/projects?id=7&chat=individual")
  })

  it("test_projectPath_byte_identical_no_opts — projectPath(id)/projectPath()/projectPath(null,{chat}) are unchanged from the base single-arg form", () => {
    expect(projectPath(7)).toBe("/projects?id=7")
    expect(projectPath(7, {})).toBe("/projects?id=7")
    expect(projectPath()).toBe(PROJECTS_PATH)
    expect(projectPath(null)).toBe(PROJECTS_PATH)
    // With no id, chat is ignored — there is no detail view to select a tab on.
    expect(projectPath(null, { chat: "group" })).toBe(PROJECTS_PATH)
    expect(projectPath(undefined, { chat: "individual" })).toBe(PROJECTS_PATH)
    expect(projectPath("")).toBe(PROJECTS_PATH)
  })
})

describe("routes — projectPath prd-param (seamless PRD-create landing, D1)", () => {
  it("test_projectPath_prd_param — appends &prd= after &chat= when a project id, opts.chat, AND opts.prd are all present", () => {
    expect(projectPath(555, { chat: "individual", prd: 501 })).toBe("/projects?id=555&chat=individual&prd=501")
    expect(projectPath("555", { chat: "individual", prd: "501" })).toBe("/projects?id=555&chat=individual&prd=501")
  })

  it("test_projectPath_prd_param_no_chat — opts.prd with no opts.chat still appends &prd= right after &id=", () => {
    expect(projectPath(555, { prd: 501 })).toBe("/projects?id=555&prd=501")
  })

  it("test_projectPath_prd_param_ignored_without_id — with no project id, opts.prd is ignored (same as opts.chat)", () => {
    expect(projectPath(null, { prd: 501 })).toBe(PROJECTS_PATH)
    expect(projectPath(undefined, { prd: 501 })).toBe(PROJECTS_PATH)
  })

  it("test_projectPath_byte_identical_no_prd_opt — omitting opts.prd (or opts entirely) is unchanged from the existing chat-only/no-opts forms", () => {
    expect(projectPath(7, { chat: "individual" })).toBe("/projects?id=7&chat=individual")
    expect(projectPath(7, {})).toBe("/projects?id=7")
    expect(projectPath(7)).toBe("/projects?id=7")
  })
})

describe("connectors route file (commit A)", () => {
  it("does not exist on disk", () => {
    const file = join(
      process.cwd(),
      "app",
      "(app)",
      "connectors",
      "page.tsx",
    )
    expect(existsSync(file)).toBe(false)
  })
})
