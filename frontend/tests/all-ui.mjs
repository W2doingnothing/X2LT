// Run both isolated browser suites in CI; each closes its own browser/server.
await import("./ui.mjs");
await import("./collections-ui.mjs");
