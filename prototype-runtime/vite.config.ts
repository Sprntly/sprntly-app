import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import anchorId from "./vite-plugin-anchor-id";

export default defineConfig({
  // prod-portable: relative asset paths resolve under path-prefixed signed-URL
  // serving (Supabase Storage). Absolute "/assets/..." would 404 against the
  // storage origin root. Build-time-only; no template-version bump (P4-09).
  base: "./",
  plugins: [
    anchorId(),
    react(),
  ],
  resolve: {
    alias: {
      // shadcn/ui convention: `@/` resolves to the prototype's `src/`.
      // Both the vendored components (src/components/ui/*, src/lib/utils.ts)
      // and the agent's own `@/...` imports resolve through this.
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
