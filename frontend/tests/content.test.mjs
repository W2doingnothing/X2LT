import { test } from "node:test";
import assert from "node:assert/strict";
import { contentPresentation } from "../src/contentPresentation.ts";
import { cycleIndex, wheelStep } from "../src/stackMotion.ts";
test("stack cycles both ways without counting quality versions", () => {
  assert.equal(cycleIndex(0, -1, 4), 3);
  assert.equal(cycleIndex(3, 1, 4), 0);
  assert.equal(cycleIndex(9, 0, 3), 0);
  assert.equal(cycleIndex(0, 1, 1), 0);
  assert.equal(cycleIndex(0, 1, 0), 0);
});
test("trackpad threshold, cooldown, gesture reset and delta units", () => {
  let s = { sum: 0, seen: 0, fired: -Infinity };
  let r = wheelStep(12, 0, 1000, s);
  assert.equal(r.delta, 0);
  s = r.state;
  r = wheelStep(28, 0, 1010, s);
  assert.equal(r.delta, 1);
  s = r.state;
  r = wheelStep(120, 0, 1050, s);
  assert.equal(r.delta, 0);
  s = r.state;
  r = wheelStep(-3, 1, 1250, s);
  assert.equal(r.delta, -1);
  r = wheelStep(1, 2, 2000, r.state);
  assert.equal(r.delta, 1);
  r = wheelStep(20, 0, 3000, r.state);
  r = wheelStep(20, 0, 3400, r.state);
  assert.equal(r.delta, 0);
});
const item = {
  id: "123",
  platform: "x",
  author: "Alice",
  handle: "alice",
  display_name: "Alice",
  text: "正文",
  title: "正文",
  media: [],
};
test("known social author versus authorless website/local presentations", () => {
  const x = contentPresentation(item);
  assert.equal(x.heading, "Alice");
  assert.equal(x.account, "alice");
  assert.equal(x.canProfile, true);
  assert.equal(x.label, "X");
  const site = contentPresentation({
    ...item,
    platform: "website",
    author: "",
    handle: "",
    display_name: "",
    source: {
      kind: "website",
      label: "Example",
      domain: "example.test",
      title: "文章标题",
    },
  });
  assert.equal(site.heading, "文章标题");
  assert.equal(site.account, "");
  assert.equal(site.subtitle, "example.test");
  assert.equal(site.canProfile, false);
  assert.equal(site.canSource, false);
  const local = contentPresentation({
    ...item,
    platform: "local",
    author: "",
    handle: "",
    display_name: "",
    title: "",
    text: "",
    media: [{ filename: "旅行.mp4" }],
  });
  assert.equal(local.heading, "旅行.mp4");
  assert.equal(local.label, "本地");
  assert.equal(local.canSource, false);
  assert.equal(local.canProfile, false);
  const telegram = contentPresentation({
    ...item,
    platform: "telegram",
    source: { kind: "message", label: "Telegram", title: "收藏频道" },
    display_name: "",
    author: "",
    handle: "",
  });
  assert.equal(telegram.heading, "收藏频道");
  assert.equal(telegram.account, "");
  assert.equal(telegram.canProfile, false);
});
