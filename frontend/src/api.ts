import { validEmoji } from "./EmojiPicker";
import type {
  Bridge,
  Snapshot,
  Payload,
  Event,
  Filter,
  Library,
  Author,
  AuthorPreference,
  Resource,
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
    tags: [] as string[],
    platform: "x",
    display_name: a[1] as string,
    avatar_available: false,
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
      width: j === 2 ? 720 : 1920,
      height: 1080,
      quality: "best",
      output: "D:\\X2LT\\Collections",
      verification: p === 100 ? "length+sha256" : "",
      demoProgress: p,
    })),
  }));
  tasks[2].media.push({
    ...tasks[2].media[0],
    id: 99,
    output: "E:\\Inspiration",
  });
  return {
    version: "0.7.9 原型",
    operation: null,
    settings: {
      confirm_removal: true,
      privacy_mode: false,
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
let authorPreferences: Record<string, Record<string, AuthorPreference>> = {};
let veto = new Set<string>();
let active = new Set<number>();
let events: Event[] = [];
try {
  const saved = JSON.parse(localStorage.getItem("x2lt-ui-preferences") || "{}");
  if (saved.tags) state.tasks.forEach((t) => (t.tags = saved.tags[t.id] || []));
  if (saved.settings) state.settings = { ...state.settings, ...saved.settings };
  if (saved.authorPreferences) authorPreferences = saved.authorPreferences;
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
      tags: Object.fromEntries(state.tasks.map((t) => [t.id, t.tags || []])),
      settings: state.settings,
      libraries: state.libraries,
      default_library: state.default_library,
      authorPreferences,
    }),
  );
