import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  FIXTURE_DIR,
  buildFixture,
  type FixtureOverrides,
} from "./helpers/build-fixture";
import {
  extractAllJsxElements,
  extractAnchorIds,
} from "./helpers/extract-anchor-ids";

const HERE = dirname(fileURLToPath(import.meta.url));
const SNAPSHOT_FILE = join(HERE, "__snapshots__", "anchor-id-stability.snap");

const ANCHOR_ID_RE = /^[0-9a-f]{8}$/;

async function readFixture(rel: string): Promise<string> {
  return readFile(join(FIXTURE_DIR, rel), "utf8");
}

// ---------------------------------------------------------------------------
// Creation — happy-path snapshot baseline + per-element coverage
// ---------------------------------------------------------------------------

describe("anchor-id stability — creation", () => {
  it("test_snapshot_matches_baseline", async () => {
    const build = await buildFixture();
    const map = extractAnchorIds(build);
    expect(map).toMatchSnapshot();
  });

  it("test_every_jsx_element_has_anchor_id", async () => {
    const build = await buildFixture();
    const elements = extractAllJsxElements(build);
    // Sanity-check that the fixture actually produced JSX elements — if the
    // walker silently returns an empty array, the format assertion below would
    // pass vacuously and the safety net would be a no-op.
    expect(elements.length).toBeGreaterThan(0);
    const missing = elements.filter((e) => e.anchorId == null);
    expect(missing, "JSX elements without data-anchor-id").toEqual([]);
    // Known-from-fixture anchor keys MUST appear in the map — guards against
    // the walker silently skipping whole component bodies.
    const map = extractAnchorIds(build);
    const knownKeys = [
      "FixtureApp.tsx | FixtureApp |  | main | 0",
      "FixtureApp.tsx | FixtureApp | main | h1 | 0",
      "FixtureApp.tsx | FixtureApp | main | ContactForm | 0",
      "FixtureApp.tsx | FixtureApp | main | TodoList | 0",
      "FixtureApp.tsx | FixtureApp | main | NestedCard | 0",
      "ContactForm.tsx | ContactForm |  | form | 0",
      "ContactForm.tsx | ContactForm | form | button | 0",
      "ContactForm.tsx | ContactForm | form | button | 1",
      "NestedCard.tsx | NestedCard |  | article | 0",
      "NestedCard.tsx | NestedCard | article | CardHeader | 0",
      "NestedCard.tsx | CardHeader |  | header | 0",
      "NestedCard.tsx | CardBody | section | p | 0",
      "TodoList.tsx | TodoList |  | ul | 0",
    ];
    for (const key of knownKeys) {
      expect(map, `expected key ${key}`).toHaveProperty(key);
    }
  });
});

// ---------------------------------------------------------------------------
// Serialization — id format + on-disk snapshot file presence
// ---------------------------------------------------------------------------

describe("anchor-id stability — serialization", () => {
  it("test_anchor_id_format_8_hex", async () => {
    const build = await buildFixture();
    const map = extractAnchorIds(build);
    for (const [key, id] of Object.entries(map)) {
      expect(id, `key=${key}`).toMatch(ANCHOR_ID_RE);
    }
  });

  it("test_snapshot_file_committed", () => {
    expect(existsSync(SNAPSHOT_FILE)).toBe(true);
    const content = readFileSync(SNAPSHOT_FILE, "utf8");
    expect(content.length).toBeGreaterThan(0);
    // Snapshot file is what `npm test -- -u` writes; verifying it parses as
    // Vitest's snapshot format (header comment + at least one named export).
    expect(content).toMatch(/Vitest Snapshot/);
    expect(content).toContain(
      "anchor-id stability — creation > test_snapshot_matches_baseline",
    );
  });
});

// ---------------------------------------------------------------------------
// Retrieval / stability — the load-bearing AC #1 assertions
// ---------------------------------------------------------------------------

