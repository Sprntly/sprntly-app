import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

// ---------------------------------------------------------------------------
// P4-09 — prod-portable prototype asset paths (Vite `base: "./"`).
//
// The bug: with no `base` set, Vite defaults to `/`, so a built prototype's
// `dist/index.html` references its bundle with ABSOLUTE paths
// (`<script src="/assets/index-*.js">`). In prod the dist is served through a
// path-prefixed Supabase signed URL, and the browser resolves `/assets/...`
// against the storage ORIGIN ROOT — dropping the `/storage/.../prototypes/...`
// prefix → 404 → blank viewer. `base: "./"` emits RELATIVE paths that resolve
// from the document's own location regardless of serving root.
//
// Why this test runs a real Vite build (and not the transform-only
// `buildFixture` helper that anchor-id-stability.test.ts uses): `base` only
// affects the EMITTED `dist/index.html` asset references, which exist solely
// after a full Rollup pipeline. The transform-only helper never produces a
// `dist/index.html`, so it cannot observe `base` at all. We invoke Vite's
// programmatic `build()` with `configFile` pointed at the REAL root
// `vite.config.ts` — so this test goes red the moment `base: "./"` is removed
// from the file under fix, not merely if `base` is wrong in some inline copy.
// The lightweight `fixture-app` is used as the build entry (same fixture the
// stability test builds) to keep the build fast.
// ---------------------------------------------------------------------------

const HERE = dirname(fileURLToPath(import.meta.url));
const PROTOTYPE_RUNTIME_ROOT = join(HERE, "..");
const ROOT_VITE_CONFIG = join(PROTOTYPE_RUNTIME_ROOT, "vite.config.ts");
const FIXTURE_DIR = join(HERE, "fixture-app");
const FIXTURE_INDEX_HTML = join(FIXTURE_DIR, "index.html");

let outDir = "";
let indexHtml = "";
let bundledJs = "";

beforeAll(async () => {
  outDir = await mkdtemp(join(tmpdir(), "p4-09-relpaths-"));
  await build({
    // Load the actual file under fix so `base: "./"` is read from it; a
    // regression that drops `base` flips this whole suite red.
    configFile: ROOT_VITE_CONFIG,
    // Build the lightweight fixture-app entry rather than the full runtime src.
    root: FIXTURE_DIR,
    logLevel: "silent",
    build: {
      outDir,
      emptyOutDir: true,
      rollupOptions: { input: FIXTURE_INDEX_HTML },
    },
  });

  indexHtml = await readFile(join(outDir, "index.html"), "utf8");

  // Concatenate every emitted JS chunk so the anchor-id non-regression check
  // observes a REAL build (stronger than the transform-only stability test).
  const assetsDir = join(outDir, "assets");
  const assetFiles = await readdir(assetsDir);
  const jsParts = await Promise.all(
    assetFiles
      .filter((f) => f.endsWith(".js"))
      .map((f) => readFile(join(assetsDir, f), "utf8")),
  );
  bundledJs = jsParts.join("\n");
}, 120_000);

afterAll(async () => {
  if (outDir) await rm(outDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Regression — would have caught the bug (AC2). Fails on the `base`-less
// config (absolute `/assets/`), passes after `base: "./"`.
// ---------------------------------------------------------------------------

describe("base: \"./\" — relative asset paths (AC2)", () => {
  it("built index.html references its JS with a relative ./assets path", () => {
    // Sanity: the build actually emitted a hashed JS bundle reference.
    expect(indexHtml).toMatch(/<script[^>]+src="[^"]*assets\/[^"]+\.js"/);
    // The script ref is relative (`./assets/...` or `assets/...`), not absolute.
    expect(indexHtml).toMatch(/<script[^>]+src="\.?\/?assets\/[^"]+\.js"/);
  });

  it("built index.html contains NO absolute /assets reference", () => {
    // The exact failure mode of the bug: an absolute `/assets/` src or href.
    expect(indexHtml).not.toMatch(/(?:src|href)="\/assets\//);
  });

  it("every asset reference in index.html is relative", () => {
    // Belt-and-suspenders: scan all src=/href= attributes pointing at assets
    // and confirm none begins with a leading slash.
    const refs = [...indexHtml.matchAll(/(?:src|href)="([^"]*assets\/[^"]+)"/g)]
      .map((m) => m[1]);
    expect(refs.length).toBeGreaterThan(0);
    for (const ref of refs) {
      expect(ref.startsWith("/"), `absolute asset ref: ${ref}`).toBe(false);
    }
  });
});

// ---------------------------------------------------------------------------
// Non-regression — `base` does not disturb the anchor-id plugin (AC4).
// (anchor-id-stability.test.ts is the primary AC4 cover; this confirms the
// literal survives a REAL build under the fixed config.)
// ---------------------------------------------------------------------------

describe("base: \"./\" — anchor-id plugin unaffected (AC4)", () => {
  it("built bundle still carries data-anchor-id literals", () => {
    expect(bundledJs).toContain("data-anchor-id");
  });
});

// ---------------------------------------------------------------------------
// Inline pre-paint background — the template `index.html` carries a neutral
// `html { background-color: #f6f7f6 }` in an inline <head> style so a served
// prototype never paints stark white before the bundle's own CSS applies. This
// piggybacks the real Vite build above to prove the emitted `dist/index.html`
// PRESERVES the inline head style (asserted against a real build, not memory).
// ---------------------------------------------------------------------------

describe("inline pre-paint background survives the build", () => {
  it("emitted index.html keeps the inline pre-paint background", () => {
    expect(indexHtml).toContain("background-color: #f6f7f6");
  });

  it("runtime index.html source carries the inline pre-paint background", async () => {
    // Guards the ROOT template independently of the fixture copy the build
    // consumes — a regression in either flips one of these two tests red.
    const runtimeIndexHtml = await readFile(
      join(PROTOTYPE_RUNTIME_ROOT, "index.html"),
      "utf8",
    );
    expect(runtimeIndexHtml).toContain("background-color: #f6f7f6");
  });
});
