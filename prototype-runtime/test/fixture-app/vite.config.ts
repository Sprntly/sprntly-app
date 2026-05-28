import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import anchorId from "../../vite-plugin-anchor-id";

const fixtureDir = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: fixtureDir,
  plugins: [react(), anchorId()],
  build: {
    outDir: resolve(fixtureDir, "../../dist-fixture"),
    emptyOutDir: true,
    rollupOptions: { input: resolve(fixtureDir, "index.html") },
  },
});
