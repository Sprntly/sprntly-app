import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    include: ["app/**/__tests__/**/*.test.ts", "app/**/__tests__/**/*.test.tsx"],
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
    // Above the 5s asyncUtilTimeout the setup file configures, so a waitFor that
    // genuinely never settles fails with its own assertion error instead of a
    // bare "test timed out" that says nothing about what was expected.
    testTimeout: 20_000,
  },
})
