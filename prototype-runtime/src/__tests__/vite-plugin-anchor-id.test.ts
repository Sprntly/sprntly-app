import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import type { Plugin } from "vite";
import anchorIdPlugin, { computeAnchorId } from "../../vite-plugin-anchor-id";

type TransformResult =
  | { code: string; map?: unknown | null }
  | string
  | null
  | undefined;

type TransformFn = (
  code: string,
  id: string,
) => TransformResult | Promise<TransformResult>;

function getTransformFn(plugin: Plugin): TransformFn {
  const hook = plugin.transform;
  if (typeof hook === "function") {
    const fn = hook as unknown as TransformFn;
    return (code, id) => fn(code, id);
  }
  if (hook && typeof hook === "object" && "handler" in hook) {
    const fn = (hook as { handler: unknown }).handler as TransformFn;
    return (code, id) => fn(code, id);
  }
  throw new Error("plugin.transform is not callable");
}

function transform(
  source: string,
  id: string = "/fixture/file.tsx",
): { code: string; map: unknown | null } {
  const plugin = anchorIdPlugin();
  const transformFn = getTransformFn(plugin);
  const result = transformFn(source, id);
  if (result == null) return { code: source, map: null };
  if (typeof result === "string") return { code: result, map: null };
  if ("then" in (result as object)) {
    throw new Error("Sync transform expected; plugin returned a promise");
  }
  const tr = result as { code: string; map?: unknown | null };
  return { code: tr.code, map: tr.map ?? null };
}

function transformRaw(
  source: string,
  id: string = "/fixture/file.tsx",
): TransformResult {
  const plugin = anchorIdPlugin();
  const transformFn = getTransformFn(plugin);
  return transformFn(source, id) as TransformResult;
}

function anchorIdsInOrder(code: string): string[] {
  const matches = code.matchAll(/data-anchor-id="([0-9a-f]{8})"/g);
  return Array.from(matches, (m) => m[1]);
}

function sha1Hex8(input: string): string {
  return createHash("sha1").update(input).digest("hex").slice(0, 8);
}

describe("anchorIdPlugin — plugin shape", () => {
  it("test_plugin_returns_vite_plugin_object", () => {
    const plugin = anchorIdPlugin();
    expect(plugin.name).toBe("anchor-id");
    expect(plugin.enforce).toBe("pre");
    expect(plugin.transform).toBeDefined();
  });
});

describe("anchorIdPlugin — annotation", () => {
  it("test_annotates_simple_button", () => {
    const src = `export default function Component() {\n  return <button>Hi</button>;\n}\n`;
    const { code } = transform(src);
    expect(code).toMatch(/<button data-anchor-id="[0-9a-f]{8}">Hi<\/button>/);
  });

  it("test_annotates_nested_jsx", () => {
    const src = `export default function Component() {\n  return (\n    <div>\n      <span>\n        <button />\n      </span>\n    </div>\n  );\n}\n`;
    const { code } = transform(src);
    const ids = anchorIdsInOrder(code);
    expect(ids).toHaveLength(3);
    expect(new Set(ids).size).toBe(3);
    for (const id of ids) {
      expect(id).toMatch(/^[0-9a-f]{8}$/);
    }
  });
});

describe("anchorIdPlugin — hash serialization", () => {
  it("test_hash_format_8_hex_chars", () => {
    const src = `export default function C() {\n  return (\n    <div>\n      <button />\n      <button />\n      <span />\n    </div>\n  );\n}\n`;
    const { code } = transform(src);
    for (const id of anchorIdsInOrder(code)) {
      expect(id).toMatch(/^[0-9a-f]{8}$/);
    }
  });

  it("test_hash_inputs_concatenated_with_null_sep", () => {
    const expected = sha1Hex8("Component\x00div\x00button\x000");
    expect(computeAnchorId("Component", ["div"], "button", 0)).toBe(expected);
  });

  it("test_sourcemap_returned", () => {
    const src = `export default function C() {\n  return <button />;\n}\n`;
    const { map } = transform(src);
    expect(map).not.toBeNull();
    expect(map).toBeDefined();
  });
});

describe("anchorIdPlugin — determinism", () => {
  it("test_identical_trees_produce_identical_ids", () => {
    const src = `export default function Component() {\n  return (\n    <section>\n      <header><h1>Title</h1></header>\n      <div>\n        <button>Go</button>\n        <button>Stop</button>\n      </div>\n    </section>\n  );\n}\n`;
    const a = transform(src);
    const b = transform(src);
    expect(a.code).toBe(b.code);
  });

  it("test_identical_trees_across_processes", async () => {
    // Re-import the plugin module fresh — simulates a separate process/load.
    const src = `export default function Component() {\n  return (\n    <div><button>Hi</button></div>\n  );\n}\n`;
    const a = transform(src);
    const freshPath = `../../vite-plugin-anchor-id?freshLoad=${Date.now()}`;
    const freshModule = (await import(freshPath)) as {
      default: typeof anchorIdPlugin;
    };
    const plugin = freshModule.default();
    const transformFn = getTransformFn(plugin);
    const result = transformFn(src, "/fixture/file.tsx") as {
      code: string;
    };
    expect(result.code).toBe(a.code);
  });
});

