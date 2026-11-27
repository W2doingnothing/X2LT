import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  server: { host: "127.0.0.1", port: 0 },
});
await server.listen();
let browser;
try {
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(6000);
  await page.addInitScript(() => {
    const library = { id: "main", name: "迁移测试库", path: "G:\\Former", default: true, online: true, quota_bytes: 0 };
    const migration = {
      id: library.id, source: library.path, destination: "F:\\Target", status: "planned",
      file_count: 5, total_bytes: 1024, copied_files: 0, copied_bytes: 0, free_bytes: 2048,
    };
    let status = null;
    const ok = (data) => ({ ok: true, data });
    window.storageCalls = [];
    window.pywebview = { api: { async call(action, p = {}) {
      window.storageCalls.push({ action, p });
      if (action === "snapshot" || action === "bootstrap") return ok({
        version: "fixture", settings: { theme: "light", accent: "#397863" }, library_storage: true,
        libraries: [library], default_library: library.id, has_credentials: false, credential_error: "",
        tasks: [], counts: { all: 0, completed: 0, pending: 0, ignored: 0 }, total: 0, offset: 0, busy: false,
      });
      if (action === "poll") return ok({ events: [], busy: false });
      if (action === "choose_folder") return ok(migration.destination);
      if (action === "library_migration_status") return ok(status);
      if (action === "library_migration_plan") return ok(migration);
      if (action === "library_migration_start") {
        status = { ...migration, status: "paused", copied_files: 2, copied_bytes: 512 };
        return ok(true);
      }
      if (action === "library_migration_retry") {
        status = { ...migration, status: "done", copied_files: 5, copied_bytes: 1024 };
        library.path = migration.destination;
        return ok(true);
      }
      if (action === "library_auto_flatten") {
        library.auto_flatten = p.enabled;
        library.auto_flatten_pending = false;
        return ok(true);
      }
      return ok(null);
    } } };
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/?desktop=1`);
  const sidebar = page.locator(".sidebar");
  await sidebar.getByRole("button", { name: "媒体工具" }).click();
  await page.getByRole("button", { name: "媒体转移", exact: true }).click();
  await page.getByRole("button", { name: "选择迁移位置" }).click();
  const migration = page.getByRole("dialog", { name: "确认整库迁移" });
  await migration.getByText("F:\\Target", { exact: true }).waitFor();
  await migration.getByRole("button", { name: "返回", exact: true }).click();
  assert.equal(await page.evaluate(() => window.storageCalls.some(({ action }) => action === "library_migration_start")), false);
  await page.getByRole("button", { name: "选择迁移位置" }).click();
  await migration.getByRole("button", { name: "开始迁移" }).click();
  await page.getByText(/迁移已暂停.*2 \/ 5/).waitFor();
  await page.getByRole("button", { name: "继续迁移" }).click();
  await page.getByText(/整库迁移已完成.*5 \/ 5/).waitFor();

  await sidebar.getByRole("button", { name: "设置", exact: true }).click();
  await page.locator(".settings-tabs").getByRole("button", { name: "媒体库", exact: true }).click();
  const toggle = page.getByRole("checkbox", { name: "迁移测试库", exact: true });
  await toggle.click();
  const enable = page.getByRole("dialog", { name: "确认开启自动脱壳" });
  await enable.getByText(/JSON 留在原目录/).waitFor();
  await enable.getByRole("button", { name: "开启并脱壳" }).click();
  await page.waitForFunction(() => document.querySelector(".privacy-setting input")?.checked);
  await toggle.click();
  const disable = page.getByRole("dialog", { name: "确认关闭自动脱壳" });
  await disable.getByText(/移回原来的目录/).waitFor();
  await disable.getByRole("button", { name: "关闭并回退" }).click();
  await disable.waitFor({ state: "hidden" });
  assert.equal(await toggle.isChecked(), false);
  const calls = await page.evaluate(() => window.storageCalls);
  assert.deepEqual(calls.filter(({ action }) => action === "library_auto_flatten").map(({ p }) => p),
    [{ library_id: "main", enabled: true }, { library_id: "main", enabled: false }]);
  console.log("PASS migration confirmation, resumable progress, and both automatic layout confirmations");
} finally {
  await browser?.close();
  await server.close();
}
