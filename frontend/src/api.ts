import type {
  Bridge,
  Snapshot,
  Payload,
  Event,
  Filter,
  Library,
} from "./types";
const MB = 1024 ** 2,
  GB = 1024 ** 3;
export const desktop =
  new URLSearchParams(location.search).get("desktop") === "1";
let bridge: Bridge | undefined;
export async function call<T = unknown>(
  action: string,
  payload: Payload = {},
): Promise<T> {
  if (desktop) {
    if (!window.pywebview)
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(
          () => reject(Error("桌面接口未就绪，请重新启动软件")),
          10000,
        );
        window.addEventListener(
          "pywebviewready",
          () => {
            clearTimeout(timer);
            resolve();
          },
          { once: true },
        );
      });
    bridge = window.pywebview!.api;
  } else bridge = demo;
  const result = await bridge!.call(action, payload);
  if (!result.ok) throw Error(result.error || "操作失败");
  return result.data as T;
}
function initial(): Snapshot {
  const tasks = [
    [
      "123",
      "山野来信",
      "fieldnotes",
      "把周末留给山野，和一场没有计划的日落。",
      "gold",
      [62, 100, 0, 0],
    ],
    [
      "124",
      "城市切片",
      "urbanarchive",
      "雨后的东京，像一帧还没结束的电影。",
      "night",
      [0, 0, 0],
    ],
    [
      "125",
      "慢慢旅行",
      "slowjourney",
      "海边的蓝色时刻，值得一遍遍重看。",
      "sea",
      [100, 100],
    ],
    [
      "126",
      "灵感备忘录",
      "designnotes",
      "一些留白，一些光，和恰到好处的安静。",
      "sand",
      [0, 0],
    ],
  ].map((a, j) => ({
    id: a[0] as string,
    author: a[1] as string,
    handle: a[2] as string,
    title: a[3] as string,
    text: a[3] + "\n\n记录生活中的一些美好。\n#本地收藏 #示例内容",
    demoColor: a[4] as string,
    updated: Date.now() / 1000 - j * 3600,
    ignored: j === 3,
    media: (a[5] as number[]).map((p, i) => ({
      id: j * 10 + i + 1,
      media_id: String(i),
      media_type: (j === 3 || i > 0 ? "image" : "video") as "image" | "video",
      extension: j === 3 || i > 0 ? "jpg" : "mp4",
      state: p === 100 ? "completed" : p ? "paused" : "pending",
      error: "",
      bytes: (i ? 8 : 128) * MB,
      width: 1920,
      height: 1080,
      quality: "best",
      output: "D:\\X2LT\\Collections",
      verification: p === 100 ? "length+sha256" : "",
      demoProgress: p,
    })),
  }));
  return {
    version: "0.6.0 原型",
    settings: {
      theme: "light",
      accent: "#397863",
      username: "",
      quality: "best",
      connections: 4,
      max_mb: 4096,
      media_mode: "all",
      request_strategy: "conservative",
    },
    has_credentials: false,
    credential_error: "",
    libraries: [
      {
        id: "main",
        name: "日常收藏",
        path: "D:\\X2LT\\Collections",
        quota_bytes: 50 * GB,
        used_bytes: 12.4 * GB,
        disk_free: 186 * GB,
        disk_total: 512 * GB,
        available_bytes: 37.6 * GB,
        quota_remaining: 37.6 * GB,
        online: true,
        default: true,
      },
      {
        id: "second",
        name: "灵感归档",
        path: "E:\\Inspiration",
        quota_bytes: 100 * GB,
        used_bytes: 28.7 * GB,
        disk_free: 320 * GB,
        disk_total: 1024 * GB,
        available_bytes: 71.3 * GB,
        quota_remaining: 71.3 * GB,
        online: true,
        default: false,
      },
    ],
    default_library: "main",
    tasks,
    counts: { all: 3, pending: 2, completed: 1, ignored: 1 },
    total: 3,
    offset: 0,
    busy: false,
  };
}
let state = initial();
let veto = new Set<string>();
let active = new Set<number>();
let events: Event[] = [];
try {
  const saved = JSON.parse(localStorage.getItem("x2lt-ui-preferences") || "{}");
  if (saved.settings) state.settings = { ...state.settings, ...saved.settings };
  if (saved.libraries) {
    state.libraries = saved.libraries;
    state.default_library = saved.default_library;
  }
} catch {
  /* Invalid demo preferences fall back to defaults. */
}
const persist = () =>
  localStorage.setItem(
    "x2lt-ui-preferences",
    JSON.stringify({
      settings: state.settings,
      libraries: state.libraries,
      default_library: state.default_library,
    }),
  );
