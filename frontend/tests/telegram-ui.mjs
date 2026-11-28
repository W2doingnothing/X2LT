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
    const otherLibrary = { ...library, id: "other", name: "备用媒体库", default: false };
    const preauthorized = new URL(location.href).searchParams.has("authorizedFixture");
    let status = { configured: preauthorized, authorized: preauthorized, has_proxy: false };
    const jobs = [];
    let scanCount = 0;
    let qrActive = false;
    window.qrResult = "waiting";
    window.scan2ShowMore = false;
    let cancelled = false;
    const items = [
      {
        id: "first",
        title: "合成视频",
        body: "合成图集说明",
        message_key: "video-message",
        card_key: "album",
        media_key: "album:video",
        sent_at: 1767225600,
        kind: "video",
        width: 640,
        height: 230,
        size: 1024,
        peer_title: "示例频道",
      },
      {
        id: "second",
        title: "合成图片",
        message_key: "photo-message",
        card_key: "album",
        media_key: "album:photo",
        download_state: "completed",
        kind: "image",
        width: 260,
        height: 700,
        size: 2048,
        peer_title: "示例频道",
      },
      {
        id: "third",
        title: "转发图片",
        message_key: "forward",
        media_key: "forward:image",
        forward_title: "原始频道",
        forward_id: "-1000000000050",
        download_state: "failed",
        kind: "image",
        size: 4096,
        peer_title: "示例频道",
      },
    ];
    const rangeItem = {
      ...items[0],
      id: "range-first",
      body: "区间扫描首条",
      message_key: "range-first",
      card_key: "range-first",
      media_key: "range-first:video",
    };
    const laterItem = {
      ...rangeItem,
      id: "later",
      body: "后续扫描结果",
      message_key: "later-message",
      card_key: "later-message",
      media_key: "later-message:video",
    };
    const makeJob = (item, id, state, library_id, received = 0) => ({
      ...item, id, state, library_id, received, total: item.size, speed: 0, error: "",
      extension: item.kind === "video" ? "mp4" : "jpg",
    });
    jobs.push(
      makeJob(items[1], "saved", "completed", "main", items[1].size),
      makeJob(items[2], "failed", "failed", "main"),
    );
    const ok = (data) => ({ ok: true, data });
    const settings = { theme: "light", accent: "#397863", privacy_mode: true };
    let backfill = null;
    window.requests = [];
    window.pywebview = {
      api: {
        async call(action, p = {}) {
          if (action.startsWith("tg_") || action === "settings") window.requests.push({ action, p });
          if (action === "settings") return ok(Object.assign(settings, p));
          if (action === "snapshot")
            return ok({
              version: "fixture",
              settings: { ...settings },
              has_credentials: false,
              credential_error: "",
              libraries: [library, otherLibrary],
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
          if (action === "tg_scan") {
            cancelled = false;
            const id = `scan-${++scanCount}`;
            if (p.backfill) backfill = { id, state: "ready", library_id: p.library_id, found: 2100, added: 2050 };
            return ok({ id });
          }
          if (action === "tg_backfill_status") return ok(backfill);
          if (action === "tg_automation_status") return ok({});
          if (action === "tg_scan_status")
            return ok(
              p.id === "scan-1"
                ? {
                    state: "ready",
                    items: p.library_id === "other"
                      ? [
                          { ...items[0], download_state: "queued" },
                          { ...items[1], download_state: "new" },
                          items[2],
                        ]
                      : items,
                  }
                : {
                    ...(p.id === "scan-4" ? {
                      state: "ready",
                      items: [rangeItem, ...Array.from({ length: 4 }, (_, index) => ({
                        ...laterItem,
                        id: `saved-photo-${index + 1}`,
                        media_key: `saved-photo-${index + 1}`,
                        kind: "image",
                      }))],
                    } : {
                    state: cancelled ? "cancelled" : "scanning",
                    items: p.id === "scan-2"
                      ? [rangeItem, ...(window.scan2ShowMore ? [laterItem] : [])]
                      : [],
                    }),
                  },
            );
          if (action === "tg_scan_preview" || action === "tg_job_preview") {
            const portrait = p.id === "second" || p.id === "saved";
            const [width, height] = portrait ? [260, 700] : [640, 230];
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="teal"/></svg>`;
            return ok("data:image/svg+xml," + encodeURIComponent(svg));
          }
          if (action === "tg_scan_cancel") {
            cancelled = true;
            return ok(true);
          }
          if (action === "tg_enqueue") {
            const item = items.find(({ id }) => id === p.ids[0]);
            jobs.push({ ...makeJob(item, "job-1", "downloading", p.library_id, 128), speed: 32 });
            return ok(p.ids.length);
          }
          if (action === "tg_jobs") return ok(jobs.filter((job) => !job.hidden && job.state !== "canceled"));
          if (action === "tg_bulk_control") {
            const removed = [];
            for (const job of jobs) {
              if (p.action === "resume" && job.state === "paused") job.state = "downloading";
              if (p.action === "cancel" && !["completed", "canceled"].includes(job.state)) {
                job.state = "canceled";
                removed.push({ library_id: job.library_id, media_key: job.media_key });
              }
            }
            return ok(p.report ? removed : true);
          }
          if (action === "tg_control") {
            const states = {
              pause: "paused",
              resume: "downloading",
              cancel: "canceled",
              retry: "queued",
            };
            const removed = [];
            for (const job of jobs.filter((entry) => p.ids.includes(entry.id))) {
              if (p.action === "delete") job.hidden = true;
              else job.state = states[p.action];
              if (p.report) removed.push({ library_id: job.library_id, media_key: job.media_key });
            }
            return ok(p.report ? removed : true);
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
  await page.getByRole("button", { name: "前往电报账号设置" }).click();
  const openAccount = async () => {
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: "电报账号", exact: true }).click();
  };
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
  await page.waitForFunction(() => window.requests.some(({ action }) => action === "tg_channels"));

  await page.getByRole("button", { name: "电报下载", exact: true }).click();
  await account.waitFor({ state: "hidden" });
  assert.equal(await page.getByRole("button", { name: "频道", exact: true }).getAttribute("aria-pressed"), "true");
  await page.getByRole("button", { name: "显示频道" }).click();
  await page.getByLabel("已加入的频道").selectOption("-1000000000042");
  await page.getByRole("button", { name: "扫描媒体" }).click();
  assert.equal(await page.getByText("合成图集说明", { exact: true }).count(), 0);
  const preview = page.getByRole("region", { name: "电报媒体" });
  const album = preview.locator('[data-message-key="album"]');
  const forwarded = preview.locator('[data-message-key="forward"]');
  const video = album.locator('[data-media-key="album:video"]');
  const photo = album.locator('[data-media-key="album:photo"]');
  await video.waitFor();
  await video.locator(".telegram-thumbnail img").waitFor();
  await photo.waitFor();
  const videoFrame = await video.locator(".telegram-media-frame").boundingBox();
  const photoFrame = await photo.locator(".telegram-media-frame").boundingBox();
  assert.ok(photoFrame.x >= videoFrame.x + videoFrame.width, "media are side by side");
  assert.ok(Math.abs(photoFrame.y - videoFrame.y) < 1, "media share a row");
  const forwardingCard = await forwarded.boundingBox();
  const albumCard = await album.boundingBox();
  assert.ok(forwardingCard.y >= albumCard.y + albumCard.height, "each message occupies a full row");
  assert.ok(Math.abs(forwardingCard.width - albumCard.width) < 1);
  assert.equal(await album.locator(".media-stack").count(), 0);
  await photo.locator(".telegram-thumbnail img").waitFor();
  await video.getByRole("button", { name: "显示封面" }).click();
  assert.equal(await video.getByRole("img", { name: "媒体预览图" }).isVisible(), true);
  assert.equal(await photo.getByRole("img", { name: "已模糊的封面" }).isVisible(), true);
  await page.setViewportSize({ width: 530, height: 950 });
  assert.equal(await album.locator(".media-grid [data-media-key]").count(), 2);
  await page.setViewportSize({ width: 1320, height: 950 });
  assert.equal(await album.count(), 1);
  await album
    .getByRole("button", { name: "内容已折叠 · 点击展开" })
    .first()
    .click();
  await preview.getByText("合成图集说明", { exact: true }).waitFor();
  assert.equal(await album.getAttribute("data-library-source"), "telegram");
  assert.equal(await album.getByText("Telegram", { exact: true }).count(), 1);
  assert.equal(await album.getByText(/转发自/).count(), 0);
  await forwarded.getByText(/转发自/).waitFor();
  await photo.getByText("已保存", { exact: true }).waitFor();
  await forwarded.getByText(/失败/).first().waitFor();
  assert.equal(await photo.getByRole("checkbox", { checked: true }).count(), 0);
  assert.equal(await forwarded.getByRole("checkbox", { checked: true }).count(), 0);
  assert.equal(await video.getByRole("checkbox").isChecked(), true);
  await preview.getByLabel("目标媒体库").selectOption("other");
  await video.getByText("排队中", { exact: true }).waitFor();
  assert.equal(await video.getByRole("checkbox", { checked: true }).count(), 0);
  assert.equal(await photo.getByRole("checkbox").isChecked(), true);
  await preview.getByLabel("目标媒体库").selectOption("main");
  await photo.getByText("已保存", { exact: true }).waitFor();
  await page.getByRole("button", { name: "总览", exact: true }).click();
  await page.getByRole("button", { name: "电报下载", exact: true }).click();
  await preview.getByRole("button", { name: "下载所选 1 项" }).waitFor();
  await preview.getByRole("button", { name: "下载所选 1 项" }).click();
  await video.getByRole("progressbar", { name: "媒体进度" }).waitFor();
  assert.equal(await page.getByRole("region", { name: "下载队列" }).count(), 0);
  assert.equal(await album.count(), 1);
  await album.getByRole("button", { name: "暂停", exact: true }).click();
  await album.getByRole("button", { name: "继续", exact: true }).click();
  await forwarded.getByRole("button", { name: "取消", exact: true }).click();
  await forwarded.waitFor({ state: "detached" });
  await album.getByRole("button", { name: "暂停", exact: true }).click();
  await video.getByText("已暂停", { exact: true }).waitFor();
  await page.getByLabel("扫描范围").selectOption("dates");
  await page.getByRole("button", { name: "最近 7 天", exact: true }).click();
  assert.match(await page.getByLabel("开始日期").inputValue(), /^\d{4}-\d{2}-\d{2}$/);
  await page.getByLabel("开始日期").fill("2026-11-01");
  await page.getByLabel("结束日期").fill("2026-11-05");
  await page.getByRole("button", { name: "扫描媒体" }).click();
  const rangeFirst = preview.locator('[data-message-key="range-first"]');
  const later = preview.locator('[data-message-key="later-message"]');
  await rangeFirst.getByRole("checkbox").uncheck();
  await page.evaluate(() => { window.scan2ShowMore = true; });
  await later.waitFor();
  assert.equal(await rangeFirst.getByRole("checkbox").isChecked(), false);
  assert.equal(await later.getByRole("checkbox").isChecked(), true);
  assert.equal(await preview.getByRole("button", { name: "下载所选 1 项" }).isDisabled(), true);
  await page.getByRole("button", { name: "取消扫描" }).click();
  await page.getByText("扫描已取消", { exact: true }).waitFor();
  await preview.getByRole("button", { name: "一键继续" }).click();
  await video.getByText("下载中", { exact: true }).waitFor();
  const cancelAll = preview.getByRole("button", { name: "取消所有任务" });
  const confirmation = page.getByRole("dialog", { name: "取消所有电报下载任务" });
  const deleteFiles = confirmation.getByRole("checkbox");
  await cancelAll.click();
  assert.equal(await deleteFiles.isChecked(), false);
  await deleteFiles.check();
  await confirmation.getByRole("button", { name: "返回" }).click();
  assert.equal(await page.evaluate(() => window.requests.filter(({action}) => action === "tg_bulk_control").length), 1);
  await cancelAll.click();
  assert.equal(await deleteFiles.isChecked(), false);
  await deleteFiles.check();
  await confirmation.getByRole("button", { name: "确认取消" }).click();
  await video.waitFor({ state: "detached" });
  await photo.getByText("已保存", { exact: true }).waitFor();
  assert.equal(await rangeFirst.count(), 1);
  assert.equal(await later.count(), 1);
  assert.equal(await album.count(), 1);
  await album.getByRole("checkbox", { name: "选择此任务" }).check();
  await preview.getByRole("button", { name: "删除所选任务" }).click();
  await page.getByRole("dialog", { name: "删除所选任务" }).getByRole("button", { name: "确认删除" }).click();
  await album.waitFor({ state: "detached" });
  assert.equal(await preview.getByRole("button", { name: "下载所选 1 项" }).isEnabled(), true);
  await page.getByRole("button", { name: "消息链接", exact: true }).click();
  await page.getByLabel("Telegram 消息链接").fill("https://t.me/fixture/42");
  await page.getByRole("button", { name: "扫描媒体" }).click();
  await page.getByRole("button", { name: "取消扫描" }).click();
  await page.getByText("扫描已取消", { exact: true }).waitFor();
  await page.getByRole("button", { name: "我的收藏", exact: true }).click();
  await page.getByLabel("扫描范围").selectOption("recent");
  await page.getByLabel("媒体类型").selectOption("video");
  await page.getByRole("button", { name: "扫描新增媒体" }).click();
  await rangeFirst.waitFor();
  assert.equal(await later.count(), 0);
  await page.getByLabel("媒体类型").selectOption("all");
  await later.waitFor();
  assert.equal(await later.locator("[data-media-key]").count(), 3);
  await later.getByRole("button", { name: "下一组媒体" }).click();
  await later.locator('[data-media-key="saved-photo-4"]').waitFor();
  assert.equal(await later.locator("[data-media-key]").count(), 1);
  await later.getByRole("button", { name: "上一组媒体" }).click();
  assert.equal(await later.locator("[data-media-key]").count(), 3);
  await openAccount();
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
  await openAccount();
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
  assert.deepEqual(calls.filter(({ action }) => action === "tg_bulk_control").map(({ p }) => p), [
    {action: "resume"}, {action: "cancel", delete_partial: true, report: true},
  ]);
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
        mode: "channel",
        source: "-1000000000042",
        limit: 50,
        media_mode: "all",
        start_date: "2026-11-01",
        end_date: "2026-11-05",
      },
      {
        mode: "link",
        source: "https://t.me/fixture/42",
        limit: 50,
        media_mode: "all",
      },
      {
        mode: "saved",
        source: "",
        limit: 50,
        media_mode: "video",
        incremental: true,
      },
    ],
  );
  assert.deepEqual(calls.find(({ action }) => action === "tg_enqueue")?.p, {
    scan_id: "scan-1",
    ids: ["first"],
    library_id: "main",
  });
  assert.deepEqual(calls.find(({ action }) => action === "tg_scan_status")?.p, {
    id: "scan-1",
    library_id: "main",
  });
  assert.deepEqual(calls.filter(({ action }) => action === "tg_control").map(({ p }) => p.ids[0]),
    ["job-1", "job-1", "failed", "job-1", "saved"]);
  assert.deepEqual(errors, []);
  await page.goto(`${url}/?desktop=1&authorizedFixture=1`);
  await page.waitForFunction(() => window.requests.some(({ action }) => action === "tg_channels"));
  assert.equal(await page.getByRole("heading", { name: "电报下载", exact: true }).count(), 0);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "自动任务", exact: true }).click();
  await page.getByLabel("按每日时段继续和暂停下载").check();
  await page.getByLabel("开始时间").fill("23:00");
  await page.getByLabel("暂停时间").fill("05:00");
  await page.getByText("下载时段跨到次日。").waitFor();
  await page.getByRole("button", { name: "保存下载时段" }).click();
  await page.getByLabel("关闭窗口时收到系统托盘").check();
  await page.getByRole("button", { name: "添加扫描规则" }).click();
  await page.getByLabel("扫描来源", { exact: true }).selectOption("channel");
  await page.getByLabel("频道", { exact: true }).selectOption("-1000000000042");
  await page.getByLabel("软件启动时扫描一次").check();
  await page.getByLabel("间隔（分钟）").fill("90");
  await page.getByRole("button", { name: "保存规则" }).click();
  await page.getByText("示例频道 · 合成媒体库").waitFor();
  const savedSettings = await page.evaluate(() => window.requests.filter(({ action }) => action === "settings").map(({ p }) => p));
  assert.deepEqual(savedSettings, [
    { schedule_enabled: true, schedule_start: "23:00", schedule_end: "05:00" },
    { close_to_tray: true },
    { telegram_scan_rules: [{ mode: "channel", source: "-1000000000042", title: "示例频道", library_id: "main",
      media_mode: "all", on_start: true, in_window: true, interval: 90 }] },
  ]);
  await page.getByRole("button", { name: /下载任务/ }).click();
  await page.getByRole("button", { name: "电报下载", exact: true }).click();
  await page.getByRole("button", { name: "我的收藏", exact: true }).click();
  await page.getByRole("button", { name: "补齐全部历史媒体", exact: true }).click();
  await page.getByText("历史扫描完成 · 找到 2100 项媒体，2050 项已提交").waitFor();
  assert.equal(await page.evaluate(() => window.requests.findLast(({ action }) => action === "tg_scan").p.backfill), true);
  assert.deepEqual(errors, []);
  console.log(
    "PASS Telegram credentials, sign-in, private preview, scan and queue controls, history backfill and automation settings",
  );
} finally {
  await browser?.close();
  await server.close();
}
