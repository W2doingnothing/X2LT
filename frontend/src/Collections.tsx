import { useState, useEffect, useRef } from "react";
import type { Library, Tweet, Operation } from "./types";
import { call } from "./api";
import { bytes, type Action } from "./Preferences";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { EmojiPicker, validEmoji } from "./EmojiPicker";
import { Plus } from "lucide-react";
import { ProtectedImage } from "./Privacy";
import "./collections.css";
let workers = 0;
const jobs: Array<() => void> = [];
export function enqueuePreview<T>(work: () => Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const run = () => {
      workers++;
      work()
        .then(resolve, reject)
        .finally(() => {
          workers--;
          jobs.shift()?.();
        });
    };
    if (workers < 2) run();
    else jobs.push(run);
  });
}
const avatars = new Map<string, string | null>();
const avatarPending = new Map<string, Promise<string | null>>();
export function Avatar({
  tweet,
}: {
  tweet: Pick<
    Tweet,
    | "id"
    | "platform"
    | "handle"
    | "author"
    | "display_name"
    | "avatar_available"
  >;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [image, setImage] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const element = ref.current;
    if (!element || !tweet.avatar_available) return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries[0].isIntersecting) return;
      observer.disconnect();
      const key =
        (tweet.platform || "x") +
        ":" +
        (tweet.handle || tweet.id).toLowerCase();
      void enqueuePreview(async () => {
        if (!alive) return;
        let result = avatars.get(key);
        if (result === undefined) {
          let pending = avatarPending.get(key);
          if (!pending) {
            pending = call<string | null>("avatar", { id: tweet.id });
            avatarPending.set(key, pending);
          }
          try {
            result = await pending;
          } finally {
            avatarPending.delete(key);
          }
          avatars.set(key, result);
          if (avatars.size > 200) avatars.delete(avatars.keys().next().value!);
        }
        if (alive) setImage(result || null);
      }).catch(() => null);
    });
    observer.observe(element);
    return () => {
      alive = false;
      observer.disconnect();
    };
  }, [tweet.id, tweet.avatar_available]);
  return (
    <div ref={ref} className="avatar">
      {image ? (
        <img
          src={image}
          alt={tweet.display_name || tweet.author}
          onError={() => setImage(null)}
        />
      ) : (
        (tweet.display_name || tweet.author || "?").slice(0, 1)
      )}
    </div>
  );
}
export function Tags({
  tweet,
  options,
  action,
  onFilter,
}: {
  tweet: Tweet;
  options: { name: string; count: number }[];
  action: Action;
  onFilter: (tag: string) => void;
}) {
  const [value, setValue] = useState("");
  const tags = tweet.tags || [];
  async function add() {
    if (!value.trim()) return;
    if (
      await action(
        "tags_set",
        { id: tweet.id, tags: [...tags, value.trim()] },
        "标签已保存",
      )
    )
      setValue("");
  }
  return (
    <aside className="message-tags">
      <small>标签</small>
      <div className="tag-chips">
        {tags.map((tag) => (
          <span className="tag-chip" key={tag}>
            <button onClick={() => onFilter(tag)}>#{tag}</button>
            <button
              aria-label={`移除标签 ${tag}`}
              onClick={async () => {
                if (
                  await action(
                    "tags_set",
                    { id: tweet.id, tags: tags.filter((t) => t !== tag) },
                    "标签已移除",
                  )
                ) {
                }
              }}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <input
        aria-label={`添加标签 ${tweet.id}`}
        value={value}
        placeholder="＋ 添加标签"
        list={"tags-" + tweet.id}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            void add();
          }
        }}
      />
      <datalist id={"tags-" + tweet.id}>
        {options
          .filter((t) => !tags.includes(t.name))
          .map((t) => (
            <option key={t.name} value={t.name} />
          ))}
      </datalist>
      {value && (
        <button className="text-btn" onClick={add}>
          添加
        </button>
      )}
    </aside>
  );
}
export function TagFilters({
  options,
  selected,
  untagged,
  onChange,
  onUntagged,
  action,
}: {
  options: { name: string; count: number }[];
  selected: string[];
  untagged: boolean;
  onChange: (tags: string[]) => void;
  onUntagged: (v: boolean) => void;
  action: Action;
}) {
  const [manage, setManage] = useState(false),
    [old, setOld] = useState(""),
    [next, setNext] = useState(""),
    [deleting, setDeleting] = useState(false);
  return (
    <>
      <div className="tag-filters">
        <button aria-pressed={untagged} onClick={() => onUntagged(!untagged)}>
          未打标签
        </button>
        {options.map((t) => (
          <button
            key={t.name}
            aria-label={"筛选标签 " + t.name}
            aria-pressed={selected.includes(t.name)}
            onClick={() =>
              onChange(
                selected.includes(t.name)
                  ? selected.filter((x) => x !== t.name)
                  : [...selected, t.name],
              )
            }
          >
            #{t.name} <small>{t.count}</small>
          </button>
        ))}
        {!!(selected.length || untagged) && (
          <button
            onClick={() => {
              onChange([]);
              onUntagged(false);
            }}
          >
            清除标签筛选
          </button>
        )}
        <button onClick={() => setManage(true)}>管理标签</button>
      </div>
      {manage && (
        <Modal open title="管理标签" onClose={() => setManage(false)}>
          <p>修改将应用于所有媒体库中的同一标签。</p>
          <label>
            原标签
            <select
              aria-label="管理原标签"
              value={old}
              onChange={(e) => setOld(e.target.value)}
            >
              <option value="">选择标签</option>
              {options.map((t) => (
                <option key={t.name}>{t.name}</option>
              ))}
            </select>
          </label>
          <input
            aria-label="新标签名称"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            placeholder="新标签名称"
          />
          <Button
            disabled={!old || !next}
            onClick={async () => {
              if (
                await action("tag_rename", { old, new: next }, "标签已重命名")
              )
                setManage(false);
            }}
          >
            重命名 / 合并
          </Button>
          <Button
            variant="outline"
            disabled={!old}
            onClick={() => setDeleting(true)}
          >
            删除全部关联
          </Button>
          {deleting && (
            <div role="alert">
              <p>确认从所有内容移除 #{old}？媒体文件和下载记录会保留。</p>
              <Button
                onClick={async () => {
                  if (await action("tag_delete", { name: old }, "标签已删除")) {
                    setDeleting(false);
                    setManage(false);
                  }
                }}
              >
                确认删除标签
              </Button>
              <Button variant="outline" onClick={() => setDeleting(false)}>
                取消
              </Button>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
export function LibraryCover({ library }: { library: Library }) {
  const [image, setImage] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setImage(null);
    if (library.cover?.kind === "image")
      void enqueuePreview(() =>
        call<string | null>("cover", { id: library.id }),
      )
        .then((v) => {
          if (alive) setImage(v);
        })
        .catch(() => null);
    return () => {
      alive = false;
    };
  }, [library.id, library.cover?.kind, library.cover?.value]);
  return (
    <div className="collection-cover">
      {image ? (
        <ProtectedImage src={image} alt={library.name + "封面"} />
      ) : library.cover?.kind === "emoji" ? (
        library.emoji || library.cover.value
      ) : (
        library.emoji || "📁"
      )}
    </div>
  );
}
export function Collections({
  libraries,
  onEnter,
  onAdd,
  action,
}: {
  libraries: Library[];
  onEnter: (id: string) => void;
  onAdd: () => void;
  action: Action;
}) {
  const [editing, setEditing] = useState<Library | null>(null),
    [emoji, setEmoji] = useState("📁");
  return (
    <section className="collections-panel" aria-label="我的媒体库">
      <div className="collections-heading">
        <h2>我的媒体库</h2>
        <Button
          className="collections-add"
          aria-label="添加媒体库"
          title="添加媒体库"
          onClick={onAdd}
        >
          <Plus size={20} aria-hidden="true" />
        </Button>
      </div>
      <div className="collections-grid">
        {libraries.map((l) => (
          <article className="collection-card" key={l.id}>
            <button
              className="collection-entry"
              aria-label={"进入" + l.name}
              onClick={() => onEnter(l.id)}
            >
              <LibraryCover library={l} />
              <h3>
                {l.emoji || "📁"} {l.name} {l.default && <small>默认库</small>}
              </h3>
            </button>
            <p>
              {l.video_count ?? 0} 个视频 · {l.image_count ?? 0} 张图片
            </p>
            <LibraryCapacity library={l} />
            <Button
              variant="outline"
              aria-label={"封面 " + l.name}
              onClick={() => {
                setEditing(l);
                setEmoji(
                  l.emoji || (l.cover?.kind === "emoji" ? l.cover.value : "📁"),
                );
              }}
            >
              更换封面
            </Button>
          </article>
        ))}
      </div>
      {editing && (
        <Modal open title="媒体库封面" onClose={() => setEditing(null)}>
          <div className="cover-editor">
            <LibraryCover library={{ ...editing, emoji }} />
            <EmojiPicker value={emoji} onChange={setEmoji} />
            <div className="cover-actions">
              <Button
                disabled={!validEmoji(emoji)}
                onClick={async () => {
                  if (
                    await action("library_cover", {
                      id: editing.id,
                      kind: "identity",
                      value: emoji.trim(),
                    })
                  )
                    setEditing(null);
                }}
              >
                保存标识
              </Button>
              <Button
                variant="outline"
                disabled={!validEmoji(emoji)}
                onClick={async () => {
                  if (
                    await action("library_cover", {
                      id: editing.id,
                      kind: "image",
                      emoji: emoji.trim(),
                    })
                  )
                    setEditing(null);
                }}
              >
                导入本地图片
              </Button>
              <Button
                variant="outline"
                onClick={async () => {
                  if (
                    await action("library_cover", {
                      id: editing.id,
                      kind: "reset",
                    })
                  )
                    setEditing(null);
                }}
              >
                重置图片
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
export function OperationStatus({
  operation,
  onPause,
  onOpen,
}: {
  operation?: Operation | null;
  onPause: () => void;
  onOpen: () => void;
}) {
  if (!operation) return null;
  const labels = {
    reading: "正在读取",
    downloading: "正在下载",
    paused: "已暂停",
    completed: "本轮处理结束",
    failed: "操作失败",
  };
  const running = ["reading", "downloading"].includes(operation.phase);
  return (
    <section
      className={
        "operation-status " +
        (running && operation.total === null ? "unknown" : "")
      }
      aria-live="polite"
    >
      <div>
        <strong>{labels[operation.phase]}</strong>{" "}
        {operation.reading && <span>读取中 · </span>}已发现{" "}
        {operation.discovered_tweets} 条内容 / {operation.discovered_media}{" "}
        个媒体 <span>已入队 {operation.queued}</span>
      </div>
      <div>
        成功 {operation.successful} · 失败 {operation.failed} · 已跳过{" "}
        {operation.skipped}
        {operation.total !== null && (
          <>
            {" "}
            · 已处理 {operation.processed} / {operation.total}
          </>
        )}
      </div>
      {operation.total !== null && (
        <progress max={operation.total || 1} value={operation.processed} />
      )}
      <p>{operation.message}</p>
      <button onClick={onOpen}>查看任务</button>
      {running && <button onClick={onPause}>暂停</button>}
    </section>
  );
}

export function LibraryCapacity({ library: l }: { library: Library }) {
  const known =
    l.online &&
    (l.quota_bytes > 0
      ? l.used_bytes !== undefined
      : l.disk_total !== undefined && l.disk_free !== undefined);
  const max = l.quota_bytes || l.disk_total || 1;
  const used = l.quota_bytes
    ? l.used_bytes || 0
    : Math.max(0, (l.disk_total || 0) - (l.disk_free || 0));
  return (
    <div className="library-capacity">
      <div>
        {l.quota_bytes ? "媒体库配额" : "磁盘空间"}
        <span>{known ? `${bytes(used)} / ${bytes(max)}` : "未知"}</span>
      </div>
      <progress
        aria-label={l.name + (l.quota_bytes ? "配额使用" : "磁盘空间使用")}
        className={known ? "" : "unknown"}
        max={max}
        value={known ? used : 0}
      />
      <small>
        本库 {l.used_bytes === undefined ? "未知" : bytes(l.used_bytes)} ·{" "}
        {l.online
          ? "可用 " +
            (l.available_bytes === undefined
              ? "未知"
              : bytes(l.available_bytes))
          : "离线"}
      </small>
    </div>
  );
}