function capacities() {
  state.libraries = state.libraries.map((l) => ({
    ...l,
    default: l.id === state.default_library,
    quota_remaining: l.quota_bytes
      ? Math.max(0, l.quota_bytes - (l.used_bytes || 0))
      : null,
    available_bytes: Math.max(
      0,
      Math.min(
        l.quota_bytes ? l.quota_bytes - (l.used_bytes || 0) : Infinity,
        l.disk_free || 0,
      ),
    ),
  }));
}
const complete = (t: Snapshot["tasks"][number]) =>
  t.media.every((m) => m.state === "completed");
const demo: Bridge = {
  async call(action, p = {}) {
    try {
      let data: unknown = true;
      if (action === "snapshot") {
        capacities();
        const filter = (p.filter || "all") as Filter,
          query = String(p.query || "").toLowerCase();
        state.counts = {
          all: state.tasks.filter((t) => !t.ignored).length,
          pending: state.tasks.filter((t) => !t.ignored && !complete(t)).length,
          completed: state.tasks.filter((t) => !t.ignored && complete(t))
            .length,
          ignored: state.tasks.filter((t) => t.ignored).length,
        };
        const tasks = state.tasks.filter(
          (t) =>
            t.ignored === (filter === "ignored") &&
            (filter === "all" ||
              filter === "ignored" ||
              complete(t) === (filter === "completed")) &&
            (t.title + t.handle).toLowerCase().includes(query),
        );
        data = {
          ...state,
          tasks: tasks.slice(Number(p.offset || 0), Number(p.offset || 0) + 50),
          total: tasks.length,
          offset: Number(p.offset || 0),
        };
      } else if (action === "settings") {
        state.settings = { ...state.settings, ...p };
        persist();
        data = state.settings;
      } else if (action === "choose_folder")
        data = `F:\\Media Library ${state.libraries.length + 1}`;
      else if (action === "library_add" || action === "library_update") {
        const path = String(p.path || "")
          .replace(/[\\/]+$/, "")
          .toLowerCase();
        if (!String(p.name || "").trim()) throw Error("请输入库名称");
        if (!/^[a-z]:[\\/]/i.test(path))
          throw Error("请输入绝对路径，例如 F:\\Media");
        if (
          state.libraries.some(
            (l) =>
              l.id !== p.id &&
              [l.path.toLowerCase(), path].some(
                (v, i, arr) =>
                  v === arr[1 - i] || v.startsWith(arr[1 - i] + "\\"),
              ),
          )
        )
          throw Error("媒体库不能重复或互相嵌套");
        if (
          !Number.isFinite(Number(p.quota_bytes)) ||
          Number(p.quota_bytes) < 0
        )
          throw Error("配额无效");
        if (action === "library_add")
          state.libraries.push({
            id: String(Date.now()),
            name: String(p.name),
            path: String(p.path),
            quota_bytes: Number(p.quota_bytes),
            used_bytes: 0,
            disk_free: 256 * GB,
            disk_total: 512 * GB,
            online: true,
            default: false,
          });
        else
          state.libraries = state.libraries.map((l) =>
            l.id === p.id
              ? {
                  ...l,
                  name: String(p.name),
                  path: String(p.path || l.path),
                  quota_bytes: Number(p.quota_bytes),
                }
              : l,
          );
        if (!state.default_library)
          state.default_library = state.libraries[0].id;
        persist();
      } else if (action === "library_default") {
        state.default_library = String(p.id);
        persist();
      } else if (action === "library_remove") {
        state.libraries = state.libraries.filter((l) => l.id !== p.id);
        if (state.default_library === p.id)
          state.default_library = state.libraries[0]?.id || null;
        persist();
      } else if (action === "library_refresh") data = state.libraries;
      else if (action === "ignore") {
        state.tasks = state.tasks.map((t) =>
          (p.ids as string[]).includes(t.id)
            ? { ...t, ignored: Boolean(p.ignored) }
            : t,
        );
        if (state.busy && (p.ignored as boolean))
          (p.ids as string[]).forEach((id) => veto.add(id));
        events.push({ type: "changed" });
      } else if (action === "start") {
        if (
          !state.libraries.find(
            (l) => l.id === (p.library_id || state.default_library) && l.online,
          )
        )
          throw Error("请选择在线媒体库");
        if (p.mode === "link") {
          if (
            !/^https:\/\/(?:x|twitter)\.com\/[A-Za-z0-9_]+\/status\/\d+(?:\?.*)?$/.test(
              String(p.url),
            )
          )
            throw Error("请输入完整推文链接");
          const task = structuredClone(initial().tasks[0]);
          task.id = String(Date.now());
          task.title = "新添加的模拟任务";
          task.text = "链接仅用于演示，不解析真实内容。";
          task.media.forEach((m, i) => {
            m.id = Date.now() + i;
            m.state = "pending";
            (m as unknown as { demoProgress: number }).demoProgress = 0;
          });
          state.tasks.unshift(task);
        }
        if (p.mode === "verify" || p.mode === "enrich") {
          events.push({
            type: "message",
            text: "演示操作已完成，未访问账号或文件",
          });
          events.push({ type: "finished" });
        } else {
          state.busy = true;
          veto.clear();
          active = new Set(
            state.tasks
              .filter((t) => !t.ignored)
              .map(
                (t) => t.media.find((m) => m.state !== "completed")?.id || 0,
              ),
          );
        }
      } else if (action === "pause") {
        state.busy = false;
        state.tasks.forEach((t) =>
          t.media.forEach((m) => {
            if (m.state === "downloading") m.state = "paused";
          }),
        );
        events.push({ type: "finished" });
      } else if (action === "poll") {
        if (state.busy) {
          for (const t of state.tasks) {
            const m = t.media.find((m) => m.state !== "completed");
            if (!m || ((t.ignored || veto.has(t.id)) && !active.has(m.id)))
              continue;
            const progress = m as typeof m & { demoProgress: number };
            progress.demoProgress = Math.min(
              100,
              (progress.demoProgress || 0) + 3,
            );
            m.state =
              progress.demoProgress >= 100 ? "completed" : "downloading";
            events.push({
              type: "progress",
              id: m.id,
              done: (m.bytes * progress.demoProgress) / 100,
              total: m.bytes,
              time: Date.now() / 1000,
            });
            if (m.state === "completed") events.push({ type: "changed" });
          }
        }
        data = { events: events.splice(0), busy: state.busy };
      } else if (action === "preview") data = null;
      else if (action === "demo_reset") {
        state = initial();
        persist();
        events = [];
      } else if (action === "demo_offline") {
        state.libraries = state.libraries.map((l) =>
          l.id === p.id ? { ...l, online: !l.online } : l,
        );
        persist();
      } else if (action === "credentials" || action === "import_legacy")
        throw Error("原型不保存凭证，请在正式桌面版中配置");
      else data = true;
      return { ok: true, data: structuredClone(data) };
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
  },
};
export function demoProgress(m: Snapshot["tasks"][number]["media"][number]) {
  return (m as typeof m & { demoProgress?: number }).demoProgress || 0;
}
