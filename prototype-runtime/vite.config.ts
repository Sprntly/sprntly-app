import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// import anchorId from "./vite-plugin-anchor-id"; // wired in P0-02

export default defineConfig({
  plugins: [
    react(),
    // anchorId(), // wired in P0-02
  ],
  build: { outDir: "dist", emptyOutDir: true },
});
