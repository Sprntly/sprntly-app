import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    include: ["app/**/__tests__/**/*.test.ts", "app/**/__tests__/**/*.test.tsx"],
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
    // Run each test file in its own isolated fork — the same file-level
    // isolation CI relies on. Pinning min/max explicitly avoids the
    // machine-dependent tinypool "minThreads and maxThreads must not
    // conflict" abort that the bare default pool resolution trips on here,
    // so `vitest run` works unqualified. NOTE: do NOT use `singleFork` —
    // it shares one worker across every file and leaks module-level mocks
    // between suites (over-reporting failures that pass in isolation).
    pool: "forks",
    poolOptions: {
      forks: { minForks: 1, maxForks: 4, isolate: true },
    },
    // Above the 5s asyncUtilTimeout the setup file configures, so a waitFor that
    // genuinely never settles fails with its own assertion error instead of a
    // bare "test timed out" that says nothing about what was expected.
    testTimeout: 20_000,
  },
})
