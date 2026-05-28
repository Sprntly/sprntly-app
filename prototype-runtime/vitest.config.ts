import { basename, dirname, join } from "node:path";
import { defineConfig } from "vitest/config";

// AC #7 (P0-04) requires the snapshot file at
// `test/__snapshots__/anchor-id-stability.snap` — without the default
// `.test.ts` infix Vitest would otherwise insert. The resolver drops the
// `.test.ts` segment but keeps the rest of Vitest's path layout intact.
export default defineConfig({
  test: {
    resolveSnapshotPath: (testPath, snapExtension) => {
      const base = basename(testPath).replace(/\.test\.tsx?$/, "");
      return join(dirname(testPath), "__snapshots__", base + snapExtension);
    },
  },
});