describe("anchorIdPlugin — error handling", () => {
  it("test_unparseable_input_throws_with_file_id", () => {
    const src = `export default function Broken() { return <div< ; }`;
    expect(() =>
      transform(src, "/fixture/broken.tsx"),
    ).toThrow(/\/fixture\/broken\.tsx/);
  });

  it("test_non_jsx_file_passthrough", () => {
    const src = `export const value = 42;`;
    const result = transformRaw(src, "/fixture/file.ts");
    expect(result).toBeNull();
  });
});

describe("anchorIdPlugin — edge cases", () => {
  it("test_jsx_fragment_not_annotated", () => {
    const src = `export default function C() {\n  return <><span /></>;\n}\n`;
    const { code } = transform(src);
    // Fragment shorthand `<>` cannot carry attributes — the only annotated
    // element is the inner <span/>.
    expect(anchorIdsInOrder(code)).toHaveLength(1);
    expect(code).not.toMatch(/<>\s*data-anchor-id/);
  });

  it("test_existing_anchor_id_preserved", () => {
    const src = `export default function C() {\n  return <div data-anchor-id="aaaaaaaa" />;\n}\n`;
    const { code } = transform(src);
    expect(code).toMatch(/data-anchor-id="aaaaaaaa"/);
    expect(anchorIdsInOrder(code)).toEqual(["aaaaaaaa"]);
  });

  it("test_jsx_spread_attribute_does_not_block", () => {
    const src = `export default function C(props: Record<string, unknown>) {\n  return <div {...props} />;\n}\n`;
    const { code } = transform(src);
    expect(code).toMatch(/<div \{\.\.\.props\} data-anchor-id="[0-9a-f]{8}"/);
  });

  it("test_sibling_index_counts_same_type_only", () => {
    const src = `export default function C() {\n  return (\n    <div>\n      <button>A</button>\n      <span>X</span>\n      <button>B</button>\n    </div>\n  );\n}\n`;
    const { code } = transform(src);
    // Compute the expected anchors against the documented algorithm.
    const div = computeAnchorId("C", [], "div", 0);
    const buttonA = computeAnchorId("C", ["div"], "button", 0);
    const span = computeAnchorId("C", ["div"], "span", 0);
    const buttonB = computeAnchorId("C", ["div"], "button", 1);
    expect(anchorIdsInOrder(code)).toEqual([div, buttonA, span, buttonB]);
  });

  it("test_text_edit_preserves_ids", () => {
    const before = `export default function C() {\n  return <button>Submit</button>;\n}\n`;
    const after = `export default function C() {\n  return <button>Submita</button>;\n}\n`;
    expect(anchorIdsInOrder(transform(before).code)).toEqual(
      anchorIdsInOrder(transform(after).code),
    );
  });

  it("test_className_edit_preserves_ids", () => {
    const before = `export default function C() {\n  return <button className="foo">Hi</button>;\n}\n`;
    const after = `export default function C() {\n  return <button className="bar">Hi</button>;\n}\n`;
    expect(anchorIdsInOrder(transform(before).code)).toEqual(
      anchorIdsInOrder(transform(after).code),
    );
  });

  it("test_wrapper_div_changes_descendant_ids", () => {
    const before = `export default function C() {\n  return <button>Hi</button>;\n}\n`;
    const after = `export default function C() {\n  return <div><button>Hi</button></div>;\n}\n`;
    const beforeIds = anchorIdsInOrder(transform(before).code);
    const afterIds = anchorIdsInOrder(transform(after).code);
    const beforeButton = beforeIds[0];
    const afterButton = afterIds[1];
    expect(beforeButton).not.toBe(afterButton);
  });

  it("test_top_level_jsx_outside_component_uses_module_sentinel", () => {
    const src = `const x = <div />;\nexport { x };\n`;
    const { code } = transform(src);
    expect(anchorIdsInOrder(code)).toEqual([
      computeAnchorId("__module__", [], "div", 0),
    ]);
  });

  it("test_renamed_component_changes_descendant_ids", () => {
    const foo = `export default function Foo() {\n  return <button>Hi</button>;\n}\n`;
    const bar = `export default function Bar() {\n  return <button>Hi</button>;\n}\n`;
    const fooId = anchorIdsInOrder(transform(foo).code)[0];
    const barId = anchorIdsInOrder(transform(bar).code)[0];
    expect(fooId).not.toBe(barId);
  });

  it("test_idempotent_re_run_on_annotated_output_does_not_drift", () => {
    const src = `export default function C() {\n  return <button>Hi</button>;\n}\n`;
    const first = transform(src);
    const second = transform(first.code);
    expect(second.code).toBe(first.code);
  });

  it("test_only_tsx_and_jsx_extensions_are_transformed", () => {
    const src = `export const value = 42;`;
    expect(transformRaw(src, "/x/style.css")).toBeNull();
    expect(transformRaw(src, "/x/data.json")).toBeNull();
    expect(transformRaw(src, "/x/file.ts")).toBeNull();
  });
});
