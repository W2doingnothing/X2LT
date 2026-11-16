import { useEffect, useRef, useState } from "react";
import { Image, Film, FolderOpen } from "lucide-react";
import { call, desktop } from "./api";
import type { Library, Media, Resource, ResourcePage } from "./types";
import { enqueuePreview } from "./Collections";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { PrivateText, usePrivateReveal } from "./Privacy";
import { art } from "./art";
import { bytes } from "./Preferences";
import "./resources.css";
export type ResourceKind = "all" | "video" | "image";
type Overview = ResourcePage & { video_count: number; image_count: number };
const previews = new Map<number, string>();
function ResourceTile({
  resource,
  media,
  onOpen,
}: {
  resource: Resource;
  media: Media;
  onOpen: () => void;
}) {
  const [image, setImage] = useState<string | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  const { hidden, reveal } = usePrivateReveal(resource.key + media.id);
  useEffect(() => {
    let alive = true;
    setImage(null);
    if (!desktop) return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries[0].isIntersecting) return;
      observer.disconnect();
      void enqueuePreview(async () => {
        if (!alive) return;
        const value =
          previews.get(media.id) ||
          (await call<string | null>("preview", { job_id: media.id }));
        if (value) {
          previews.set(media.id, value);
          if (previews.size > 200)
            previews.delete(previews.keys().next().value!);
        }
        if (alive) setImage(value || null);
      }).catch(() => null);
    });
    if (ref.current) observer.observe(ref.current);
    return () => {
      alive = false;
      observer.disconnect();
    };
  }, [media.id]);
  return (
    <button
      ref={ref}
      className="resource-preview"
      aria-label={(hidden ? "显示资源 " : "打开资源 ") + resource.media_id}
      onClick={() => (hidden ? reveal() : onOpen())}
    >
      {image || !desktop ? (
        <img
          className={hidden ? "privacy-blurred" : ""}
          src={image || art("gold", media.id % 3)}
          alt={hidden ? "已模糊的媒体" : "媒体预览"}
        />
      ) : (
        <FolderOpen size={28} />
      )}
      {hidden && (
        <>
          <span className="privacy-shield" />
          <span className="privacy-hint">点击显示</span>
        </>
      )}
      <span className="resource-badge">
        {resource.media_type === "video" ? (
          <Film size={12} />
        ) : (
          <Image size={12} />
        )}{" "}
        {media.extension.toUpperCase()}
      </span>
    </button>
  );
}
export function ResourceBrowser({
  libraries,
  kind,
  onKind,
  onLibraries,
  options,
  revision,
  active,
}: {
  libraries: Library[];
  kind: ResourceKind | null;
  onKind: (kind: ResourceKind | null) => void;
  onLibraries: () => void;
  options: { name: string; count: number }[];
  revision: string;
  active: boolean;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [page, setPage] = useState<ResourcePage>({
    resources: [],
    total: 0,
    offset: 0,
  });
  const [query, setQuery] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<Resource | null>(null);
  const [location, setLocation] = useState(0);
  const request = useRef(0);
  useEffect(() => {
    if (!active) setDetail(null);
  }, [active]);
  useEffect(() => {
    if (!active) return;
    const version = ++request.current;
    setError("");
    const timer = setTimeout(() => {
      const work =
        kind === null
          ? call<Overview>("resource_overview")
          : call<ResourcePage>("resources", { kind, query, tags, offset });
      void work
        .then((result) => {
          if (version !== request.current) return;
          if (kind === null) setOverview(result as Overview);
          else {
            setPage(result);
            if (result.offset !== offset) setOffset(result.offset);
          }
        })
        .catch((e) => {
          if (version === request.current) setError(e.message);
        });
    }, 100);
    return () => {
      clearTimeout(timer);
      request.current++;
    };
  }, [active, kind, query, tags, offset, revision]);
  const preferred = (r: Resource) =>
    r.locations.find((m) =>
      libraries.some(
        (l) => l.online && l.path.toLowerCase() === m.output.toLowerCase(),
      ),
    ) || r.locations[0];
  function open(r: Resource) {
    setDetail(r);
    setLocation(Math.max(0, r.locations.indexOf(preferred(r))));
  }
  const lastListKind = useRef<ResourceKind | null>(null);
  function filter(k: ResourceKind) {
    if (lastListKind.current !== k) setOffset(0);
    lastListKind.current = k;
    onKind(k);
  }
  async function action(name: string, payload: Record<string, unknown>) {
    try {
      await call(name, payload);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const rows = kind === null ? overview?.resources || [] : page.resources;
  const media = detail?.locations[location];
  return (
    <section
      className="resource-browser"
      aria-label={kind === null ? "收藏概览" : "所有资源"}
    >
      {kind === null ? (
        <>
          <div className="resource-stats">
            <button onClick={onLibraries}>
              <span>媒体库</span>
              <strong>{libraries.length}</strong>
            </button>
            <button onClick={() => filter("video")}>
              <span>视频</span>
              <strong>{overview?.video_count ?? "—"}</strong>
            </button>
            <button onClick={() => filter("image")}>
              <span>图片</span>
              <strong>{overview?.image_count ?? "—"}</strong>
            </button>
            <div>
              <span>本地占用</span>
              <strong>
                {libraries.some((l) => l.used_bytes === undefined)
                  ? "未知"
                  : bytes(
                      libraries.reduce((n, l) => n + (l.used_bytes || 0), 0),
                    )}
              </strong>
            </div>
          </div>
          <div className="resource-heading">
            <h2>最近添加</h2>
            <Button variant="outline" onClick={() => filter("all")}>
              查看全部
            </Button>
          </div>
        </>
      ) : (
        <>
          <div className="library-breadcrumb">
            <button onClick={() => onKind(null)}>总览</button>
            <span>/ 所有资源</span>
          </div>
          <div className="list-toolbar">
            <div className="tabs">
              {(
                [
                  ["all", "全部"],
                  ["video", "视频"],
                  ["image", "图片"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  className={kind === key ? "active" : ""}
                  onClick={() => filter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <input
              aria-label="搜索资源"
              placeholder="搜索内容、文件名…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          <div className="tag-filters">
            {options.map((t) => (
              <button
                key={t.name}
                aria-pressed={tags.includes(t.name)}
                onClick={() => {
                  setTags(
                    tags.includes(t.name)
                      ? tags.filter((v) => v !== t.name)
                      : [...tags, t.name],
                  );
                  setOffset(0);
                }}
              >
                #{t.name}
              </button>
            ))}
            {!!tags.length && (
              <button onClick={() => setTags([])}>清除标签筛选</button>
            )}
          </div>
          <p>{page.total} 个资源</p>
        </>
      )}
      {error && (
        <div role="alert" className="error-banner">
          {error}
        </div>
      )}
      <div className={kind === null ? "recent-resources" : "resource-grid"}>
        {rows.map((r) => (
          <article className="resource-card" key={r.key}>
            <ResourceTile
              resource={r}
              media={preferred(r)}
              onOpen={() => open(r)}
            />
            <PrivateText text={r.title} compact onOpen={() => open(r)} />
            <small>
              {r.source_label} · {r.media_type === "video" ? "视频" : "图片"}
              {r.locations.length > 1
                ? ` · ${r.locations.length} 个位置/版本`
                : ""}
            </small>
          </article>
        ))}
      </div>
      {!rows.length && (
        <div className="empty">
          <FolderOpen size={30} />
          <h3>暂无资源</h3>
        </div>
      )}
      {kind !== null && page.total > 50 && (
        <div className="pagination">
          <Button
            variant="outline"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            上一页
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + 50, page.total)} / {page.total}
          </span>
          <Button
            variant="outline"
            disabled={offset + 50 >= page.total}
            onClick={() => setOffset(offset + 50)}
          >
            下一页
          </Button>
        </div>
      )}
      <Modal
        open={!!detail}
        title="资源详情"
        onClose={() => setDetail(null)}
        drawer
      >
        {detail && media && (
          <div className="resource-details">
            {error && (
              <div className="error-banner" role="alert">
                {error}
              </div>
            )}
            <ResourceTile
              resource={detail}
              media={media}
              onOpen={() => void action("open_media", { job_id: media.id })}
            />
            <PrivateText text={detail.title} collapsible />
            <p>
              {detail.source_label}
              {detail.author ? " · " + detail.author : ""}
            </p>
            <p>
              添加时间：
              {detail.added_at
                ? new Date(detail.added_at * 1000).toLocaleString()
                : "未知"}
            </p>
            <label>
              位置与版本
              <select
                aria-label="资源位置与版本"
                value={location}
                onChange={(e) => setLocation(Number(e.target.value))}
              >
                {detail.locations.map((m, i) => {
                  const lib = libraries.find(
                    (l) => l.path.toLowerCase() === m.output.toLowerCase(),
                  );
                  return (
                    <option key={m.id} value={i}>
                      {lib?.emoji || "📁"} {lib?.name || "媒体库"}
                      {lib?.online ? "" : "（离线）"} · {m.quality} · {m.width}×
                      {m.height} · {bytes(m.bytes)}
                    </option>
                  );
                })}
              </select>
            </label>
            <p>{media.filename}</p>
            <p>{detail.tags.map((t) => "#" + t).join(" ")}</p>
            <div className="cover-actions">
              <Button
                onClick={() => void action("open_media", { job_id: media.id })}
              >
                打开媒体
              </Button>
              <Button
                variant="outline"
                onClick={() => void action("open_folder", { job_id: media.id })}
              >
                打开文件夹
              </Button>
              {detail.platform === "x" && (
                <Button
                  variant="outline"
                  onClick={() =>
                    void action("open_tweet", { id: detail.message_id })
                  }
                >
                  查看来源
                </Button>
              )}
            </div>
          </div>
        )}
      </Modal>
    </section>
  );
}
