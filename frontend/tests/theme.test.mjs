import { test } from "node:test";
import assert from "node:assert/strict";
import { themeColors, contrast, applyTheme } from "../src/theme.ts";

const sources = [
  "#1b5e20",
  "#397863",
  "#ff0000",
  "#0000ff",
  "#ffff00",
  "#808080",
  "#ffffff",
  "#000000",
  "#00ff00",
  "#ff0066",
  "#00ffff",
];
const colors = [
  "bg",
  "bg-subtle",
  "surface",
  "surface-2",
  "border",
  "border-strong",
  "text",
  "text-muted",
  "text-subtle",
  "accent",
  "accent-hover",
  "accent-active",
  "accent-text",
  "accent-subtle",
  "on-accent",
  "success",
  "warning",
  "danger",
  "info",
];
// Independently compute WCAG contrast from emitted, quantized hex values.
function ratio(a, b) {
  const lum = (hex) =>
    [1, 3, 5]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((x) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
      .reduce((sum, x, i) => sum + x * [0.2126, 0.7152, 0.0722][i], 0);
  return (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
}

test("source accent survives while independent light and dark full palettes are derived", () => {
  for (const source of sources) {
    const light = themeColors(source, false),
      dark = themeColors(source, true);
    for (const theme of [light, dark]) {
      assert.equal(theme.source, source);
      for (const name of colors)
        assert.match(theme.tokens[name], /^#[0-9a-f]{6}$/i, name);
      assert.equal(theme.fill, theme.tokens.accent);
      assert.equal(theme.on, theme.tokens["on-accent"]);
      assert.equal(theme.text, theme.tokens["accent-text"]);
      assert.equal(theme.soft, theme.tokens["accent-subtle"]);
      assert.ok(
        theme.tokens.shadow1 && theme.tokens.shadow2 && theme.tokens.ring,
      );
    }
    assert.notEqual(light.fill, dark.fill);
    assert.notEqual(light.tokens.bg, dark.tokens.bg);
  }
});

test("quantized text and all button states pass AA on their actual surfaces", () => {
  for (const source of sources)
    for (const dark of [false, true]) {
      const t = themeColors(source, dark).tokens;
      for (const bg of [
        "bg",
        "bg-subtle",
        "surface",
        "surface-2",
        "accent-subtle",
      ]) {
        for (const fg of ["text", "text-muted", "accent-text"]) {
          assert.ok(
            ratio(t[fg], t[bg]) >= 4.5,
            `${source} ${dark} ${fg}/${bg}: ${ratio(t[fg], t[bg])}`,
          );
        }
        for (const status of ["success", "warning", "danger", "info"]) {
          assert.ok(
            ratio(t[`${status}-text`], t[bg]) >= 4.5,
            `${source} ${dark} ${status}/${bg}`,
          );
          assert.ok(
            ratio(t[`${status}-text`], t[`${status}-subtle`]) >= 4.5,
            `${status} badge`,
          );
        }
      }
      for (const state of ["accent", "accent-hover", "accent-active"])
        assert.ok(
          ratio(t["on-accent"], t[state]) >= 4.5,
          `${source} ${dark} ${state}`,
        );
    }
});

test("status palettes retain fixed meaning across unrelated source hues", () => {
  for (const dark of [false, true])
    for (const status of ["success", "warning", "danger", "info"])
      assert.equal(
        themeColors("#ff0000", dark).tokens[status],
        themeColors("#0000ff", dark).tokens[status],
      );
});

test("applying a theme writes semantic tokens and compatible aliases", () => {
  const style = new Map();
  globalThis.document = {
    documentElement: {
      dataset: {},
      style: { setProperty: (k, v) => style.set(k, v) },
    },
  };
  applyTheme("#397863", true);
  assert.equal(document.documentElement.dataset.theme, "dark");
  assert.equal(style.get("--primary"), style.get("--accent"));
  assert.equal(style.get("--accent-fill"), style.get("--accent"));
  assert.equal(style.get("--muted"), style.get("--text-muted"));
  assert.equal(style.get("--accent-on"), style.get("--on-accent"));
  for (const token of colors) assert.ok(style.has(`--${token}`));
  delete globalThis.document;
});

test("invalid hex is rejected and contrast agrees with known WCAG endpoints", () => {
  for (const invalid of [
    "url(javascript:alert(1))",
    "#gggggg",
    "#fff",
    "#ffffff00",
  ])
    assert.throws(() => themeColors(invalid, false));
  assert.equal(contrast("#000000", "#ffffff"), 21);
});

test("blocking boot restores only current validated palette colors before app mount", async () => {
  const { readFileSync, existsSync } = await import("node:fs");
  const { runInNewContext } = await import("node:vm");
  const path = new URL("../public/theme-boot.js", import.meta.url);
  const script = existsSync(path) ? readFileSync(path, "utf8") : "";
  const run = (saved) => {
    const values = new Map(),
      root = {
        dataset: {},
        style: { setProperty: (k, v) => values.set(k, v) },
      };
    runInNewContext(script, {
      location: { search: "" },
      URLSearchParams,
      document: { documentElement: root },
      localStorage: { getItem: () => JSON.stringify(saved) },
      matchMedia: () => ({ matches: false }),
    });
    return { root, values };
  };
  const cached = {
    version: 1,
    accent: "#397863",
    theme: "dark",
    tokens: {
      bg: "#07120e",
      text: "#eaf2ef",
      overlay: "url(evil)",
      untrusted: "#ffffff",
    },
  };
  const { root, values } = run(cached);
  assert.equal(root.dataset.theme, "dark");
  assert.equal(values.get("--bg"), "#07120e");
  assert.equal(values.get("background-color"), "var(--bg)");
  assert.equal(values.get("--text"), "#eaf2ef");
  assert.equal(values.has("--overlay"), false);
  assert.equal(values.has("--untrusted"), false);
  for (const invalid of [
    { ...cached, version: 0 },
    { ...cached, accent: "url(evil)" },
    { ...cached, theme: "evil" },
  ]) {
    const { root, values } = run(invalid);
    assert.equal(root.dataset.theme, "light");
    assert.equal(values.get("--bg"), "#effaf5");
  }
});

test("native appearance query overrides stale cache before canvas and main mount", async () => {
  const { readFileSync } = await import("node:fs");
  const { runInNewContext } = await import("node:vm");
  const boot = readFileSync(
    new URL("../public/theme-boot.js", import.meta.url),
    "utf8",
  );
  const main = readFileSync(new URL("../src/main.tsx", import.meta.url), "utf8")
    .split("// CSS keeps")[0]
    .replace(/^import .*;\r?$/gm, "");
  for (const [search, cacheMode, wantMode, wantColor] of [
    ["?desktop=1&appearance=dark&accent=%23ff0066", "light", "dark", "#ff0066"],
    [
      "?desktop=1&appearance=light&accent=%230000ff",
      "dark",
      "light",
      "#0000ff",
    ],
    ["?desktop=0&appearance=light&accent=%230000ff", "dark", "dark", "#397863"],
    ["?desktop=1&appearance=evil&accent=%230000ff", "dark", "dark", "#397863"],
  ]) {
    const values = new Map(),
      calls = [];
    const root = {
      dataset: {},
      style: { setProperty: (k, v) => values.set(k, v) },
    };
    const context = {
      document: { documentElement: root },
      location: { search },
      URLSearchParams,
      localStorage: {
        getItem: () =>
          JSON.stringify({
            version: 1,
            accent: "#397863",
            theme: cacheMode,
            tokens: { bg: cacheMode === "dark" ? "#07120e" : "#effaf5" },
          }),
      },
      matchMedia: () => ({ matches: false }),
      applyTheme: (...args) => calls.push(args),
    };
    runInNewContext(boot, context);
    assert.equal(root.dataset.theme, wantMode, search);
    assert.equal(
      values.get("--bg"),
      wantMode === "dark" ? "#07120e" : "#effaf5",
    );
    runInNewContext(main, context);
    assert.deepEqual(calls, [[wantColor, wantMode === "dark"]], search);
  }
});
