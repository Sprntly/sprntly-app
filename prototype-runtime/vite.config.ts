import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import anchorId from "./vite-plugin-anchor-id";

export default defineConfig({
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
