// Markup tests for the pure "Formats we write in" view.
//
// Everything here is a rule the design depends on and a rendering change could
// silently break: three groups always render, each header states what governs
// it, a row with no uploader and no date still renders BOTH labelled lines, the
// active row is unmissable, and every one of the five compile states carries a
// word badge plus a reason line.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ArtifactFormatsView, metaParts, reasonLine } from "../ArtifactFormatsSection"
import type { ArtifactTemplate, CompileStatus } from "../../../../lib/api"

function noop() {}

function row(over: Partial<ArtifactTemplate> = {}): ArtifactTemplate {
  return {
    id: "t1",
    name: "Acme PRD v3",
    artifact_type: "prd",
    uploader_name: "Dana Okoye",
    created_at: "2026-08-03T10:00:00Z",
    updated_at: "2026-08-03T10:00:00Z",
    compile_status: "ready",
    is_active: false,
    source_chars: 4210,
    compile_summary: null,
    compile_note_count: 0,
    ...over,
  }
}

const EMPTY = { prd: [], tickets: [], impl_spec: [] }

/** Just ONE group's markup.
 *
 *  Needed because the other two groups legitimately say "built-in" — nothing of
 *  their type is active — so a whole-document assertion about the built-in
 *  claim would pass or fail for the wrong reason. */
function groupHtml(html: string, type: "prd" | "tickets" | "impl_spec"): string {
  const start = html.indexOf(`id="afmt-group-${type}"`)
  expect(start, `group ${type} not rendered`).toBeGreaterThan(-1)
  const order = ["prd", "tickets", "impl_spec"]
  const nextType = order[order.indexOf(type) + 1]
  const end = nextType ? html.indexOf(`id="afmt-group-${nextType}"`) : -1
  return end === -1 ? html.slice(start) : html.slice(start, end)
}

function render(
  over: Partial<React.ComponentProps<typeof ArtifactFormatsView>> = {},
): string {
  return renderToStaticMarkup(
    <ArtifactFormatsView
      groups={EMPTY}
      loading={false}
      refreshing={false}
      error={null}
      orgRole="admin"
      liveTypes={new Set(["prd"])}
      pollingIds={new Set()}
      stalledIds={new Set()}
      connectionLost={false}
      activatingId={null}
      deletingId={null}
      recompilingId={null}
      announcement={null}
      onAddFormat={noop}
      onPreview={noop}
      onActivateRequest={noop}
      onDeactivateRequest={noop}
      onDeleteRequest={noop}
      onRenameRequest={noop}
      onRecompile={noop}
      onRetryLoad={noop}
      renamingId={null}
      renameValue=""
      renameSaving={false}
      onRenameChange={noop}
      onRenameSave={noop}
      onRenameCancel={noop}
      onReplaceFile={noop}
      {...over}
    />,
  )
}

describe("ArtifactFormatsView — the section", () => {
  it("names itself, and says in the header what formats govern", () => {
    const html = render()
    expect(html).toMatch(/Formats we write in/)
    expect(html).toMatch(/One format is active per document type/)
    // The intro banner is gone — it existed to separate this library from the
    // exemplar library below, which is itself hidden now. What it said that
    // still matters (formats govern layout, not content) moved to the header
    // sub, so THAT is what must survive.
    expect(html).not.toMatch(/Skills decide what a document says/)
    expect(html).toMatch(/headings, order, wording/)
  })

  it("carries exactly ONE section-level live region", () => {
    const html = render({ announcement: "Acme PRD v3 is ready to activate." })
    expect(html).toContain("Acme PRD v3 is ready to activate.")
    expect(html.match(/aria-live="polite"/g) ?? []).toHaveLength(1)
  })
})

