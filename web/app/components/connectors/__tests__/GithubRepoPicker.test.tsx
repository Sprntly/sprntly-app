// View tests for the in-drawer GitHub repo picker.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { GithubRepoPickerView } from "../GithubRepoPicker"
import type {
  GitHubInstallation,
  GitHubInstallRepo,
} from "../../../lib/api"

function noop() {}

const INSTALL_SELECTED: GitHubInstallation = {
  installation_id: 12345,
  account_login: "octocat",
  account_type: "User",
  repository_selection: "selected",
}

const INSTALL_ALL: GitHubInstallation = {
  ...INSTALL_SELECTED,
  repository_selection: "all",
}

const REPOS: GitHubInstallRepo[] = [
  {
    id: 101,
    name: "widgets",
    full_name: "octocat/widgets",
    private: false,
    html_url: "https://github.com/octocat/widgets",
    default_branch: "main",
    description: "Widget factory",
  },
  {
    id: 102,
    name: "secret-thing",
    full_name: "octocat/secret-thing",
    private: true,
    html_url: "https://github.com/octocat/secret-thing",
    default_branch: "main",
    description: null,
  },
]

function render(
  override: Partial<React.ComponentProps<typeof GithubRepoPickerView>> = {},
): string {
  const defaults: React.ComponentProps<typeof GithubRepoPickerView> = {
    installation: INSTALL_SELECTED,
    repos: REPOS,
    loading: false,
    loadError: null,
    busyRepoIds: new Set(),
    toggleError: null,
    onToggleRepo: noop,
    githubInstallSettingsUrl:
      "https://github.com/settings/installations/12345",
  }
  return renderToStaticMarkup(
    React.createElement(GithubRepoPickerView, { ...defaults, ...override }),
  )
}

describe("GithubRepoPickerView — chrome", () => {
  it("shows the installation account + type in the header", () => {
    const html = render()
    expect(html).toContain("@octocat")
    expect(html).toContain("User")
  })

  it("shows loading state", () => {
    const html = render({ loading: true, repos: [] })
    expect(html).toContain("Loading repositories")
  })

  it("shows load error", () => {
    const html = render({ loadError: "HTTP 500", repos: [] })
    expect(html).toContain("HTTP 500")
  })

  it("shows toggle error", () => {
    const html = render({
      toggleError: "GitHub rejected the change",
    })
    expect(html).toContain("GitHub rejected the change")
  })
})

describe("GithubRepoPickerView — 'selected' mode (per-repo control)", () => {
  it("renders one checkbox per repo, all checked (since these are the install's selected repos)", () => {
    const html = render()
    const checks = html.match(/<input[^>]*type="checkbox"[^>]*>/g) || []
    expect(checks.length).toBe(REPOS.length)
    for (const c of checks) {
      expect(c).toContain("checked")
      expect(c).not.toMatch(/\bdisabled\b/)
    }
  })

  it("flags private repos with a 'private' badge", () => {
    const html = render()
    expect(html).toContain("secret-thing")
    expect(html).toContain("private")
  })

  it("renders the description when present", () => {
    const html = render()
    expect(html).toContain("Widget factory")
  })

  it("does NOT show the 'all repositories' notice", () => {
    const html = render()
    expect(html.toLowerCase()).not.toContain("all repositories")
  })

  it("includes a deep link to GitHub install settings", () => {
    const html = render()
    expect(html).toContain(
      "https://github.com/settings/installations/12345",
    )
  })

  it("disables the checkbox for a repo currently in flight", () => {
    const html = render({ busyRepoIds: new Set([101]) })
    // 101 disabled, 102 still enabled.
    const lines = html.split("</li>")
    const widgetsLine = lines.find((l) => l.includes("widgets")) || ""
    const secretLine = lines.find((l) => l.includes("secret-thing")) || ""
    expect(widgetsLine).toMatch(/\bdisabled\b/)
    expect(secretLine).not.toMatch(/<input[^>]*\bdisabled\b/)
  })
})

describe("GithubRepoPickerView — 'all' mode (per-repo control disallowed)", () => {
  it("shows the 'all repositories' notice", () => {
    const html = render({ installation: INSTALL_ALL })
    expect(html.toLowerCase()).toContain("all repositories")
    expect(html).toContain("switch") // copy includes "Switch it to..."
  })

  it("disables every checkbox", () => {
    const html = render({ installation: INSTALL_ALL })
    const checks = html.match(/<input[^>]*type="checkbox"[^>]*>/g) || []
    expect(checks.length).toBe(REPOS.length)
    for (const c of checks) {
      expect(c).toMatch(/\bdisabled\b/)
    }
  })

  it("still shows the deep link out to GitHub", () => {
    const html = render({ installation: INSTALL_ALL })
    expect(html).toContain(
      "https://github.com/settings/installations/12345",
    )
  })
})

describe("GithubRepoPickerView — empty state", () => {
  it("when 'selected' mode + no repos, surfaces a friendly empty-state with a link", () => {
    const html = render({ repos: [] })
    expect(html.toLowerCase()).toContain("no repositories")
    expect(html).toContain(
      "https://github.com/settings/installations/12345",
    )
  })
})