function capacities() {
  state.libraries = state.libraries.map((l) => ({
    ...l,
    emoji: l.emoji || (l.cover?.kind === "emoji" ? l.cover.value : "📁"),
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
const resourceOrder = new Map<string, number>();
function resources(): Resource[] {
  const paths = new Set(state.libraries.map((l) => l.path.toLowerCase()));
  const map = new Map<string, Resource>();
  for (const t of state.tasks)
    for (const m of t.media) {
      if (m.state !== "completed" || !paths.has(m.output.toLowerCase()))
        continue;
      const key = JSON.stringify([
        t.platform || "x",
        t.id,
        m.media_type,
        m.media_id,
      ]);
      if (!resourceOrder.has(key))
        resourceOrder.set(key, resourceOrder.size + 1);
      const old = map.get(key);
      if (old) old.locations.push({ ...m });
      else
        map.set(key, {
          key,
          platform: t.platform || "x",
          source_label: "X",
          message_id: t.id,
          media_id: m.media_id,
          media_type: m.media_type,
          title: t.text || t.title,
          author: t.author,
          added_at: null,
          tags: t.tags || [],
          locations: [
            { ...m, filename: `${t.id}_${m.media_id}.${m.extension}` },
          ],
        });
    }
  return [...map.values()].sort(
    (a, b) => resourceOrder.get(b.key)! - resourceOrder.get(a.key)!,
  );
}
const removedFilters = new Map<
  string,
  {
    key: string;
    platform: string;
    message_id: string;
    author: string;
    text: string;
    created: number;
    reason: string;
    operations: never[];
  }
>();
const demo: Bridge = {
  async call(action, p = {}) {
    try {
      let data: unknown = true;
      if (action === "removal_plan" || action === "removal_start") {
        const lib = state.libraries.find((l) => l.id === p.library_id);
        if (!lib) throw Error("请选择媒体库");
        const ids = p.ids as string[];
        const tasks = state.tasks.filter((t) => ids.includes(t.id));
        const media = tasks.flatMap((t) =>
          t.media.filter(
            (m) =>
              m.output.toLowerCase() === lib.path.toLowerCase() &&
              m.state !== "removed",
          ),
        );
        if (!media.length) throw Error("当前库中没有对应媒体");
        data = {
          content_count: tasks.length,
          media_count: media.length,
          bytes: media.reduce((n, m) => n + m.bytes, 0),
          unknown: 0,
        };
        if (action === "removal_start") {
          tasks.forEach((t) => {
            t.ignored = true;
            const key = JSON.stringify(["x", t.id]);
            removedFilters.set(key, {
              key,
              platform: "x",
              message_id: t.id,
              author: t.author,
              text: t.text,
              created: Date.now() / 1000,
              reason: "removed",
              operations: [],
            });
            t.media.forEach((m) => {
              if (m.output.toLowerCase() === lib.path.toLowerCase())
                m.state = "removed";
            });
          });
          events.push({ type: "changed" });
        }
      } else if (action === "filters") {
        const all = new Map(removedFilters);
        state.tasks
          .filter((t) => t.ignored)
          .forEach((t) => {
            const key = JSON.stringify(["x", t.id]);
            if (!all.has(key))
              all.set(key, {
                key,
                platform: "x",
                message_id: t.id,
                author: t.author,
                text: t.text,
                created: t.updated,
                reason: "ignored",
                operations: [],
              });
          });
        const items = [...all.values()].filter((i) =>
          (i.author + " " + i.text + " " + i.message_id)
            .toLowerCase()
            .includes(String(p.query || "").toLowerCase()),
        );
        let off = Number(p.offset || 0);
        if (off >= items.length)
          off = Math.max(0, Math.floor((items.length - 1) / 50)) * 50;
        data = {
          items: items.slice(off, off + 50),
          total: items.length,
          offset: off,
        };
      } else if (action === "filters_clear") {
        const keys = p.all
          ? [
              ...new Set([
                ...removedFilters.keys(),
                ...state.tasks
                  .filter((t) => t.ignored)
                  .map((t) => JSON.stringify(["x", t.id])),
              ]),
            ]
          : (p.keys as string[]);
        keys.forEach((key) => {
          removedFilters.delete(key);
          const [, id] = JSON.parse(key);
          const t = state.tasks.find((t) => t.id === id);
          if (t) t.ignored = false;
        });
        events.push({ type: "changed" });
        data = keys.length;
      } else if (action === "resources" || action === "resource_overview") {
        const all = resources();
        if (action === "resource_overview")
          data = {
            resources: all.slice(0, 8),
            total: all.length,
            offset: 0,
            video_count: all.filter((r) => r.media_type === "video").length,
            image_count: all.filter((r) => r.media_type === "image").length,
          };
        else {
          const words = String(p.query || "")
            .toLowerCase()
            .split(/\s+/)
            .filter(Boolean);
          const rows = all.filter(
            (r) =>
              (!p.kind || p.kind === "all" || r.media_type === p.kind) &&
              ((p.tags || []) as string[]).every((tag) =>
                r.tags.some((t) => t.toLowerCase() === tag.toLowerCase()),
              ) &&
              words.every((word) =>
                (
                  r.title +
                  " " +
                  r.locations.map((m) => m.filename).join(" ") +
                  " " +
                  r.media_id
                )
                  .toLowerCase()
                  .includes(word),
              ),
          );
          let off = Math.max(0, Number(p.offset || 0));
          const limit = Math.min(100, Math.max(1, Number(p.limit || 50)));
          if (off >= rows.length)
            off = Math.max(0, Math.floor((rows.length - 1) / limit)) * limit;
          data = {
            resources: rows.slice(off, off + limit),
            total: rows.length,
            offset: off,
          };
        }
      } else if (action === "snapshot") {
        capacities();
        const filter = (p.filter || "all") as Filter;
        const lib = p.library_id
          ? state.libraries.find((l) => l.id === p.library_id)
          : null;
        if (p.library_id && !lib) throw Error("媒体库不存在");
        const collection = !!p.collection && !!lib;
        const sourceView = collection && p.view === "sources";
        const authorKey = (t: Snapshot["tasks"][number]) =>
          "handle:" + t.handle.normalize("NFKC").toLowerCase();
        const scope = state.tasks
          .map((t) => ({
            ...t,
            media: t.media.filter(
              (m) =>
                m.state !== "removed" &&
                (!lib || m.output.toLowerCase() === lib.path.toLowerCase()) &&
                (!collection || m.state === "completed"),
            ),
          }))
          .filter((t) => t.media.length);
        const archived = (t: (typeof scope)[number]) =>
          lib ? t.media.some((m) => m.state === "completed") : complete(t);
        const query = String(p.query || "").normalize("NFKC");
        const wanted = [
          ...((p.tags || []) as string[]),
          ...Array.from(query.matchAll(/#([^\s#]+)/gu), (m) => m[1]),
        ].map((t) => t.toLowerCase());
        const keyword = query
          .replace(/#[^\s#]+/gu, "")
          .trim()
          .toLowerCase();
        const matched = scope.filter(
          (t) =>
            (!p.untagged || !t.tags?.length) &&
            wanted.every((tag) =>
              t.tags?.some((x) => x.toLowerCase() === tag),
            ) &&
            (!p.author_key || authorKey(t) === p.author_key) &&
            (!p.source_key || (t.platform || "x") === p.source_key) &&
            (p.view === "authors"
              ? (t.display_name || t.author) + t.handle
              : t.title + t.text + t.handle + t.author
            )
              .toLowerCase()
              .includes(keyword),
        );
        const counts = {
          all: matched.filter((t) => !t.ignored).length,
          pending: matched.filter((t) => !t.ignored && !complete(t)).length,
          completed: matched.filter((t) => !t.ignored && archived(t)).length,
          ignored: matched.filter((t) => t.ignored).length,
        };
        const tasks = matched.filter((t) =>
          collection
            ? true
            : filter === "ignored"
              ? t.ignored
              : !t.ignored &&
                (filter === "all" ||
                  (filter === "completed" ? archived(t) : !complete(t))),
        );
        const authors = new Map<string, Author>();
        if (collection)
          matched.forEach((t) => {
            const key = authorKey(t);
            const previous = authors.get(key);
            authors.set(key, {
              key,
              handle: t.handle,
              display_name: t.display_name || t.author,
              avatar_available: !!t.avatar_available,
              message_id: previous?.message_id || t.id,
              media_count:
                (previous?.media_count || 0) +
                new Set(t.media.map((m) => m.media_type + ":" + m.media_id))
                  .size,
              message_count: (previous?.message_count || 0) + 1,
            });
          });
        let authorRows = [...authors.values()].map((a) => ({
          ...a,
          ...(authorPreferences[lib?.id || ""]?.[a.key] || {
            starred: false,
            in_other: false,
          }),
        }));
        const authorView = collection && p.view === "authors";
        if (authorView) {
          const group = p.author_group || "root";
          if (group === "other" || group === "archive")
            authorRows = authorRows.filter((a) => a.in_other);
          else if (group === "active")
            authorRows = authorRows.filter((a) => !a.in_other);
          else if (group === "root") {
            const other = authorRows.filter((a) => a.in_other);
            authorRows = authorRows.filter((a) => !a.in_other);
            if (other.length)
              authorRows.push({
                key: "__other__",
                handle: "",
                display_name: "其他博主",
                avatar_available: false,
                message_id: "",
                starred: false,
                in_other: true,
                is_group: true,
                member_count: other.length,
                media_count: other.reduce((n, a) => n + a.media_count, 0),
                message_count: other.reduce((n, a) => n + a.message_count, 0),
              } as Author & AuthorPreference);
          }
        }
        const rank = (a: Author) => (a.starred ? 0 : a.is_group ? 1 : 2);
        authorRows.sort(
          (a, b) =>
            rank(a) - rank(b) ||
            b.media_count - a.media_count ||
            b.message_count - a.message_count ||
            a.key.localeCompare(b.key),
        );
        const requestedOffset = Math.max(0, Number(p.offset || 0));
        const resolvedOffset =
          authorView && requestedOffset >= authorRows.length
            ? Math.max(0, Math.floor((authorRows.length - 1) / 50)) * 50
            : requestedOffset;
        const options = new Map<string, number>();
        scope.forEach((t) =>
          t.tags?.forEach((tag) =>
            options.set(tag, (options.get(tag) || 0) + 1),
          ),
        );
        data = {
          ...state,
          counts,
          tag_options: [...options].map(([name, count]) => ({ name, count })),
          libraries: state.libraries.map((l) => {
            const archived = state.tasks
              .map((t) => ({
                ...t,
                media: t.media.filter(
                  (m) => m.output.toLowerCase() === l.path.toLowerCase(),
                ),
              }))
              .filter((t) => t.media.some((m) => m.state === "completed"));
            return {
              ...l,
              emoji: l.emoji || "📁",
              video_count: new Set(
                archived.flatMap((t) =>
                  t.media
                    .filter(
                      (m) =>
                        m.state === "completed" && m.media_type === "video",
                    )
                    .map((m) => t.id + ":" + m.media_id),
                ),
              ).size,
              image_count: new Set(
                archived.flatMap((t) =>
                  t.media
                    .filter(
                      (m) =>
                        m.state === "completed" && m.media_type === "image",
                    )
                    .map((m) => t.id + ":" + m.media_id),
                ),
              ).size,
              message_count: archived.length,
              media_count: archived.reduce(
                (n, t) =>
                  n +
                  new Set(
                    t.media
                      .filter((m) => m.state === "completed")
                      .map((m) => m.media_type + ":" + m.media_id),
                  ).size,
                0,
              ),
            };
          }),
          tasks:
            authorView || sourceView
              ? []
              : tasks.slice(Number(p.offset || 0), Number(p.offset || 0) + 50),
          authors: authorRows.slice(resolvedOffset, resolvedOffset + 50),
          collection_author_count: new Set(scope.map(authorKey)).size,
          sources:
            sourceView &&
            scope.length &&
            (!p.untagged || scope.some((t) => !t.tags?.length)) &&
            ["x", "twitter", "推特"].some((name) => name.includes(keyword))
              ? (() => {
                  const rows = scope.filter(
                    (t) =>
                      (!p.untagged || !t.tags?.length) &&
                      wanted.every((tag) =>
                        t.tags?.some((v) => v.toLowerCase() === tag),
                      ),
                  );
                  return rows.length
                    ? [
                        {
                          key: "x",
                          label: "X",
                          message_count: rows.length,
                          media_count: new Set(
                            rows.flatMap((t) =>
                              t.media.map(
                                (m) =>
                                  t.id + ":" + m.media_type + ":" + m.media_id,
                              ),
                            ),
                          ).size,
                        },
                      ]
                    : [];
                })()
              : [],
          total: sourceView ? 0 : authorView ? authorRows.length : tasks.length,
          offset: resolvedOffset,
        };
        if (sourceView) {
          const result = data as Snapshot;
          result.total = result.sources?.length || 0;
          result.offset = 0;
        }
      } else if (action === "author_preferences") {
        if (p.archived !== undefined) p = { ...p, in_other: p.archived };
        const lib = state.libraries.find((l) => l.id === p.library_id);
        if (!lib) throw Error("媒体库不存在");
        const keys = p.keys as string[];
        if (
          !Array.isArray(keys) ||
          !keys.length ||
          keys.length > 100 ||
          new Set(keys).size !== keys.length
        )
          throw Error("每次选择 1–100 位不同博主");
        const available = new Set(
          state.tasks
            .filter((t) =>
              t.media.some(
                (m) =>
                  m.output.toLowerCase() === lib.path.toLowerCase() &&
                  m.state === "completed",
              ),
            )
            .map((t) => "handle:" + t.handle.normalize("NFKC").toLowerCase()),
        );
        if (keys.some((k) => !available.has(k)))
          throw Error("找不到该库中的已保存博主");
        if (
          [p.starred, p.in_other].some(
            (v) => v !== undefined && typeof v !== "boolean",
          )
        )
          throw Error("博主偏好必须为布尔值");
        const before = keys.map(
          (key) =>
            authorPreferences[lib.id]?.[key] || {
              key,
              starred: false,
              in_other: false,
            },
        );
        const restore = p.restore as AuthorPreference[] | undefined;
        if (
          restore &&
          (!Array.isArray(restore) ||
            restore.length !== keys.length ||
            new Set(restore.map((r) => r.key)).size !== keys.length ||
            restore.some(
              (r) =>
                !keys.includes(r.key) ||
                typeof r.starred !== "boolean" ||
                typeof r.in_other !== "boolean",
            ))
        )
          throw Error("恢复数据无效");
        if (!restore && p.starred === undefined && p.in_other === undefined)
          throw Error("缺少偏好变更");
        authorPreferences[lib.id] ||= {};
        before.forEach((old) => {
          authorPreferences[lib.id][old.key] = restore?.find(
            (r) => r.key === old.key,
          ) || {
            ...old,
            ...(p.starred === undefined
              ? {}
              : { starred: p.starred as boolean }),
            ...(p.in_other === undefined
              ? {}
              : { in_other: p.in_other as boolean }),
          };
        });
        data = before;
        persist();
      } else if (action === "tags_set") {
        const task = state.tasks.find((t) => t.id === p.id);
        if (!task) throw Error("推文不存在");
        const tags = (p.tags as string[]).map((v) =>
          v.normalize("NFKC").replace(/^#/, ""),
        );
        if (
          tags.length > 20 ||
          tags.some((v) => !/^[-_\p{L}\p{N}]{1,32}$/u.test(v))
        )
          throw Error("标签须为 1–32 个字母、数字、下划线或连字符，最多 20 个");
        task.tags = [
          ...new Map(tags.map((v) => [v.toLowerCase(), v])).values(),
        ];
        persist();
      } else if (action === "tag_rename" || action === "tag_delete") {
        const name = String(p.old || p.name).toLowerCase();
        const replacement = String(p.new || "")
          .normalize("NFKC")
          .replace(/^#/, "");
        if (
          action === "tag_rename" &&
          !/^[-_\p{L}\p{N}]{1,32}$/u.test(replacement)
        )
          throw Error("标签格式无效");
        state.tasks.forEach((t) => {
          t.tags = [
            ...new Map(
              (t.tags || [])
                .flatMap((v) =>
                  v.toLowerCase() === name
                    ? action === "tag_delete"
                      ? []
                      : [replacement]
                    : [v],
                )
                .map((v) => [v.toLowerCase(), v]),
            ).values(),
          ];
        });
        persist();
      } else if (action === "library_cover") {
        const lib = state.libraries.find((l) => l.id === p.id);
        if (!lib) throw Error("媒体库不存在");
        if (p.kind === "image")
          throw Error("本地图片导入请在桌面版使用原生文件选择器");
        if (p.kind === "identity") {
          if (!validEmoji(String(p.value || "")))
            throw Error("请选择一个 emoji 标识");
          lib.emoji = String(p.value).trim();
        } else if (p.kind === "reset") lib.cover = null;
        else {
          if (!validEmoji(String(p.value || "")))
            throw Error("请选择一个 emoji 标识");
          lib.emoji = String(p.value).trim();
          lib.cover = { kind: "emoji", value: lib.emoji };
        }
        persist();
      } else if (action === "cover" || action === "avatar") {
        data = null;
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
        if (!validEmoji(String(p.emoji || "📁")))
          throw Error("请选择一个 emoji 标识");
        if (action === "library_add")
          state.libraries.push({
            id: String(Date.now()),
            emoji: String(p.emoji || "📁").trim(),
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
                  emoji: String(p.emoji || "📁").trim(),
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
        const target = state.libraries.find(
          (l) => l.id === (p.library_id || state.default_library),
        )!;
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
            m.output = target.path;
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
              .flatMap((t) =>
                t.media
                  .filter(
                    (m) => m.output === target.path && m.state !== "completed",
                  )
                  .map((m) => m.id),
              ),
          );
          state.operation = {
            run_id: String(Date.now()),
            phase: "reading",
            reading: true,
            discovered_tweets: state.tasks.filter((t) =>
              t.media.some((m) => active.has(m.id)),
            ).length,
            discovered_media: active.size,
            queued: active.size,
            processed: 0,
            successful: 0,
            failed: 0,
            skipped: 0,
            total: null,
            message: "正在读取模拟任务；不访问真实账号",
          };
        }
      } else if (action === "pause") {
        state.busy = false;
        if (state.operation)
          state.operation = {
            ...state.operation,
            phase: "paused",
            reading: false,
            message: "已暂停，下载进度已保留",
          };
        state.tasks.forEach((t) =>
          t.media.forEach((m) => {
            if (m.state === "downloading") m.state = "paused";
          }),
        );
        events.push({ type: "finished" });
      } else if (action === "poll") {
        if (state.busy) {
          const operation = state.operation!;
          if (operation.reading) {
            operation.reading = false;
            operation.phase = "downloading";
            operation.total = operation.queued;
            operation.message = "读取结束，正在处理本次任务";
          }
          for (const t of state.tasks) {
            if (t.ignored || veto.has(t.id))
              t.media.forEach((m) => {
                if (
                  active.has(m.id) &&
                  m.state !== "downloading" &&
                  m.state !== "completed"
                ) {
                  active.delete(m.id);
                  operation.skipped++;
                  operation.processed++;
                }
              });
            const m = t.media.find(
              (m) => active.has(m.id) && m.state !== "completed",
            );
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
            if (m.state === "completed") {
              operation.processed++;
              operation.successful++;
              events.push({ type: "changed" });
            }
          }
        }
        if (
          state.busy &&
          state.operation &&
          state.operation.processed >= (state.operation.total ?? Infinity)
        ) {
          state.busy = false;
          state.operation.phase = "completed";
          state.operation.message = "本轮处理结束";
          events.push({ type: "finished" });
        }
        data = {
          events: events
            .splice(0)
            .map((e) =>
              state.operation ? { ...e, run_id: state.operation.run_id } : e,
            ),
          busy: state.busy,
          operation: state.operation,
        };
      } else if (action === "preview") data = null;
      else if (action === "demo_reset") {
        state = initial();
        authorPreferences = {};
        veto = new Set();
        active = new Set();
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