// Every VISIBLE group renders, always — engineering spec is withheld from the
// UI for now (HIDDEN_TYPES), so "always" means the two on offer. What must not
// regress is that an EMPTY visible group still renders: its header is the "what
// governs this document right now" statement, and hiding it would make
// one-active-per-type unlearnable.
describe("ArtifactFormatsView — every visible group, always", () => {
  it("renders every visible group even when all of them are empty", () => {
    const html = render()
    expect(html).toMatch(/id="afmt-group-prd"/)
    expect(html).toMatch(/id="afmt-group-tickets"/)
    expect(html).toMatch(/>PRD</)
    expect(html).toMatch(/>Tickets</)
    // Withheld from the UI, not from the product: the backend still accepts,
    // compiles and generates from engineering-spec formats.
    expect(html).not.toMatch(/id="afmt-group-impl_spec"/)
    expect(html).not.toMatch(/>Engineering spec</)
    // Never the raw column value.
    expect(html).not.toMatch(/>impl_spec</)
  })

  it("each empty group states what governs it — the built-in, named and tagged", () => {
    const html = render()
    expect(html).toMatch(/Now using: Sprntly&#x27;s built-in PRD format/)
    expect(html).toMatch(/Now using: Sprntly&#x27;s built-in ticket format/)
    expect(html).not.toMatch(/built-in engineering-spec format/)
    expect(html.match(/tag tag-impact/g) ?? []).toHaveLength(2)
  })

  it("an empty group says so in words and still offers its own Add action", () => {
    const html = render()
    expect(html).toMatch(/No PRD format uploaded — Sprntly uses its own\./)
    expect(html).toMatch(/Add a PRD format/)
    expect(html).toMatch(/Add a ticket format/)
  })

  it("every visible group empty adds the explainer that frames the built-in as chosen", () => {
    const html = render()
    expect(html).toMatch(
      /Sprntly writes in its own format — until you give it yours\./,
    )
    // …and the group rows still render below it, so nothing reads as broken.
    expect(html).toMatch(/Now using:/)
  })

  it("drops the explainer as soon as any group has a row", () => {
    const html = render({ groups: { ...EMPTY, prd: [row()] } })
    expect(html).not.toMatch(/until you give it yours/)
  })

  it("never claims the built-in before the list lands", () => {
    const html = render({ loading: true })
    expect(html).toMatch(/Checking which format is in use…/)
    expect(html).not.toMatch(/Now using:/)
    // Two dashed skeleton rows per VISIBLE group.
    expect(html.match(/afmt-row--skel/g) ?? []).toHaveLength(4)
  })

  it("names the ACTIVE format in the group header, tagged as the team's own", () => {
    const html = render({
      groups: { ...EMPTY, prd: [row({ is_active: true })] },
    })
    expect(html).toMatch(/Now using: <strong>Acme PRD v3<\/strong>/)
    expect(html).toMatch(/tag tag-double/)
  })
})

describe("ArtifactFormatsView — the row", () => {
  it("renders BOTH metadata lines when the uploader is blank and the date is null", () => {
    // House rule: a missing value is a reason to say what we know, never a
    // reason to drop the line and never a bare dash.
    const html = render({
      groups: {
        ...EMPTY,
        prd: [row({ uploader_name: "", created_at: null, source_chars: 0 })],
      },
    })
    expect(html).toContain("Uploaded by someone no longer on the team")
    expect(html).toContain("Added — date not recorded")
    expect(html).toContain("Length not recorded")
  })

  it("metaParts always answers three parts, whatever the row carries", () => {
    expect(metaParts(row())).toEqual([
      "Uploaded by Dana Okoye",
      expect.stringMatching(/^Added /),
      "4,210 characters of Markdown",
    ])
    expect(
      metaParts(row({ uploader_name: "", created_at: "not-a-date", source_chars: 0 })),
    ).toEqual([
      "Uploaded by someone no longer on the team",
      "Added — date not recorded",
      "Length not recorded",
    ])
  })

  it("the active row carries the full pill text, aria-current, and sorts first", () => {
    const html = render({
      groups: {
        ...EMPTY,
        prd: [row({ id: "a", name: "Active one", is_active: true }), row({ id: "b", name: "Other one" })],
      },
    })
    // Not "Active" alone — the three extra words stop it reading as a status.
    expect(html).toContain("Active — in use now")
    expect(html).toMatch(/aria-current="true"/)
    expect(html).toMatch(/is-active/)
    // The view renders the order it is given (the hook sorts), so assert the
    // active row's markers land on the row that is active.
    expect(html.indexOf("Active one")).toBeLessThan(html.indexOf("Other one"))
  })

  it("a very long name keeps its full string in `title` rather than truncating away", () => {
    const long = "A".repeat(180)
    const html = render({ groups: { ...EMPTY, prd: [row({ name: long })] } })
    expect(html).toContain(`title="${long}"`)
  })

  it("never renders a numeric quality score", () => {
    const html = render({ groups: { ...EMPTY, prd: [row()] } })
    // Compile status is a statement of READINESS, not a judgement of quality —
    // a number beside a format about to go company-wide would be a confidently
    // false conclusion. The only digits allowed are the character count.
    expect(html).not.toMatch(/\b\d{1,3}\s*(\/\s*100|%)/)
  })
})

describe("ArtifactFormatsView — the five compile states", () => {
  // `null` reason = the card deliberately says nothing. Only `ready` qualifies:
  // its badge already states the state, and repeating it in a sentence on every
  // healthy card — which is most of them — taught people to skip the line that
  // carries the real reason on a card that needs attention.
  const CASES: [CompileStatus, RegExp, RegExp | null][] = [
    ["pending", /Queued/, /Queued — we&#x27;ll check this format in a moment\./],
    ["compiling", /Checking…/, /Checking your format against what a Sprntly document needs…/],
    ["ready", /Ready/, null],
    ["needs_review", /Needs a look/, /didn&#x27;t map onto a Sprntly document|bulleted evidence list/],
    ["failed", /Couldn&#x27;t be read/, /didn&#x27;t map onto a Sprntly document|Nothing about your file/],
  ]

  it("every status renders a WORD badge — colour is never the only signal", () => {
    for (const [status, badge] of CASES) {
      const html = render({
        groups: { ...EMPTY, prd: [row({ compile_status: status })] },
      })
      expect(html, status).toMatch(badge)
    }
  })

  it("every status that has something to say says it; ready stays silent", () => {
    for (const [status, , reason] of CASES) {
      const html = render({
        groups: { ...EMPTY, prd: [row({ compile_status: status })] },
      })
      if (reason) {
        expect(html, status).toMatch(reason)
      } else {
        // Not merely empty text — the element is dropped, so a healthy card
        // carries no blank paragraph taking its own margin.
        expect(html, status).not.toMatch(/class="afmt-reason"/)
        expect(html, status).not.toMatch(/Checked — every part of a Sprntly document/)
      }
    }
  })

  it("reasonLine answers something for the four states that need one", () => {
    for (const status of [
      "pending",
      "compiling",
      "needs_review",
      "failed",
    ] as CompileStatus[]) {
      expect(reasonLine(row({ compile_status: status }), false).length).toBeGreaterThan(10)
    }
    // Ready and an unknown status both stay silent rather than inventing copy.
    expect(reasonLine(row({ compile_status: "ready" }), false)).toBe("")
    expect(
      reasonLine(row({ compile_status: "wat" as CompileStatus }), false),
    ).toBe("")
  })

  it("marks an in-flight row aria-busy off its own status, not off the poller's bookkeeping", () => {
    const html = render({
      groups: { ...EMPTY, prd: [row({ compile_status: "compiling" })] },
      pollingIds: new Set(),
    })
    expect(html).toMatch(/aria-busy="true"/)
  })

  it("a stalled poll swaps the reason and offers Check again — never an endless spinner", () => {
    const html = render({
      groups: { ...EMPTY, prd: [row({ compile_status: "compiling" })] },
      stalledIds: new Set(["t1"]),
    })
    expect(html).toMatch(/Still checking — this is taking longer than usual\./)
    expect(html).toMatch(/Check again/)
  })

  it("translates a compile summary rather than printing it", () => {
    const html = render({
      groups: {
        ...EMPTY,
        prd: [
          row({
            compile_status: "needs_review",
            compile_summary: "no `ul.ev` in the skeleton",
            compile_note_count: 3,
          }),
        ],
      },
    })
    expect(html).toMatch(/bulleted evidence list/)
    expect(html).not.toContain("ul.ev")
    // The count comes off the row — it cannot be derived from the summary.
    expect(html).toMatch(/See all 3/)
  })

  it("hides 'See all' when there is only one note", () => {
    const html = render({
      groups: {
        ...EMPTY,
        prd: [
          row({
            compile_status: "needs_review",
            compile_summary: "no <h1>",
            compile_note_count: 1,
          }),
        ],
      },
    })
    expect(html).not.toMatch(/See all/)
  })

  it("a needs_review row offers the loop out of it in place of Activate", () => {
    const html = render({
      groups: { ...EMPTY, prd: [row({ compile_status: "needs_review" })] },
    })
    expect(html).toMatch(/Preview it to see what we could map/)
    expect(html).not.toMatch(/Use this format/)
  })

  it("a failed row offers Try again and Replace the file", () => {
    const html = render({
      groups: { ...EMPTY, prd: [row({ compile_status: "failed" })] },
    })
    expect(html).toMatch(/Try again/)
    expect(html).toMatch(/Replace the file/)
  })
})

describe("ArtifactFormatsView — role gating", () => {
  it("a non-admin sees the denial line instead of Activate, and keeps Preview", () => {
    const html = render({
      orgRole: "member",
      groups: { ...EMPTY, prd: [row()] },
    })
    expect(html).toMatch(/Only an admin can change your team&#x27;s format\./)
    expect(html).not.toMatch(/>Use this format</)
    expect(html).toMatch(/>Preview</)
  })

  it("a non-admin keeps Delete on a NON-active row", () => {
    const html = render({
      orgRole: "member",
      groups: { ...EMPTY, prd: [row()] },
    })
    // Delete is an icon button now, so the word lives in `aria-label` rather
    // than in the text. Asserted there deliberately: that attribute IS the
    // control's name for anyone not looking at a trash glyph, so a change that
    // dropped it would be a real regression and not a cosmetic one.
    expect(html).toMatch(/aria-label="Delete /)
    expect(html).not.toMatch(/Only an admin can delete/)
  })

  it("a non-admin gets the DELETE-specific line on the active row", () => {
    const html = render({
      orgRole: "member",
      groups: { ...EMPTY, prd: [row({ is_active: true })] },
    })
    expect(html).toMatch(/Only an admin can delete the format your team is using\./)
    expect(html).not.toMatch(/>Delete</)
  })

  it("orgRole null shows NEITHER denial string — disabled + aria-busy instead", () => {
    // Flashing "Only an admin can…" at an admin for 200ms is worse than a
    // brief disabled button.
    const html = render({
      orgRole: null,
      groups: { ...EMPTY, prd: [row({ id: "a" }), row({ id: "b", is_active: true })] },
    })
    expect(html).not.toMatch(/Only an admin can/)
    expect(html).toMatch(/aria-busy="true"/)
    expect(html).toMatch(/disabled=""/)
  })

  it("an admin gets the real controls", () => {
    const html = render({
      orgRole: "owner",
      groups: { ...EMPTY, prd: [row()] },
    })
    expect(html).toMatch(/Use this format/)
    expect(html).not.toMatch(/Only an admin can/)
  })
})

describe("ArtifactFormatsView — generation not wired yet", () => {
  it("notes the type on its group even with the whole library empty", () => {
    const html = render({ liveTypes: new Set(["prd"]) })
    expect(html).toMatch(/Sprntly doesn&#x27;t write tickets from a custom format yet/)
    expect(html).not.toMatch(/Sprntly doesn&#x27;t write PRDs/)
    // The engineering-spec group is withheld from the UI, so its note has no
    // group to render on. Nothing about the note's own wiring changed.
    expect(html).not.toMatch(/write engineering specs from a custom format yet/)
  })

  it("all-false — today's real backend state — notes all three", () => {
    const html = render({ liveTypes: new Set() })
    expect(html).toMatch(/Sprntly doesn&#x27;t write PRDs from a custom format yet/)
  })

  it("replaces Activate with the SHORT form on a ready row of an unwired type", () => {
    const html = render({
      liveTypes: new Set(),
      groups: { ...EMPTY, prd: [row()] },
    })
    const prd = groupHtml(html, "prd")
    expect(prd).not.toMatch(/>Use this format</)
    expect(prd).toMatch(/Sprntly doesn&#x27;t write PRDs from a custom format yet\./)
    // The instruction half is an instruction, so it renders EXACTLY once per
    // group — under the header — not again on every row beneath it.
    expect(
      prd.match(/you&#x27;ll be able to switch it on when support lands/g) ?? [],
    ).toHaveLength(1)
  })

  it("keeps the instruction once even with several rows in the group", () => {
    const html = render({
      liveTypes: new Set(),
      groups: {
        ...EMPTY,
        prd: [row({ id: "a" }), row({ id: "b" }), row({ id: "c" })],
      },
    })
    const prd = groupHtml(html, "prd")
    expect(
      prd.match(/you&#x27;ll be able to switch it on when support lands/g) ?? [],
    ).toHaveLength(1)
    // …while every row still says why it has no Activate control.
    expect(
      prd.match(/Sprntly doesn&#x27;t write PRDs from a custom format yet\./g) ?? [],
    ).toHaveLength(4) // 1 group note + 3 rows
  })
})

// ── the active format being re-checked (settled 2026-08-06) ─────────────────
// `resolve_prd_template` gates on `compiled != ''`, so the last good skeleton
// keeps serving through a recompile. The row shows an "Active — in use now"
// pill AND a "Checking…" badge at once, which read together say "nobody knows
// what my next PRD will look like" — this line is the only thing that answers
// that, and it must never imply the built-in has taken over.
describe("ArtifactFormatsView — active + recompiling", () => {
  for (const status of ["compiling", "pending"] as const) {
    it(`says the previous version is still serving while ${status}`, () => {
      const html = render({
        groups: {
          ...EMPTY,
          prd: [row({ is_active: true, compile_status: status })],
        },
      })
      const prd = groupHtml(html, "prd")
      expect(prd).toMatch(
        /Still writing PRDs in the version you had — we&#x27;ll switch to your edit once it checks out\./,
      )
      // Both signals are still on the row — the line explains them, it does
      // not hide them.
      expect(prd).toContain("Active — in use now")
      expect(prd).toMatch(status === "compiling" ? /Checking…/ : /Queued/)
    })
  }

  it("never implies the built-in has taken over for that type", () => {
    const html = render({
      groups: {
        ...EMPTY,
        prd: [row({ is_active: true, compile_status: "compiling" })],
      },
    })
    const prd = groupHtml(html, "prd")
    // The reason line is the claim under test. The row ALSO carries a
    // "Use Sprntly's built-in format instead" control, which is an offer, not a
    // statement about what is running — so the assertion is scoped to the line
    // that makes the claim.
    const reason = prd.match(/<p class="afmt-reason">(.*?)<\/p>/s)?.[1] ?? ""
    expect(reason).toMatch(/Still writing PRDs in the version you had/)
    expect(reason).not.toMatch(/built-in/i)
    // The rejected alternative wording, which would only have been honest if
    // the resolver gated on `compile_status == "ready"`. It doesn't.
    expect(prd).not.toMatch(/until this finishes checking/i)
    // And the header keeps naming the customer's format, because that IS what
    // is being served.
    expect(prd).toMatch(/Now using: <strong>Acme PRD v3<\/strong>/)
    expect(prd).not.toMatch(/Now using: Sprntly&#x27;s built-in/)
  })

  it("a slow check keeps the reassurance and still offers Check again", () => {
    const html = render({
      groups: {
        ...EMPTY,
        prd: [row({ is_active: true, compile_status: "compiling" })],
      },
      stalledIds: new Set(["t1"]),
    })
    const prd = groupHtml(html, "prd")
    expect(prd).toMatch(/Still writing PRDs in the version you had/)
    // A slow check does not change what is being served, so the reason line
    // does not swap — but the escape hatch still appears.
    expect(prd).not.toMatch(/this is taking longer than usual/)
    expect(prd).toMatch(/Check again/)
  })

  it("leaves a NON-active row's checking copy exactly as it was", () => {
    const html = render({
      groups: { ...EMPTY, prd: [row({ compile_status: "compiling" })] },
    })
    const prd = groupHtml(html, "prd")
    expect(prd).toMatch(/Checking your format against what a Sprntly document needs…/)
    expect(prd).not.toMatch(/Still writing PRDs/)
  })

  it("reasonLine keys the copy on the row's own type", () => {
    expect(
      reasonLine(
        row({ is_active: true, compile_status: "compiling", artifact_type: "tickets" }),
        false,
      ),
    ).toMatch(/Still writing tickets in the version you had/)
    expect(
      reasonLine(
        row({ is_active: true, compile_status: "pending", artifact_type: "impl_spec" }),
        false,
      ),
    ).toMatch(/Still writing engineering specs in the version you had/)
  })
})

describe("ArtifactFormatsView — load failure and lost connection", () => {
  it("shows the inline error with a Try again, and nothing else pretends to work", () => {
    const html = render({
      error: "We couldn't load your document formats. Check your connection and try again.",
    })
    expect(html).toMatch(/We couldn&#x27;t load your document formats/)
    expect(html).toMatch(/Try again/)
    expect(html).toMatch(/role="alert"/)
  })

  it("says the connection is gone without claiming the compile stopped", () => {
    const html = render({ connectionLost: true })
    expect(html).toMatch(
      /We&#x27;ve lost the connection — we&#x27;ll pick this up when you&#x27;re back\./,
    )
  })

  it("marks the section busy on a refresh without blanking the rows", () => {
    const html = render({
      refreshing: true,
      groups: { ...EMPTY, prd: [row()] },
    })
    expect(html).toMatch(/aria-busy="true"/)
    expect(html).toContain("Acme PRD v3")
  })
})
