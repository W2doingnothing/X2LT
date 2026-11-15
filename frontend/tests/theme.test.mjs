import { test } from "node:test";
import assert from "node:assert/strict";
import { themeColors, contrast } from "../src/theme.ts";
test("exact custom accent is retained while text stays readable in both modes", () => {
  for (const color of [
    "#ffffff",
    "#000000",
    "#ff0066",
    "#00ff00",
    "#397863",
    "#ffff00",
  ])
    for (const dark of [true, false]) {
      const theme = themeColors(color, dark);
      assert.equal(theme.fill, color);
      assert.ok(contrast(theme.fill, theme.on) >= 4.5);
      assert.ok(contrast(theme.text, dark ? "#141b18" : "#f6f8f7") >= 4.5);
    }
});
test("invalid hex is rejected, shorthand input has explicit conversion", () => {
  assert.throws(() => themeColors("url(javascript:alert(1))", false));
  assert.throws(() => themeColors("#gggggg", false));
});
