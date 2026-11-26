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
  const page = await browser.newPage({
    viewport: { width: 1320, height: 950 },
  });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const library = {
      id: "main",
      name: "合成媒体库",
      path: "F:\\Fixture",
      online: true,
      default: true,
      quota_bytes: 0,
    };
    let status = { configured: false, authorized: false, has_proxy: false };
    const jobs = [];
    let scanCount = 0;
    let qrActive = false;
    window.qrResult = "waiting";
    let cancelled = false;
    const items = [
      {
        id: "first",
        title: "合成视频",
        kind: "video",
        size: 1024,
        peer_title: "示例频道",
      },
      {
        id: "second",
        title: "合成图片",
        kind: "image",
        size: 2048,
        peer_title: "示例频道",
      },
    ];
    const ok = (data) => ({ ok: true, data });
    window.requests = [];
    window.pywebview = {
      api: {
        async call(action, p = {}) {
          if (action.startsWith("tg_")) window.requests.push({ action, p });
          if (action === "snapshot")
            return ok({
              version: "fixture",
              settings: {
                theme: "light",
                accent: "#397863",
                privacy_mode: true,
              },
              has_credentials: false,
              credential_error: "",
              libraries: [library],
              default_library: library.id,
              tasks: [],
              counts: { all: 0, pending: 0, completed: 0, ignored: 0 },
              total: 0,
              offset: 0,
              busy: false,
            });
          if (action === "poll") return ok({ events: [], busy: false });
          if (action === "tg_status") return ok(status);
          if (action === "tg_import")
            return ok((status = { ...status, configured: true }));
          if (action === "tg_credentials")
            return ok((status = { ...status, configured: true }));
          if (action === "tg_proxy")
            return ok((status = { ...status, has_proxy: Boolean(p.port), proxy_mode: p.mode, proxy_port: p.port }));
          if (action === "tg_qr_start") {
            qrActive = true;
            return ok({
              state: "waiting",
              image: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCnkAAAAASUVORK5CYII=",
              expires_at: Date.now() + 30000,
            });
          }
          if (action === "tg_qr_status") {
            if (!qrActive) return ok({ state: "idle" });
            if (window.qrResult === "authorized") {
              qrActive = false;
              status = { ...status, authorized: true };
            }
            return ok({ state: window.qrResult });
          }
          if (action === "tg_qr_cancel") {
            qrActive = false;
            return ok({ state: "idle" });
          }
          if (action === "tg_send_code")
            return ok((status = { ...status, code_sent: true, code_delivery: "app", code_can_resend: true,
              code_resend_at: Date.now() + 1400 }));
          if (action === "tg_resend_code")
            return ok((status = { ...status, code_delivery: "sms", code_can_resend: false, code_resend_at: null }));
          if (action === "tg_sign_in") {
            if (p.password) qrActive = false;
            return ok(
              (status = {
                ...status,
                password_required: !p.password,
                authorized: Boolean(p.password),
                code_sent: !p.password,
              }),
            );
          }
          if (action === "tg_channels")
            return ok([{ id: "-1000000000042", title: "示例频道" }]);
          if (action === "tg_scan") return ok({ id: `scan-${++scanCount}` });
          if (action === "tg_scan_status")
            return ok(
              p.id === "scan-1"
                ? { state: "ready", items }
                : {
                    state: cancelled ? "cancelled" : "scanning",
                    items: [],
                  },
            );
          if (action === "tg_scan_cancel") {
            cancelled = true;
            return ok(true);
          }
          if (action === "tg_enqueue") {
            jobs.push({
              id: "job-1",
              title: "合成视频任务",
              kind: "video",
              state: "downloading",
              received: 128,
              total: 1024,
              speed: 32,
              error: "",
            });
            return ok(p.ids.length);
          }
          if (action === "tg_jobs") return ok(jobs);
          if (action === "tg_control") {
            const states = {
              pause: "paused",
              resume: "downloading",
              cancel: "canceled",
              retry: "queued",
            };
            jobs.find((job) => p.ids.includes(job.id)).state = states[p.action];
            return ok(true);
          }
          if (action === "tg_logout")
            return ok((status = { ...status, authorized: false }));
          return ok(null);
        },
      },
    };
  });
  const url = `http://127.0.0.1:${server.httpServer.address().port}`;
  await page.goto(`${url}/?desktop=1`);
  const openTelegram = async () => {
    await page.getByRole("button", { name: "下载任务", exact: true }).click();
    await page.getByRole("button", { name: "电报下载", exact: true }).click();
  };
  await openTelegram();
  const account = page.getByRole("region", { name: "Telegram 账号" });
  await account.getByText("代理设置 · 沿用 X 的代理", { exact: true }).click();
  await account.getByLabel("代理方式").selectOption("socks5");
  await account.getByLabel("本机代理端口").fill("1234");
  await account.getByRole("button", { name: "保存代理" }).click();
  await account.getByText("代理设置 · SOCKS5", { exact: true }).waitFor();
  assert.equal(await account.getByLabel("本机代理端口").inputValue(), "1234");
  await account.getByRole("button", { name: "导入 API 凭证" }).click();
  await account.getByRole("button", { name: "手动输入 API 凭证" }).click();
  await account.getByLabel("API ID", { exact: true }).fill("123456");
  const apiHash = account.getByLabel("API Hash", { exact: true });
  assert.equal(await apiHash.getAttribute("type"), "password");
  await apiHash.fill("b".repeat(32));
  await account.getByRole("button", { name: "保存 API 凭证" }).click();
  await apiHash.waitFor({ state: "detached" });
  const qrButton = account.getByRole("button", { name: "扫码登录" });
  const qrImage = account.getByRole("img", { name: "Telegram 登录二维码" });
  await qrButton.click();
  await qrImage.waitFor();
  assert.match(await qrImage.getAttribute("src"), /^data:image\/png;base64,/);
  await page.evaluate(() => { window.qrResult = "expired"; });
  await account.getByText("二维码已过期，请刷新。", { exact: true }).waitFor();
  assert.equal(await qrImage.count(), 0);
  await page.evaluate(() => { window.qrResult = "waiting"; });
  await account.getByRole("button", { name: "刷新二维码" }).click();
  await qrImage.waitFor();
  await account.getByRole("button", { name: "手机号登录" }).click();
  await page.waitForFunction(() => window.requests.some(({ action }) => action === "tg_qr_cancel"));
  await page.getByLabel("手机号").fill("+10000000000");
  await page.getByRole("button", { name: "发送验证码" }).click();
  await account.getByText(/打开 Telegram 的官方/).waitFor();
  assert.equal(await account.getByRole("button", { name: /秒后可重发/ }).isDisabled(), true);
  await account.getByRole("button", { name: "重新发送验证码" }).click();
  await account.getByText(/查看手机短信/).waitFor();
  await page.getByLabel("验证码", { exact: true }).fill("12345");
  await page.getByRole("button", { name: "验证并登录" }).click();
  const password = page.getByLabel("两步验证密码");
  assert.equal(await password.getAttribute("type"), "password");
  await password.fill("fixture-only");
  await page.getByRole("button", { name: "验证并登录" }).click();
  await account.getByText("已登录", { exact: true }).waitFor();

  await page.getByRole("button", { name: "频道", exact: true }).click();
  await page.getByRole("button", { name: "载入频道" }).click();
  await page.getByLabel("已加入的频道").selectOption("-1000000000042");
  await page.getByRole("button", { name: "扫描媒体" }).click();
  assert.equal(await page.getByText("合成视频", { exact: true }).count(), 0);
  const preview = page.getByRole("region", { name: "媒体预览" });
  await preview
    .getByRole("button", { name: "内容已折叠 · 点击展开" })
    .first()
    .click();
  await preview.getByText("合成视频", { exact: true }).waitFor();
  await preview.getByRole("checkbox", { name: "选择第 2 项" }).uncheck();
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "电报下载", exact: true }).click();
  await preview.getByRole("button", { name: "下载所选 1 项" }).waitFor();
  await preview.getByRole("button", { name: "下载所选 1 项" }).click();
  const queue = page.getByRole("region", { name: "下载队列" });
  await queue.getByRole("button", { name: "暂停" }).click();
  await queue.getByRole("button", { name: "继续" }).click();
  await queue.getByRole("button", { name: "取消" }).click();
  await queue.getByText("已取消", { exact: true }).waitFor();
  await queue.getByRole("button", { name: "重试" }).click();
  await queue.getByText("排队中", { exact: true }).waitFor();
  await queue.getByRole("button", { name: "暂停" }).click();
  await queue.getByText("已暂停", { exact: true }).waitFor();
  await page.getByRole("button", { name: "消息链接", exact: true }).click();
  await page.getByLabel("Telegram 消息链接").fill("https://t.me/fixture/42");
  await page.getByRole("button", { name: "扫描媒体" }).click();
  await page.getByRole("button", { name: "取消扫描" }).click();
  await page.getByText("扫描已取消", { exact: true }).waitFor();
  await account.getByRole("button", { name: "退出登录" }).click();
  await account.getByText("等待登录", { exact: true }).waitFor();
  await qrButton.click();
  await qrImage.waitFor();
  const cancelCount = await page.evaluate(() =>
    window.requests.filter(({ action }) => action === "tg_qr_cancel").length,
  );
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.waitForFunction((count) =>
    window.requests.filter(({ action }) => action === "tg_qr_cancel").length === count + 1,
    cancelCount,
  );
  await page.getByRole("button", { name: "电报下载", exact: true }).click();
  await account.getByLabel("手机号").waitFor();
  await page.evaluate(() => { window.qrResult = "authorized"; });
  await qrButton.click();
  await account.getByText("已登录", { exact: true }).waitFor();
  await account.getByRole("button", { name: "退出登录" }).click();
  await account.getByText("等待登录", { exact: true }).waitFor();
  await page.evaluate(() => { window.qrResult = "password"; });
  await qrButton.click();
  const qrPassword = account.getByLabel("两步验证密码");
  await qrPassword.fill("qr-fixture-only");
  await account.getByRole("button", { name: "验证并登录" }).click();
  await account.getByText("已登录", { exact: true }).waitFor();
  const calls = await page.evaluate(() => window.requests);
  assert.deepEqual(calls.filter(({ action }) => action === "tg_sign_in").at(-1)?.p, {
    password: "qr-fixture-only",
  });
  assert.deepEqual(calls.find(({ action }) => action === "tg_import")?.p, {});
  assert.deepEqual(calls.find(({ action }) => action === "tg_proxy")?.p, {
    mode: "socks5", port: 1234,
  });
  assert.deepEqual(calls.find(({ action }) => action === "tg_credentials")?.p, {
    api_id: "123456", api_hash: "b".repeat(32),
  });
  assert.deepEqual(
    calls.filter(({ action }) => action === "tg_scan").map(({ p }) => p),
    [
      {
        mode: "channel",
        source: "-1000000000042",
        limit: 50,
        media_mode: "all",
      },
      {
        mode: "link",
        source: "https://t.me/fixture/42",
        limit: 50,
        media_mode: "all",
      },
    ],
  );
  assert.deepEqual(calls.find(({ action }) => action === "tg_enqueue")?.p, {
    scan_id: "scan-1",
    ids: ["first"],
    library_id: "main",
  });
  assert.deepEqual(errors, []);
  console.log(
    "PASS Telegram credentials, phone and QR sign-in, private preview, scans, queue actions and logout",
  );
} finally {
  await browser?.close();
  await server.close();
}