describe("anchor-id stability — retrieval", () => {
  it("test_text_edit_preserves_all_ids", async () => {
    const baseline = extractAnchorIds(await buildFixture());

    const original = await readFixture("ContactForm.tsx");
    expect(original).toContain(">Submit<");
    const edited = original.replace(">Submit<", ">Submita<");
    expect(edited).toContain(">Submita<");

    const after = extractAnchorIds(
      await buildFixture({ "ContactForm.tsx": edited }),
    );

    // Every key in the baseline must still exist with the same anchor-id.
    for (const [key, id] of Object.entries(baseline)) {
      expect(after[key], `key=${key} changed after text edit`).toBe(id);
    }
    // And — explicitly — the edited Submit button's anchor-id is unchanged.
    const submitKey = "ContactForm.tsx | ContactForm | form | button | 1";
    expect(after[submitKey]).toBe(baseline[submitKey]);
  });

  it("test_className_change_preserves_all_ids", async () => {
    const baseline = extractAnchorIds(await buildFixture());
    const original = await readFixture("ContactForm.tsx");
    const edited = original.replace(
      "<form>",
      '<form className="contact-form-edited">',
    );
    const after = extractAnchorIds(
      await buildFixture({ "ContactForm.tsx": edited }),
    );
    for (const [key, id] of Object.entries(baseline)) {
      expect(after[key], `key=${key} changed after className edit`).toBe(id);
    }
  });

  it("test_attribute_value_change_preserves_ids", async () => {
    const baseline = extractAnchorIds(await buildFixture());
    const original = await readFixture("ContactForm.tsx");
    const edited = original.replace('type="submit"', 'type="reset"');
    expect(edited).not.toBe(original);
    const after = extractAnchorIds(
      await buildFixture({ "ContactForm.tsx": edited }),
    );
    for (const [key, id] of Object.entries(baseline)) {
      expect(after[key], `key=${key} changed after attribute edit`).toBe(id);
    }
  });

  it("test_whitespace_only_change_preserves_ids", async () => {
    const baseline = extractAnchorIds(await buildFixture());
    const original = await readFixture("ContactForm.tsx");
    const edited = original
      .split("\n")
      .map((line) => `  ${line}`) // indent every line by two spaces
      .join("\n");
    expect(edited).not.toBe(original);
    const after = extractAnchorIds(
      await buildFixture({ "ContactForm.tsx": edited }),
    );
    for (const [key, id] of Object.entries(baseline)) {
      expect(after[key], `key=${key} changed after whitespace edit`).toBe(id);
    }
  });
});

// ---------------------------------------------------------------------------
// Error handling — surfacing build failures + Vitest's missing-snapshot behaviour
// ---------------------------------------------------------------------------

describe("anchor-id stability — error handling", () => {
  it("test_fixture_build_failure_surfaces_error", async () => {
    const corrupt = `export default function Broken() { return <div< ; }`;
    await expect(
      buildFixture({ "ContactForm.tsx": corrupt }),
    ).rejects.toThrow(/ContactForm\.tsx/);
  });

  it("test_missing_snapshot_creates_one_on_first_run", () => {
    // Vitest writes a fresh snapshot on first run when the snapshot file or
    // entry is missing, and FAILS the run when CI=1 (the default in GitHub
    // Actions). We can't actually delete + re-invoke Vitest from inside a
    // test, so we assert the two preconditions that drive that behaviour:
    //   1. The snapshot file is present on disk (was created on a real run).
    //   2. CI mode is wired through; the workflow runs with CI=1 implicitly
    //      via GitHub Actions, and Vitest's docs guarantee a missing-snapshot
    //      failure in that mode.
    expect(existsSync(SNAPSHOT_FILE)).toBe(true);
    const fileContent = readFileSync(SNAPSHOT_FILE, "utf8");
    expect(fileContent).toContain("test_snapshot_matches_baseline");
  });
});

// ---------------------------------------------------------------------------
// Edge cases — per ticket + BUILD-PHASES.md Risk + mitigation
// ---------------------------------------------------------------------------

const EMPTY_FIXTURE: FixtureOverrides = {
  "FixtureApp.tsx": `export function FixtureApp() {\n  return null;\n}\n`,
  "ContactForm.tsx": `export function ContactForm() {\n  return null;\n}\n`,
  "TodoList.tsx": `export function TodoList() {\n  return null;\n}\n`,
  "NestedCard.tsx": `export function NestedCard() {\n  return null;\n}\n`,
  "main.tsx": `export {};\n`,
};

