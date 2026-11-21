// Run both isolated browser suites in CI; each closes its own browser/server.
await import("./ui.mjs");
await import("./collections-ui.mjs");
await import("./collections-geometry.mjs");
await import("./ui071.mjs");
await import("./ui072.mjs");
await import("./ui073.mjs");
await import("./ui074.mjs");
await import("./ui075.mjs");
await import("./ui076.mjs");
await import("./ui078.mjs");
