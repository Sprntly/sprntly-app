import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";
import anchorIdPlugin from "../../vite-plugin-anchor-id";

const HERE = dirname(fileURLToPath(import.meta.url));
export const FIXTURE_DIR = join(HERE, "..", "fixture-app");

export const FIXTURE_FILES = [
  "main.tsx",
  "FixtureApp.tsx",
  "ContactForm.tsx",
  "TodoList.tsx",
  "NestedCard.tsx",
] as const;

export type FixtureFileName = (typeof FIXTURE_FILES)[number] | string;
export type FixtureOverrides = Record<string, string>;

export interface FixtureBuild {
  files: Record<string, string>;
}

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
    return hook as unknown as TransformFn;
  }
  if (hook && typeof hook === "object" && "handler" in hook) {
    return (hook as { handler: unknown }).handler as TransformFn;
  }
  throw new Error("anchor-id plugin.transform is not callable");
}

// Runs the anchor-id Vite plugin against every fixture file (or an override
// for that filename) and returns the transformed module sources. The full
// Vite build is exercised separately in the workflow's "Build fixture" step;
// this helper exists so the Vitest test suite can run hermetically without
// touching disk or spinning up a Rollup pipeline per assertion.
export async function buildFixture(
  overrides: FixtureOverrides = {},
  files: readonly string[] = FIXTURE_FILES,
): Promise<FixtureBuild> {
  const plugin = anchorIdPlugin();
  const transform = getTransformFn(plugin);
  const out: Record<string, string> = {};
  for (const rel of files) {
    const id = join(FIXTURE_DIR, rel);
    const src =
      rel in overrides
        ? overrides[rel]
        : await readFile(id, "utf8");
    const result = await transform(src, id);
    if (result == null) {
      out[rel] = src;
      continue;
    }
    if (typeof result === "string") {
      out[rel] = result;
      continue;
    }
    out[rel] = (result as { code: string }).code;
  }
  return { files: out };
}