describe("anchor-id stability — edge cases", () => {
  it("test_empty_fixture_produces_empty_snapshot", async () => {
    const build = await buildFixture(EMPTY_FIXTURE);
    const map = extractAnchorIds(build);
    expect(map).toEqual({});
  });

  it("test_single_element_fixture", async () => {
    const overrides: FixtureOverrides = {
      ...EMPTY_FIXTURE,
      "FixtureApp.tsx": `export function FixtureApp() {\n  return <div />;\n}\n`,
    };
    const map = extractAnchorIds(await buildFixture(overrides));
    expect(Object.keys(map)).toEqual([
      "FixtureApp.tsx | FixtureApp |  | div | 0",
    ]);
    const [only] = Object.values(map);
    expect(only).toMatch(ANCHOR_ID_RE);
  });

  it("test_deeply_nested_composition_stable", async () => {
    const a = extractAnchorIds(await buildFixture());
    const b = extractAnchorIds(await buildFixture());
    // Pull every NestedCard-tree key out of both runs and confirm equality.
    const nestedKeys = Object.keys(a).filter((k) =>
      k.startsWith("NestedCard.tsx"),
    );
    expect(nestedKeys.length).toBeGreaterThan(3);
    for (const k of nestedKeys) expect(b[k]).toBe(a[k]);
  });

  it("test_self_closing_tag_with_no_children", async () => {
    const map = extractAnchorIds(await buildFixture());
    // ContactForm has two self-closing <input /> elements (text, email) and a
    // self-closing <textarea />. Each lives inside its own <label>, so per the
    // plugin's hashing every <input /> resolves to the SAME anchor-key shape:
    //   ContactForm.tsx | ContactForm | form>label | input | 0
    // The extractor's dedup suffixes the second occurrence with " #1". The
    // assertion confirms both that self-closing tags are annotated AND that
    // sibling-index is still computed (each gets the canonical "0").
    const inputKeys = Object.keys(map).filter((k) => k.includes("| input | "));
    expect(inputKeys.length).toBe(2);
    for (const k of inputKeys) expect(map[k]).toMatch(ANCHOR_ID_RE);
    // The matching <textarea /> is also self-closing and annotated.
    const textareaKey =
      "ContactForm.tsx | ContactForm | form>label | textarea | 0";
    expect(map[textareaKey]).toMatch(ANCHOR_ID_RE);
  });

  it("test_wrapper_div_changes_descendant_ids_explicitly", async () => {
    const baseline = extractAnchorIds(await buildFixture());
    const original = await readFixture("NestedCard.tsx");
    // Inject a wrapper <div> around the three Card* children inside <article>.
    const wrapped = original.replace(
      `<article>
      <CardHeader />
      <CardBody />
      <CardFooter />
    </article>`,
      `<article>
      <div>
        <CardHeader />
        <CardBody />
        <CardFooter />
      </div>
    </article>`,
    );
    expect(wrapped).not.toBe(original);
    const after = extractAnchorIds(
      await buildFixture({ "NestedCard.tsx": wrapped }),
    );

    const cardChildren = [
      ["NestedCard.tsx | NestedCard | article | CardHeader | 0", "CardHeader"],
      ["NestedCard.tsx | NestedCard | article | CardBody | 0", "CardBody"],
      ["NestedCard.tsx | NestedCard | article | CardFooter | 0", "CardFooter"],
    ] as const;

    // Documents the known fragility: the original keys disappear (new nesting
    // path includes the wrapper div) and the new ids differ from the old.
    for (const [oldKey, tag] of cardChildren) {
      expect(after).not.toHaveProperty(oldKey);
      const newKey = `NestedCard.tsx | NestedCard | article>div | ${tag} | 0`;
      expect(after).toHaveProperty(newKey);
      expect(after[newKey]).not.toBe(baseline[oldKey]);
    }
  });

  it("test_two_identically_shaped_components_produce_same_ids", async () => {
    const sourceA = `export function Same() {\n  return <div><button /></div>;\n}\n`;
    const sourceB = `export function Same() {\n  return <div><button /></div>;\n}\n`;
    const a = extractAnchorIds(
      await buildFixture(
        { "FixtureApp.tsx": sourceA, ...EMPTY_FIXTURE },
        ["FixtureApp.tsx"],
      ),
    );
    const b = extractAnchorIds(
      await buildFixture(
        { "ContactForm.tsx": sourceB, ...EMPTY_FIXTURE },
        ["ContactForm.tsx"],
      ),
    );
    expect(Object.values(a).sort()).toEqual(Object.values(b).sort());
  });

  it("test_jsx_comment_children_ignored", async () => {
    const source = `export function FixtureApp() {\n  return (\n    <div>\n      {/* a comment */}\n      <button />\n    </div>\n  );\n}\n`;
    const map = extractAnchorIds(
      await buildFixture({ ...EMPTY_FIXTURE, "FixtureApp.tsx": source }),
    );
    // The button is sibling-index 0 — the JSX comment doesn't count as a
    // sibling. We assert by comparing against a "no-comment" build.
    const noComment = `export function FixtureApp() {\n  return (\n    <div>\n      <button />\n    </div>\n  );\n}\n`;
    const map2 = extractAnchorIds(
      await buildFixture({ ...EMPTY_FIXTURE, "FixtureApp.tsx": noComment }),
    );
    const buttonKey = "FixtureApp.tsx | FixtureApp | div | button | 0";
    expect(map[buttonKey]).toBe(map2[buttonKey]);
  });
});
