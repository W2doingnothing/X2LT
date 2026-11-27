import assert from "node:assert/strict";
import { chromium } from "playwright";
import { createServer } from "vite";
import { fileURLToPath } from "node:url";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  server: { host: "127.0.0.1", port: 0 },
});
await server.listen();
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1350, height: 900 } });
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const library = { id: "main", name: "混合测试库", path: "F:\\Fixture", default: true, online: true, quota_bytes: 0 };
    const local = { id: 1, title: "本地合成图片", kind: "image", bytes: 100, tags: ["混合"],
      libraries: ["main"], locations: [{ id: 1, state: "missing", managed: false, label: "fixture.png" }] };
    const telegram = { ...local, id: 2, title: "电报合成图片", body: "电报合成正文",
      telegram: { content_id: 2, sent_at: 1767240000, channel: "样本频道", has_url: true, forward: null },
      telegram_items: [{ ...local, id: 2 }],
    };
    const task = { id: "123456", author: "样本作者", display_name: "样本作者", title: "", text: "混合推文",
      ignored: false, tags: ["混合"], handle: "", platform: "x", updated: 1767250000,
      media: [{ id: 3, media_id: "1", media_type: "image", extension: "png", bytes: 100,
        state: "completed", width: 900, height: 600, error: "", output: library.path }],
    };
    const rows = [{ type: "local", entry: telegram }, { type: "x", task }, { type: "local", entry: local },
      ...Array.from({ length: 22 }, (_, index) => ({
        type: "local",
        entry: { ...local, id: index + 4, title: `额外文件 ${index}`, tags: [] },
      })),
    ];
    const snapshot = { version: "fixture", settings: { theme: "light", accent: "#397863", privacy_mode: false },
      library_cards: true, has_credentials: false, credential_error: "", libraries: [library],
      default_library: "main", busy: false, tasks: [], total: 0, offset: 0,
      tag_options: [{ name: "混合", count: 3 }], counts: { all: 0, pending: 0, completed: 0, ignored: 0 },
    };
    const ok = (data) => ({ ok: true, data });
    window.cardRequests = [];
    window.pywebview = { api: { async call(action, p = {}) {
      window.cardRequests.push({ action, p });
      if (action === "snapshot" || action === "bootstrap") return ok(snapshot);
      if (action === "poll") return ok({ events: [], busy: false });
      if (action === "library_cards") {
        const matches = rows.filter((row) => {
          const item = row.entry ?? row.task;
          return (!p.query || [item.title, item.text, item.body].some((text) => text?.includes(p.query))) &&
            (!p.tags.length || p.tags.every((tag) => item.tags.includes(tag))) &&
            (!p.untagged || !item.tags.length);
        });
        return ok({ items: matches.slice(p.offset, p.offset + p.limit), total: matches.length, offset: p.offset });
      }
      return ok(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/?desktop=1`);
  await page.locator(".sidebar").getByRole("button", { name: "媒体库", exact: true }).click();
  await page.getByRole("button", { name: "进入混合测试库" }).click();
  const grid = page.locator(".collection-content-list");
  const cards = grid.locator(":scope > .library-media-card");
  await cards.nth(23).waitFor();
  assert.equal(await grid.count(), 1);
  assert.equal(await page.getByRole("heading", { name: "本地导入", exact: true }).count(), 0);
  assert.deepEqual(await cards.evaluateAll((nodes) => nodes.slice(0, 3).map((n) => n.dataset.librarySource)),
    ["telegram", "x", "local"]);
  for (const [index, label] of ["Telegram", "X", "本地导入"].entries()) {
    await cards.nth(index).getByText(label, { exact: true }).waitFor();
  }
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await grid.getByText("额外文件 21", { exact: true }).waitFor();
  assert.equal(await cards.count(), 1);
  await page.getByRole("textbox", { name: "搜索内容" }).fill("合成");
  await grid.getByText("电报合成正文", { exact: true }).waitFor();
  assert.equal(await cards.count(), 2);
  const calls = await page.evaluate(() => window.cardRequests);
  assert.equal(calls.some(({ action }) => action === "local_library"), false);
  const requests = calls.filter(({ action }) => action === "library_cards").map(({ p }) => p);
  assert.deepEqual([...new Set(requests.filter(({ query }) => !query).map(({ offset }) => offset))], [0, 24]);
  assert.ok(requests.some(({ query, offset }) => query === "合成" && offset === 0));
  assert.deepEqual(errors, []);
  console.log("PASS mixed library keeps Telegram, X and local in one grid with shared pagination and search");
} finally {
  await browser?.close();
  await server.close();
}
