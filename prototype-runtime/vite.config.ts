import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import anchorId from "./vite-plugin-anchor-id";

export default defineConfig({
  plugins: [
    anchorId(),
    react(),
  ],
  build: { outDir: "dist", emptyOutDir: true },
});
