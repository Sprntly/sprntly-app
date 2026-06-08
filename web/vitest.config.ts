import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    include: ["app/**/__tests__/**/*.test.ts", "app/**/__tests__/**/*.test.tsx"],
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
  },
})
