import {
  BookOpen,
  Film,
  FolderOpen,
  Image,
  MoreHorizontal,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { call } from "./api";
import { enqueuePreview } from "./Collections";
import { ComicReader } from "./ComicReader";
import { bytes } from "./Preferences";
import { PrivateText, usePrivateReveal } from "./Privacy";
import type { Library, Payload } from "./types";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import "./local-library.css";

type Kind = "image" | "video" | "comic";
type Location = {
  id: number;
  state: "available" | "offline" | "missing";
  managed: boolean;
  recyclable?: boolean;
  label: string;
};
type Entry = {
  id: number;
  title: string;
  kind: Kind;
  bytes: number;
  tags: string[];
  libraries: string[];
  locations: Location[];
};
type LocalResults = { items: Entry[]; total: number; offset: number };
type ImportKind = "media" | "comic";
type ImportSelection = { id: string; names: string[] };
type ImportJob = {
  id: string;
  stage:
    "scanning" | "preview" | "importing" | "completed" | "failed" | "cancelled";
  total: number;
  done: number;
  bytes: number;
  copy_bytes?: number;
  duplicate: number;
  failed: number;
  retryable?: number;
  items: {
    id: string;
    title: string;
    kind: Kind;
    bytes: number;
    duplicate: boolean;
  }[];
  error?: string;
  failures?: { id: string; title: string; error: string }[];
};

const kindLabel = { image: "图片", video: "视频", comic: "漫画" };
const stageLabel = {
  scanning: "正在扫描",
  preview: "确认导入",
  importing: "正在导入",
  completed: "导入完成",
  failed: "导入失败",
  cancelled: "已取消",
};
const locationLabel = {
  available: "可用",
  offline: "离线",
  missing: "文件缺失",
};
const pageSize = 24;

export function LocalImportDialog({
  library,
  close,
  changed,
}: {
  library: Pick<Library, "id" | "name" | "online">;
  close: () => void;
  changed: () => void;
}) {
  const [kind, setKind] = useState<ImportKind>("media");
  const [selectionMode, setSelectionMode] = useState<"files" | "folder">(
    "files",
  );
  const [picked, setPicked] = useState<ImportSelection | null>(null);
  const [storage, setStorage] = useState<"reference" | "copy">("reference");
  const [recursive, setRecursive] = useState(true);
  const [batch, setBatch] = useState(false);
  const [id, setId] = useState<string | null>(null);
  const [status, setStatus] = useState<ImportJob | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const didImport = useRef(false);
  const latestChanged = useRef(changed);
  latestChanged.current = changed;
  const stage = status?.stage;
  const running =
    !!id && (!stage || stage === "scanning" || stage === "importing");
  const candidateCount = status ? Math.max(0, status.total - status.failed) : 0;
  const fullPreview = !!status && status.items.length >= candidateCount;
  const canRetry =
    !!status &&
    (status.retryable === undefined
      ? stage === "failed" || stage === "cancelled" || status.failed > 0
      : status.retryable > 0);
  const chosen =
    status?.items.filter((item) => !excluded.includes(item.id)) ?? [];

  useEffect(() => {
    if (!id || !running) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const current = await call<ImportJob>("local_import_status", { id });
        if (!alive) return;
        setPollError("");
        setStatus(current);
        if (current.stage === "scanning" || current.stage === "importing") {
          timer = setTimeout(poll, 350);
        } else if (didImport.current) {
          latestChanged.current();
          didImport.current = false;
        }
      } catch (reason) {
        if (!alive) return;
        setPollError(String(reason));
        timer = setTimeout(poll, 1200);
      }
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id, running]);

  async function perform(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  function updateSelection(
    nextKind: ImportKind,
    nextSelection: "files" | "folder",
  ) {
    setKind(nextKind);
    setSelectionMode(nextSelection);
    setPicked(null);
    setError("");
  }
  function backToOptions() {
    setId(null);
    setStatus(null);
    setExcluded([]);
    setError("");
    setPollError("");
  }
  function openPicker() {
    void perform(async () => {
      const choice = await call<ImportSelection | null>("local_pick", {
        kind,
        selection: selectionMode,
      });
      if (choice) setPicked(choice);
    });
  }
  function scan() {
    if (!picked) return;
    void perform(async () => {
      const result = await call<{ id: string }>("local_scan", {
        selection: picked.id,
        library_id: library.id,
        kind,
        storage,
        recursive: selectionMode === "folder" && recursive,
        batch: kind === "comic" && selectionMode === "folder" && batch,
      });
      setId(result.id);
    });
  }
  function start(retry = false, itemId?: string) {
    if (!status) return;
    void perform(async () => {
      const accepted = await call<boolean>(
        retry ? "local_import_retry" : "local_import_start",
        {
          id: status.id,
          ...(!retry && fullPreview
            ? { ids: chosen.map((item) => item.id) }
            : {}),
          ...(itemId ? { item_id: itemId } : {}),
        },
      );
      if (!accepted) throw Error("无法继续此导入任务");
      didImport.current = true;
      setStatus({ ...status, stage: "importing", done: 0, error: undefined });
    });
  }
  const title = "导入到 " + library.name;
  return (
    <Modal
      open
      onClose={() => {
        if (!running && !busy) close();
      }}
      title={title}
    >
      <div className="local-dialog">
        {!id ? (
          <>
            <div className="local-actions">
              <Button
                disabled={busy}
                variant={kind === "media" ? "default" : "outline"}
                onClick={() => updateSelection("media", "files")}
              >
                普通媒体
              </Button>
              <Button
                disabled={busy}
                variant={kind === "comic" ? "default" : "outline"}
                onClick={() => updateSelection("comic", "folder")}
              >
                漫画
              </Button>
            </div>
            <label>
              导入来源
              <select
                disabled={busy}
                value={selectionMode}
                onChange={(e) =>
                  updateSelection(kind, e.target.value as "files" | "folder")
                }
              >
                <option value="files">
                  {kind === "comic" ? "ZIP / CBZ 压缩包" : "图片和视频文件"}
                </option>
                <option value="folder">文件夹</option>
              </select>
            </label>
            {selectionMode === "folder" && (
              <label className="local-check">
                <input
                  type="checkbox"
                  checked={recursive}
                  disabled={busy}
                  onChange={(e) => setRecursive(e.target.checked)}
                />
                扫描子文件夹
              </label>
            )}
            {kind === "comic" && selectionMode === "folder" && (
              <label className="local-check">
                <input
                  type="checkbox"
                  checked={batch}
                  disabled={busy}
                  onChange={(e) => setBatch(e.target.checked)}
                />
                批量导入其中的漫画文件夹及压缩包
              </label>
            )}
            <label>
              保存方式
              <select
                disabled={busy}
                value={storage}
                onChange={(e) =>
                  setStorage(e.target.value as "reference" | "copy")
                }
              >
                <option value="reference">引用原文件</option>
                <option value="copy" disabled={!library.online}>
                  复制到媒体库
                </option>
              </select>
            </label>
            <div className="local-actions">
              <Button variant="outline" disabled={busy} onClick={openPicker}>
                选择{selectionMode === "folder" ? "文件夹" : "文件"}
              </Button>
              {picked && (
                <PrivateText
                  compact
                  text={
                    picked.names.length > 1
                      ? `已选 ${picked.names.length} 个文件`
                      : picked.names[0] || "已选择"
                  }
                />
              )}
            </div>
            <div className="local-actions">
              <Button disabled={!picked || busy} onClick={scan}>
                扫描
              </Button>
            </div>
          </>
        ) : (
          <>
            <strong role="status">
              {stage ? stageLabel[stage] : stageLabel.scanning}
            </strong>
            {status && (
              <div className="local-actions local-summary">
                <span>
                  {stage === "preview"
                    ? `${candidateCount} 项候选`
                    : `${status.done} / ${status.total} 项`}
                </span>
                <span>{bytes(status.bytes)}</span>
                {stage === "preview" &&
                  storage === "copy" &&
                  status.copy_bytes !== undefined && (
                    <span>预计占用 {bytes(status.copy_bytes)}</span>
                  )}
                {!!status.duplicate && <span>{status.duplicate} 项重复</span>}
                {!!status.failed && <span>{status.failed} 项失败</span>}
              </div>
            )}
            {running && (
              <progress
                aria-label={stage ? stageLabel[stage] : stageLabel.scanning}
                max={status?.total || 1}
                value={status?.total ? status.done : undefined}
              />
            )}
            {stage === "preview" && status && (
              <ul className="local-import-list">
                {status.items.map((item) => (
                  <li key={item.id}>
                    {fullPreview && (
                      <input
                        type="checkbox"
                        aria-label="选择此候选项"
                        checked={!excluded.includes(item.id)}
                        onChange={(e) =>
                          setExcluded((previous) =>
                            e.target.checked
                              ? previous.filter((value) => value !== item.id)
                              : [...previous, item.id],
                          )
                        }
                      />
                    )}
                    <PrivateText compact text={item.title} />
                    <span>
                      {kindLabel[item.kind] || item.kind} · {bytes(item.bytes)}
                      {item.duplicate ? " · 重复" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {!!status?.failures?.length && (
              <ul className="local-import-list">
                {status.failures.map((item, index) => (
                  <li key={item.id + ":" + index}>
                    <PrivateText compact text={item.title} />
                    <span>{item.error}</span>
                    {!running && stage !== "preview" && item.id && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => start(true, item.id)}
                      >
                        重试此项
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="local-actions">
              {stage === "preview" && status && (
                <Button
                  disabled={
                    busy || !candidateCount || (fullPreview && !chosen.length)
                  }
                  onClick={() => start()}
                >
                  导入{fullPreview ? ` ${chosen.length} 项` : ""}
                </Button>
              )}
              {running && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      await call("local_import_cancel", { id });
                    })
                  }
                >
                  取消任务
                </Button>
              )}
              {!running && stage !== "preview" && canRetry && (
                <Button disabled={busy} onClick={() => start(true)}>
                  {stage === "cancelled" ? "继续任务" : "重试失败项"}
                </Button>
              )}
              {!running && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={backToOptions}
                >
                  返回导入选项
                </Button>
              )}
              {!running && stage !== "preview" && (
                <Button variant="outline" disabled={busy} onClick={close}>
                  关闭
                </Button>
              )}
            </div>
          </>
        )}
        {(error || pollError || status?.error) && (
          <p className="local-error" role="alert">
            {error || pollError || status?.error}
          </p>
        )}
      </div>
    </Modal>
  );
}

function LocalCard({
  item,
  revision,
  open,
  details,
}: {
  item: Entry;
  revision: number;
  open: () => void;
  details: () => void;
}) {
  const preview = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const [image, setImage] = useState("");
  const { hidden, reveal } = usePrivateReveal(`local-${item.id}`);
  const available = item.locations.some(
    (location) => location.state === "available",
  );
  const Icon =
    item.kind === "video" ? Film : item.kind === "comic" ? BookOpen : Image;
  useEffect(() => {
    const node = preview.current;
    if (!node) return;
    if (!window.IntersectionObserver) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([result]) => {
        if (result.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "250px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible || !available) {
      setImage("");
      return;
    }
    let alive = true;
    void enqueuePreview(() =>
      alive
        ? call<string | null>("local_media_preview", { id: item.id })
        : Promise.resolve(null),
    )
      .then((src) => {
        if (alive) setImage(src || "");
      })
      .catch(() => {
        if (alive) setImage("");
      });
    return () => {
      alive = false;
    };
  }, [visible, available, item.id, revision]);
  return (
    <article className="local-card">
      <button
        ref={preview}
        className="local-preview"
        aria-label={hidden ? "显示预览" : kindLabel[item.kind] + " · 打开"}
        onClick={() => (hidden ? reveal() : available ? open() : details())}
      >
        {image ? (
          <img
            src={image}
            alt=""
            className={hidden ? "privacy-blurred" : ""}
            onError={() => setImage("")}
          />
        ) : (
          <Icon size={28} />
        )}
        <span className="local-preview-kind">{kindLabel[item.kind]}</span>
        {hidden && <span className="privacy-hint">点击显示</span>}
        {!available && <span className="local-preview-state">不可用</span>}
      </button>
      <div className="local-card-body">
        <div className="local-card-title">
          <PrivateText compact text={item.title} />
        </div>
        <button
          type="button"
          className="icon-btn"
          aria-label="本地媒体详情"
          onClick={details}
        >
          <MoreHorizontal size={17} />
        </button>
        <div className="local-card-meta">
          <span>本地导入</span>
          <span>{bytes(item.bytes)}</span>
          {item.tags.map((tag) => (
            <span key={tag} className="local-tag">
              #{tag.replace(/^#/, "")}
            </span>
          ))}
        </div>
      </div>
    </article>
  );
}

function LocalDetails({
  item,
  libraryId,
  libraries,
  close,
  changed,
}: {
  item: Entry;
  libraryId: string;
  libraries: Library[];
  close: () => void;
  changed: () => void;
}) {
  const [tags, setTags] = useState(item.tags.join("，"));
  const [target, setTarget] = useState("");
  const [confirm, setConfirm] = useState<"membership" | Location | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const { hidden, reveal } = usePrivateReveal(`location-${item.id}`);
  async function perform(
    action: string,
    payload: Payload,
    options: { update?: boolean; close?: boolean; message?: string } = {},
  ) {
    setBusy(true);
    setMessage("");
    try {
      const result = await call(action, payload);
      if (result === null || result === false) return;
      if (options.update) changed();
      if (options.close) close();
      if (options.message) setMessage(options.message);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  }
  function saveTags(e: FormEvent) {
    e.preventDefault();
    const values = tags
      .split(/[，,\n]+/)
      .map((tag) => tag.trim().replace(/^#+/, ""))
      .filter(Boolean);
    void perform(
      "local_media_tags",
      { id: item.id, tags: [...new Set(values)] },
      { update: true, message: "标签已保存" },
    );
  }
  return (
    <Modal open onClose={close} title={kindLabel[item.kind] + "详情"}>
      <div className="local-dialog">
        <div className="local-card-title">
          <PrivateText compact text={item.title} />
        </div>
        <div className="local-actions local-summary">
          <span>本地导入</span>
          <span>{bytes(item.bytes)}</span>
        </div>
        <form className="local-actions" onSubmit={saveTags}>
          <input
            className="local-tags-input"
            aria-label="标签"
            placeholder="标签，用逗号分隔"
            value={tags}
            disabled={busy}
            onChange={(e) => setTags(e.target.value)}
          />
          <Button type="submit" size="sm" variant="outline" disabled={busy}>
            保存标签
          </Button>
        </form>
        <section className="local-detail-section">
          <strong>文件位置</strong>
          {item.locations.map((location) => (
            <div className="local-location" key={location.id}>
              <div className="local-location-description">
                <span>
                  {location.managed ? "库内副本" : "原文件"} ·{" "}
                  {locationLabel[location.state]}
                </span>
                {location.label &&
                  (hidden ? (
                    <button className="privacy-text-toggle" onClick={reveal}>
                      显示位置
                    </button>
                  ) : (
                    <small>{location.label}</small>
                  ))}
              </div>
              <div className="local-actions">
                {location.state === "available" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void perform("local_media_open", {
                        id: item.id,
                        location_id: location.id,
                        folder: true,
                      })
                    }
                  >
                    <FolderOpen size={15} />
                    打开目录
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void perform(
                        "local_media_relocate",
                        { id: item.id, location_id: location.id },
                        { update: true, message: "已更新文件位置" },
                      )
                    }
                  >
                    重新定位
                  </Button>
                )}
                {location.recyclable && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => setConfirm(location)}
                  >
                    移入回收站
                  </Button>
                )}
              </div>
            </div>
          ))}
        </section>
        <section className="local-detail-section">
          <strong>所属媒体库</strong>
          <div className="local-actions local-summary">
            {libraries
              .filter((library) => item.libraries.includes(library.id))
              .map((library) => (
                <span key={library.id}>{library.name}</span>
              ))}
          </div>
          {libraries.some(
            (library) => !item.libraries.includes(library.id),
          ) && (
            <div className="local-actions">
              <select
                aria-label="添加到其他媒体库"
                value={target}
                disabled={busy}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">选择其他媒体库</option>
                {libraries
                  .filter((library) => !item.libraries.includes(library.id))
                  .map((library) => (
                    <option key={library.id} value={library.id}>
                      {library.name}
                    </option>
                  ))}
              </select>
              <Button
                variant="outline"
                size="sm"
                disabled={busy || !target || item.libraries.includes(target)}
                onClick={() =>
                  void perform(
                    "local_media_link",
                    { id: item.id, library_id: target, linked: true },
                    { update: true, message: "已添加到媒体库" },
                  )
                }
              >
                添加引用
              </Button>
            </div>
          )}
        </section>
        {confirm ? (
          <div className="local-confirm" role="group" aria-label="确认移除">
            <p>
              {confirm === "membership"
                ? "从当前媒体库移除此条目？"
                : "将这个库内副本移入系统回收站？"}
            </p>
            <div className="local-actions">
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => setConfirm(null)}
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={busy}
                onClick={() =>
                  void perform(
                    "local_media_remove",
                    {
                      id: item.id,
                      library_id: libraryId,
                      ...(confirm === "membership"
                        ? {}
                        : { location_id: confirm.id }),
                      recycle: confirm !== "membership",
                    },
                    { update: true, close: true },
                  )
                }
              >
                确认
              </Button>
            </div>
          </div>
        ) : (
          <div className="local-actions">
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => setConfirm("membership")}
            >
              从当前媒体库移除
            </Button>
          </div>
        )}
        {message && <p role="status">{message}</p>}
      </div>
    </Modal>
  );
}

function LocalImage({ item, close }: { item: Entry; close: () => void }) {
  const [src, setSrc] = useState<string | null>();
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    void call<string | null>("local_media_preview", {
      id: item.id,
      large: true,
    })
      .then((value) => {
        if (alive) setSrc(value);
      })
      .catch((reason) => {
        if (alive) {
          setError(String(reason));
          setSrc(null);
        }
      });
    return () => {
      alive = false;
    };
  }, [item.id]);
  return (
    <Modal open onClose={close} title="图片预览">
      <div className="local-image">
        {src ? (
          <img src={src} alt="本地图片" onError={() => setSrc(null)} />
        ) : (
          <p role="status">
            {src === undefined ? "正在读取图片…" : error || "无法显示此图片"}
          </p>
        )}
        <div className="local-actions">
          <Button
            variant="outline"
            onClick={() =>
              void call("local_media_open", { id: item.id }).catch((reason) =>
                setError(String(reason)),
              )
            }
          >
            用系统应用打开
          </Button>
          {src && error && <p role="alert">{error}</p>}
        </div>
      </div>
    </Modal>
  );
}

export function LocalLibrary({
  libraryId,
  libraries,
  query,
  tags,
  untagged,
  revision,
  changed,
  onTotal,
  notify,
  standalone,
}: {
  libraryId: string;
  libraries: Library[];
  query: string;
  tags: string[];
  untagged: boolean;
  revision: number;
  changed: () => void;
  onTotal: (count: number) => void;
  notify: (message: string) => void;
  standalone: boolean;
}) {
  const [data, setData] = useState<LocalResults | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [details, setDetails] = useState<Entry | null>(null);
  const [image, setImage] = useState<Entry | null>(null);
  const [comic, setComic] = useState<number | null>(null);
  const list = useRef<HTMLElement>(null);
  const latestTotal = useRef(onTotal);
  latestTotal.current = onTotal;
  const tagKey = JSON.stringify(tags);
  useEffect(() => {
    let alive = true;
    setError("");
    void call<LocalResults | null>("local_library", {
      library_id: libraryId,
      offset,
      limit: pageSize,
      query,
      tags: JSON.parse(tagKey) as string[],
      untagged,
    })
      .then((response) => {
        if (!alive) return;
        const result = response ?? { items: [], total: 0, offset: 0 };
        latestTotal.current(result.total);
        if (offset > 0 && offset >= result.total) {
          setOffset(
            Math.max(0, Math.floor((result.total - 1) / pageSize) * pageSize),
          );
          return;
        }
        setData(result);
        setDetails(
          (previous) =>
            result.items.find((item) => item.id === previous?.id) ?? null,
        );
      })
      .catch((reason) => {
        if (alive) {
          setError(String(reason));
          latestTotal.current(0);
        }
      });
    return () => {
      alive = false;
    };
  }, [libraryId, query, tagKey, untagged, offset, revision, reload]);
  function open(item: Entry) {
    if (item.kind === "comic") setComic(item.id);
    else if (item.kind === "image") setImage(item);
    else
      void call("local_media_open", { id: item.id }).catch((reason) =>
        notify(String(reason)),
      );
  }
  function paginate(next: number) {
    setOffset(next);
    list.current?.scrollIntoView({ block: "start" });
  }
  if (!data?.items.length && !error) {
    if (!standalone) return null;
    return (
      <div className="empty">
        <FolderOpen size={35} />
        <h3>
          {!data
            ? "正在读取本地内容…"
            : query || tags.length || untagged
              ? "没有匹配的本地内容"
              : "暂无本地导入"}
        </h3>
      </div>
    );
  }
  return (
    <section className="local-library" ref={list}>
      {!standalone && <h2>本地导入</h2>}
      {error && (
        <div className="local-actions local-error" role="alert">
          {error}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReload((value) => value + 1)}
          >
            重试
          </Button>
        </div>
      )}
      <div className="local-grid">
        {data?.items.map((item) => (
          <LocalCard
            key={item.id}
            item={item}
            revision={revision}
            open={() => open(item)}
            details={() => setDetails(item)}
          />
        ))}
      </div>
      {data && data.total > pageSize && (
        <div className="pagination">
          <Button
            variant="outline"
            size="sm"
            disabled={!offset}
            onClick={() => paginate(offset - pageSize)}
          >
            上一页
          </Button>
          <span>
            {data.offset + 1}–{Math.min(data.offset + pageSize, data.total)} /{" "}
            {data.total}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={offset + pageSize >= data.total}
            onClick={() => paginate(offset + pageSize)}
          >
            下一页
          </Button>
        </div>
      )}
      {details && (
        <LocalDetails
          key={details.id}
          item={details}
          libraryId={libraryId}
          libraries={libraries}
          close={() => setDetails(null)}
          changed={changed}
        />
      )}
      {image && (
        <LocalImage key={image.id} item={image} close={() => setImage(null)} />
      )}
      {comic !== null && (
        <ComicReader
          id={comic}
          close={() => setComic(null)}
          onChanged={changed}
        />
      )}
    </section>
  );
}
