// The OTHER HALF of the drop-code contract.
//
// `backend/tests/test_routes_crucible.py::test_every_engine_drop_code_has_panel_copy`
// checks the same invariant from the backend side — but `test-backend.yml`'s
// path filter is `backend/**`, so a WEB-ONLY change never runs it, and the web
// file is the side that breaks this contract. A plausible tidy-up that drops
// `single_account` from `DROP_ORDER` would merge green: the row still renders,
// appended by the unknown-code tail at the END of the funnel instead of in rule
// order, which is the exact defect an earlier review pass raised.
//
// So the contract is pinned from both directions and neither lane can miss it.
// Same reasoning `test-web.yml` records for the `:::block` template contract,
// solved there with a path filter; solved here with a mirror test, because a
// workflow change is Apurva-gated per CONVENTIONS and this needs neither.
import { readFileSync, existsSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

const PIPELINE = join(
  process.cwd(), "..", "backend", "app", "crucible", "pipeline.py",
)
const PANEL = join(
  process.cwd(), "app", "components", "shared", "GoalRunNarration.tsx",
)

describe("every engine drop code has panel copy", () => {
  it("mirrors NARRATED_DROPS from the engine", () => {
    // Fail as a CONTRACT break, not a stack trace: a moved file must read as
    // "update this test", never as a mysterious throw someone deletes.
    expect(existsSync(PIPELINE), `engine constants missing at ${PIPELINE}`)
      .toBe(true)

    const py = readFileSync(PIPELINE, "utf8")
    const block = py.match(/NARRATED_DROPS\s*=\s*\(([\s\S]*?)\)/)
    expect(block, "NARRATED_DROPS is gone from pipeline.py — update this test")
      .toBeTruthy()

    const codes = [...block![1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    expect(codes.length, "parsed no drop codes — the constant's shape changed")
      .toBeGreaterThan(0)

    const panel = readFileSync(PANEL, "utf8")
    const copy = panel.slice(
      panel.indexOf("const DROP_COPY"), panel.indexOf("const DROP_ORDER"),
    )
    const order = panel.slice(
      panel.indexOf("const DROP_ORDER"), panel.indexOf("const n ="),
    )

    for (const code of codes) {
      // ANCHORED AS A KEY. Merely finding the word also matches it inside a
      // comment, so commenting an entry out would leave this green while the
      // panel rendered the raw code.
      expect(
        new RegExp(`^\\s*${code}:`, "m").test(copy),
        `${code} has no panel copy key — the funnel would render a raw code`,
      ).toBe(true)
      expect(
        new RegExp(`"${code}"`).test(order),
        `${code} is missing from DROP_ORDER — it would render out of funnel order`,
      ).toBe(true)
    }
  })
})
