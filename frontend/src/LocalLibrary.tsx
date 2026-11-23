import {
  BookOpen,
  Film,
  FolderOpen,
  Image,
  Layers,
  MoreHorizontal,
  Pencil,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { call } from "./api";
import { enqueuePreview } from "./Collections";
import { ComicReader } from "./ComicReader";
import { ContinuousFooter, useContinuousPages } from "./ContinuousPages";
import { MediaStack } from "./MediaStack";
import { useMasonry } from "./Masonry";
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
  manageable?: boolean;
  label: string;
};
type Entry = {
  id: number;
  title: string;
  body?: string;
  kind: Kind;
  bytes: number;
  tags: string[];
  libraries: string[];
  locations: Location[];
};
type MediaGroup = {
  id: number;
  title: string;
  body?: string;
  cover_id: number;
  items: Entry[];
};
type LocalRow = Entry & { group?: MediaGroup };
type LocalResults = { items: LocalRow[]; total: number; offset: number };
type GroupOperation = "merge" | "edit" | "detach" | "dissolve" | "undo";
type CardManagement = {
  selected: boolean;
  dropTarget: boolean;
  disabled: boolean;
  toggle: () => void;
  dragStart: (event: DragEvent) => void;
  dragEnd: () => void;
  dragOver: (event: DragEvent) => void;
  dragLeave: (event: DragEvent) => void;
  drop: (event: DragEvent) => void;
};
const dragType = "application/x-x2lt-local-cards";

function rowIds(row: LocalRow) {
  return row.group ? row.group.items.map((item) => item.id) : [row.id];
}

function rowKey(row: LocalRow) {
  return row.group ? "group-" + row.group.id : "entry-" + row.id;
}
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
  already?: number;
  excluded?: number;
  missing?: number;
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
const cardTitleLimit = 160;

export function LocalImportDialog({
  library,
  close,
  changed,
  scanCurrentPath = false,
}: {
  library: Pick<Library, "id" | "name" | "online" | "path" | "scan_mode">;
  close: () => void;
  changed: () => void;
  scanCurrentPath?: boolean;
}) {
  const [kind, setKind] = useState<ImportKind>("media");
  const [selectionMode, setSelectionMode] = useState<"files" | "folder">(
    "files",
  );
  const [picked, setPicked] = useState<ImportSelection | null>(null);
  const [storage, setStorage] = useState<"reference" | "copy">("reference");
  const [recursive, setRecursive] = useState(true);
  const [batch, setBatch] = useState(false);
  const [includeLoose, setIncludeLoose] = useState(false);
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
    if (!scanCurrentPath && !picked) return;
    void perform(async () => {
      const result = scanCurrentPath
        ? await call<{ id: string }>("local_library_scan", {
            library_id: library.id,
            include_loose: library.scan_mode === "comic" && includeLoose,
          })
        : await call<{ id: string }>("local_scan", {
            selection: picked?.id,
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
  const title = (scanCurrentPath ? "扫描 " : "导入到 ") + library.name;
  return (
    <Modal
      open
      onClose={() => {
        if (!running && !busy) close();
      }}
      title={title}
    >
      <div className="local-dialog">
        {!id && scanCurrentPath ? (
          <>
            <div className="local-actions">
              <strong>{library.scan_mode === "comic" ? "漫画" : "图片 / 视频"}</strong>
              <PrivateText compact text={library.path} />
            </div>
            {library.scan_mode === "comic" && (
              <label className="local-check">
                <input
                  type="checkbox"
                  checked={includeLoose}
                  disabled={busy}
                  onChange={(event) => setIncludeLoose(event.target.checked)}
                />
                将根目录的散图合成一本漫画
              </label>
            )}
            <div className="local-actions">
              <Button disabled={busy || !library.online} onClick={scan}>开始扫描</Button>
            </div>
          </>
        ) : !id ? (
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
                {scanCurrentPath && status.already !== undefined
                  ? !!status.already && <span>{status.already} 项已入库</span>
                  : !!status.duplicate && <span>{status.duplicate} 项重复</span>}
                {!!status.excluded && <span>{status.excluded} 项已跳过</span>}
                {!!status.missing && <span>{status.missing} 项文件缺失或离线</span>}
                {!!status.failed && <span>{status.failed} 项失败</span>}
              </div>
            )}
            {running && (
              <progress
                aria-label={stage ? stageLabel[stage] : stageLabel.scanning}
                max={status?.total || 1}
                value={stage === "importing" && status?.total ? status.done : undefined}
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
                  {scanCurrentPath ? "返回扫描选项" : "返回导入选项"}
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

function LocalPreview({
  item,
  revision,
  open,
  label,
  managing = false,
  continuous = false,
}: {
  item: Entry;
  revision: number;
  open: () => void;
  label: string;
  managing?: boolean;
  continuous?: boolean;
}) {
  const preview = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [image, setImage] = useState("");
  const [aspectRatio, setAspectRatio] = useState<string>();
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
        setVisible(result.isIntersecting);
        if (result.isIntersecting && !continuous) observer.disconnect();
      },
      { rootMargin: "250px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [continuous]);
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
    <div
      ref={preview}
      className="local-preview"
      style={{ aspectRatio: available && (image || continuous) ? aspectRatio : undefined }}
    >
      {image ? (
        <img
          src={image}
          alt=""
          draggable={false}
          className={hidden ? "privacy-blurred" : ""}
          onLoad={({ currentTarget }) => {
            if (currentTarget.naturalWidth > 0 && currentTarget.naturalHeight > 0)
              setAspectRatio(`${currentTarget.naturalWidth} / ${currentTarget.naturalHeight}`);
          }}
          onError={() => setImage("")}
        />
      ) : (
        <Icon size={28} />
      )}
      <span className="local-preview-kind">{kindLabel[item.kind]}</span>
      {!managing && (
        <button
          type="button"
          className="local-preview-open"
          aria-label={hidden ? "显示预览" : label}
          onClick={hidden ? reveal : open}
        />
      )}
      {hidden && (managing
        ? <button type="button" className="privacy-hint local-preview-reveal" onClick={reveal}>点击显示</button>
        : <span className="privacy-hint">点击显示</span>)}
      {!available && <span className="local-preview-state">不可用</span>}
    </div>
  );
}

function LocalCard({
  item,
  revision,
  open,
  details,
  management,
  continuous,
}: {
  item: LocalRow;
  revision: number;
  open: () => void;
  details: () => void;
  management?: CardManagement;
  continuous: boolean;
}) {
  const [slot, setSlot] = useState(0);
  const fromControl = useRef(false);
  const { group } = item;
  const body = group ? group.body : item.body;
  useEffect(() => setSlot(0), [group?.cover_id]);
  const coverIndex = group?.items.findIndex((entry) => entry.id === group.cover_id) ?? -1;
  const previews = group && coverIndex > 0
    ? [group.items[coverIndex], ...group.items.filter((entry) => entry.id !== group.cover_id)]
    : group?.items;
  const isAvailable = item.locations.some((location) => location.state === "available");
  return (
    <article
      className={[
        "local-card",
        group && "local-group-card",
        management?.selected && "local-card-selected",
        management?.dropTarget && "local-card-drop",
      ].filter(Boolean).join(" ")}
      draggable={!!management && !management.disabled}
      onPointerDownCapture={(event) => {
        fromControl.current = event.target instanceof Element &&
          !!event.target.closest("button, input, textarea, select, a, [contenteditable]");
      }}
      onDragStart={management && ((event) => {
        if (fromControl.current) {
          event.preventDefault();
          return;
        }
        management.dragStart(event);
      })}
      onDragEnd={management?.dragEnd}
      onDragOver={management?.dragOver}
      onDragLeave={management?.dragLeave}
      onDrop={management?.drop}
    >
      <header className="local-card-header">
        {management && (
          <input
            type="checkbox"
            aria-label={group ? "选择集合" : "选择媒体"}
            checked={management.selected}
            onChange={management.toggle}
            disabled={management.disabled}
          />
        )}
        <div className="local-card-title">
          <PrivateText compact text={group?.title || item.title} />
        </div>
        <button
          type="button"
          className="icon-btn"
          aria-label={management ? "编辑卡片信息" : group ? "集合详情" : "本地媒体详情"}
          disabled={management?.disabled}
          title={management ? "编辑卡片信息" : undefined}
          onClick={details}
        >
          {management ? <Pencil size={17} /> : <MoreHorizontal size={17} />}
        </button>
      </header>
      {body && (
        <div className="local-card-text">
          <PrivateText compact text={body} />
        </div>
      )}
      {previews?.length ? (
        <MediaStack
          key={group?.cover_id}
          count={previews.length}
          index={slot}
          onChange={setSlot}
          render={(index) => (
            <LocalPreview
              key={previews[index].id}
              item={previews[index]}
              revision={revision}
              open={open}
              label="打开集合"
              managing={!!management}
              continuous={continuous}
            />
          )}
        />
      ) : (
        <LocalPreview
          item={item}
          revision={revision}
          open={isAvailable ? open : details}
          label={kindLabel[item.kind] + " · 打开"}
          managing={!!management}
          continuous={continuous}
        />
      )}
      <footer className="local-card-meta">
        {group ? <span>{group.items.length} 项媒体</span> : <span>本地导入</span>}
        <span>{bytes(item.bytes)}</span>
        {item.tags.map((tag) => (
          <span key={tag} className="local-tag">
            #{tag.replace(/^#/, "")}
          </span>
        ))}
      </footer>
    </article>
  );
}

function CardEditFields({
  titleLabel = "标题",
  titlePlaceholder,
  title,
  body,
  busy,
  onTitle,
  onBody,
}: {
  titleLabel?: string;
  titlePlaceholder?: string;
  title: string;
  body: string;
  busy: boolean;
  onTitle: (value: string) => void;
  onBody: (value: string) => void;
}) {
  return (
    <>
      <label>
        {titleLabel}
        <input className="local-tags-input" value={title} maxLength={cardTitleLimit} placeholder={titlePlaceholder}
          onChange={(event) => onTitle(event.target.value)} disabled={busy} />
      </label>
      <label>
        正文
        <textarea className="local-tags-input local-body-input" value={body} rows={4} maxLength={8000}
          onChange={(event) => onBody(event.target.value)} disabled={busy} />
      </label>
    </>
  );
}

function LocalDetails({
  item,
  libraryId,
  libraries,
  close,
  changed,
  managing,
}: {
  item: Entry;
  libraryId: string;
  libraries: Library[];
  close: () => void;
  changed: () => void;
  managing: boolean;
}) {
  const [tags, setTags] = useState(item.tags.join("，"));
  const [target, setTarget] = useState("");
  const [confirm, setConfirm] = useState<"membership" | Location | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [rename, setRename] = useState<Location | null>(null);
  const [name, setName] = useState("");
  const initialTitle = item.title.length <= cardTitleLimit ? item.title : "";
  const [title, setTitle] = useState(initialTitle);
  const [body, setBody] = useState(item.body ?? "");
  const { hidden, reveal } = usePrivateReveal(`location-${item.id}`);
  const cardPrivacy = usePrivateReveal(`edit-local-${item.id}`);
  useEffect(() => setTitle(initialTitle), [initialTitle]);
  useEffect(() => setBody(item.body ?? ""), [item.body]);
  async function perform(
    action: string,
    payload: Payload,
    options: { update?: boolean; close?: boolean; message?: string } = {},
  ) {
    setBusy(true);
    setMessage("");
    try {
      const result = await call(action, payload);
      if (result === null || result === false) return false;
      if (options.update) changed();
      if (options.close) close();
      if (options.message) setMessage(options.message);
      return true;
    } catch (error) {
      setMessage(String(error));
      return false;
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
  async function renameFile(e: FormEvent) {
    e.preventDefault();
    if (!rename || !name.trim()) return;
    const saved = await perform("local_file_manage", {
      library_id: libraryId,
      id: item.id,
      location_id: rename.id,
      name: name.trim(),
    }, { update: true, message: "名称已更新" });
    if (saved) setRename(null);
  }
  function saveCard(event: FormEvent) {
    event.preventDefault();
    if (!managing || cardPrivacy.hidden || busy) return;
    void perform(
      "local_media_edit",
      { library_id: libraryId, id: item.id, title: title.trim(), body: body.trim() },
      { update: true, message: "卡片信息已保存" },
    );
  }
  return (
    <Modal open onClose={close} title={kindLabel[item.kind] + "详情"}>
      <div className="local-dialog">
        {managing && !cardPrivacy.hidden ? (
          <form className="local-dialog local-card-edit" onSubmit={saveCard}>
            <CardEditFields title={title} body={body} busy={busy} onTitle={setTitle} onBody={setBody}
              titlePlaceholder={item.kind === "comic" ? "默认使用原书名" : "默认使用原文件名"} />
            <Button type="submit" size="sm" variant="outline"
              disabled={busy || (title.trim() === initialTitle && body.trim() === (item.body ?? ""))}>
              保存卡片信息
            </Button>
          </form>
        ) : (
          <>
            <div className="local-card-title"><PrivateText compact text={item.title} /></div>
            {item.body && <div className="local-detail-text"><PrivateText compact text={item.body} /></div>}
            {managing && (
              <Button type="button" size="sm" variant="outline" onClick={cardPrivacy.reveal}>
                显示编辑
              </Button>
            )}
          </>
        )}
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
                {managing && location.manageable && location.state === "available" && (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        setName(location.label);
                        setRename(location);
                      }}
                    >
                      重命名
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => void perform("local_file_manage", {
                        library_id: libraryId,
                        id: item.id,
                        location_id: location.id,
                        move: true,
                      }, { update: true, message: "文件已移动" })}
                    >
                      移动到…
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
          {rename && (
            <form className="local-actions" onSubmit={renameFile}>
              <input
                className="local-tags-input"
                aria-label="新名称"
                placeholder="新名称"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={150}
                disabled={busy}
              />
              <Button type="submit" size="sm" disabled={busy || !name.trim()}>
                保存名称
              </Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => setRename(null)}>
                取消
              </Button>
            </form>
          )}
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
                : "将这个文件移入系统回收站？"}
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

function GroupDetails({
  group,
  managing,
  busy,
  close,
  open,
  details,
  change,
}: {
  group: MediaGroup;
  managing: boolean;
  busy: boolean;
  close: () => void;
  open: (entry: Entry) => void;
  details: (entry: Entry) => void;
  change: (operation: GroupOperation, payload: Payload) => Promise<boolean>;
}) {
  const [title, setTitle] = useState(group.title);
  const [body, setBody] = useState(group.body ?? "");
  const [cover, setCover] = useState(group.cover_id);
  const [order, setOrder] = useState(group.items.map((item) => item.id));
  const [confirm, setConfirm] = useState(false);
  const { hidden, reveal } = usePrivateReveal("edit-group-" + group.id);
  const groupOrder = group.items.map((item) => item.id).join(",");
  const edited = title.trim() !== group.title || body.trim() !== (group.body ?? "")
    || cover !== group.cover_id || order.join(",") !== groupOrder;
  useEffect(() => {
    setOrder(groupOrder.split(",").map(Number));
  }, [groupOrder]);
  useEffect(() => setTitle(group.title), [group.title]);
  useEffect(() => setBody(group.body ?? ""), [group.body]);
  useEffect(() => setCover(group.cover_id), [group.cover_id]);
  const members = new Map(group.items.map((entry) => [entry.id, entry]));
  function shift(index: number, delta: number) {
    setOrder((previous) => {
      const next = [...previous];
      [next[index], next[index + delta]] = [next[index + delta], next[index]];
      return next;
    });
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (!managing || hidden || busy || !title.trim() || !edited) return;
    void change("edit", {
      group_id: group.id,
      title: title.trim(),
      body: body.trim(),
      cover_id: cover,
      order,
    });
  }
  return (
    <Modal open onClose={close} title="媒体集合">
      <form className="local-dialog" onSubmit={save}>
        {managing && !hidden ? (
          <CardEditFields titleLabel="集合名称" title={title} body={body} busy={busy}
            onTitle={setTitle} onBody={setBody} />
        ) : (
          <>
            <div className="local-card-title"><PrivateText compact text={group.title} /></div>
            {group.body && <div className="local-detail-text"><PrivateText compact text={group.body} /></div>}
            {managing && hidden && (
              <Button type="button" size="sm" variant="outline" onClick={reveal}>
                显示编辑
              </Button>
            )}
          </>
        )}
        <ol className="local-group-members">
          {order.map((id, index) => {
            const entry = members.get(id);
            if (!entry) return null;
            const available = entry.locations.some((location) => location.state === "available");
            return (
              <li key={id} className="local-group-member">
                <PrivateText compact text={entry.title} />
                <div className="local-actions">
                  <span className="local-member-kind">{kindLabel[entry.kind]}</span>
                  {cover === id && <span className="local-member-kind">封面</span>}
                  <Button type="button" variant="outline" size="sm" onClick={() => available ? open(entry) : details(entry)}>
                    {available ? "打开" : "文件位置"}
                  </Button>
                  {managing && (
                    <>
                      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => details(entry)}>
                        文件管理
                      </Button>
                      <Button type="button" variant="outline" size="sm" disabled={busy || cover === id} onClick={() => setCover(id)}>
                        设为封面
                      </Button>
                      <Button type="button" variant="outline" size="sm" disabled={busy || !index} onClick={() => shift(index, -1)}>
                        上移
                      </Button>
                      <Button type="button" variant="outline" size="sm" disabled={busy || index >= order.length - 1} onClick={() => shift(index, 1)}>
                        下移
                      </Button>
                      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void change("detach", { group_id: group.id, entry_id: id })}>
                        移出集合
                      </Button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        {managing && (
          <div className="local-actions">
            <Button type="submit" size="sm" disabled={busy || hidden || !title.trim() || !edited}>
              保存集合
            </Button>
            {confirm ? (
              <>
                <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void change("dissolve", { group_id: group.id })}>
                  确认解散
                </Button>
                <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setConfirm(false)}>
                  取消
                </Button>
              </>
            ) : (
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setConfirm(true)}>
                解散集合
              </Button>
            )}
          </div>
        )}
      </form>
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
  infiniteScroll = false,
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
  infiniteScroll?: boolean;
}) {
  const [pagedData, setData] = useState<LocalResults | null>(null);
  const [pagination, setPagination] = useState({ filter: "", offset: 0 });
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [details, setDetails] = useState<Entry | null>(null);
  const [image, setImage] = useState<Entry | null>(null);
  const [comic, setComic] = useState<number | null>(null);
  const [groupId, setGroupId] = useState<number | null>(null);
  const [managing, setManaging] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [undo, setUndo] = useState("");
  const [dropTarget, setDropTarget] = useState("");
  const drag = useRef<{ source: string; ids: number[] } | null>(null);
  const list = useRef<HTMLElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const latestTotal = useRef(onTotal);
  latestTotal.current = onTotal;
  const tagKey = JSON.stringify(tags);
  const filter = JSON.stringify([libraryId, query, tagKey, untagged]);
  const offset = pagination.filter === filter ? pagination.offset : 0;
  const loadPage = useCallback(async (requested: number) =>
    await call<LocalResults | null>("local_library", {
      library_id: libraryId, offset: requested, limit: pageSize, query,
      tags: JSON.parse(tagKey) as string[], untagged,
    }) ?? { items: [], total: 0, offset: requested },
  [libraryId, query, tagKey, untagged]);
  const continuous = useContinuousPages({
    enabled: infiniteScroll, scope: filter, revision: `${revision}:${reload}`,
    pageSize, key: rowKey,
    load: async (requested) => {
      const page = await loadPage(requested);
      return { ...page, rows: page.items };
    },
  });
  const data = infiniteScroll
    ? continuous.ready ? { items: continuous.rows, total: continuous.total, offset: 0 } : null
    : pagedData;
  useMasonry(grid, !!data?.items.length, data?.items.map(rowKey).join(",") ?? "", ".local-card");
  useEffect(() => {
    if (infiniteScroll) return;
    let alive = true;
    setError("");
    void loadPage(offset)
      .then((result) => {
        if (!alive) return;
        latestTotal.current(result.total);
        if (offset > 0 && offset >= result.total) {
          setPagination({
            filter,
            offset: Math.max(0, Math.floor((result.total - 1) / pageSize) * pageSize),
          });
          return;
        }
        setData(result);
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
  }, [filter, offset, revision, reload, infiniteScroll, loadPage]);
  const rows = data?.items;
  useEffect(() => {
    if (!rows) return;
    setDetails((previous) => previous
      ? rows.flatMap((row) => row.group?.items ?? [row]).find((item) => item.id === previous.id) ?? null
      : null);
    setGroupId((current) => current !== null && rows.some((item) => item.group?.id === current) ? current : null);
  }, [rows]);
  useEffect(() => {
    if (infiniteScroll && (continuous.ready || continuous.error)) {
      latestTotal.current(continuous.total);
    }
  }, [infiniteScroll, continuous.ready, continuous.total, continuous.error]);
  function open(item: Entry) {
    if (item.kind === "comic") setComic(item.id);
    else if (item.kind === "image") setImage(item);
    else
      void call("local_media_open", { id: item.id }).catch((reason) =>
        notify(String(reason)),
      );
  }
  async function changeGroup(operation: GroupOperation, payload: Payload) {
    if (busy) return false;
    setBusy(true);
    try {
      const result = await call<{ undo?: string }>("local_group_change", {
        ...payload, library_id: libraryId, operation,
      });
      setUndo(result?.undo || "");
      setSelected([]);
      setReload((value) => value + 1);
      changed();
      if (operation === "merge") notify("已聚合到集合");
      if (operation === "edit") notify("集合已保存");
      if (operation === "detach") notify("已移出集合");
      if (operation === "dissolve") notify("集合已解散");
      if (operation === "undo") notify("已撤销集合操作");
      return true;
    } catch (reason) {
      notify(String(reason));
      return false;
    } finally {
      setBusy(false);
    }
  }
  function stopDrag() {
    drag.current = null;
    setDropTarget("");
  }
  function toggleManaging() {
    setSelected([]);
    stopDrag();
    setManaging((value) => !value);
  }
  function managementFor(row: LocalRow): CardManagement {
    const ids = rowIds(row);
    const key = rowKey(row);
    const isSelected = ids.every((id) => selected.includes(id));
    function canDrop(event: DragEvent) {
      return !busy && drag.current && drag.current.source !== key
        && drag.current.ids.some((id) => !ids.includes(id))
        && event.dataTransfer.types.includes(dragType);
    }
    return {
      selected: isSelected,
      dropTarget: dropTarget === key,
      disabled: busy,
      toggle: () => setSelected((previous) =>
        isSelected ? previous.filter((id) => !ids.includes(id)) : [...new Set([...previous, ...ids])],
      ),
      dragStart: (event) => {
        drag.current = { source: key, ids: isSelected ? selected : ids };
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(dragType, key);
      },
      dragEnd: stopDrag,
      dragOver: (event) => {
        if (!canDrop(event)) {
          if (dropTarget) setDropTarget("");
          return;
        }
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        if (dropTarget !== key) setDropTarget(key);
      },
      dragLeave: (event) => {
        const next = event.relatedTarget;
        if (dropTarget === key && (!(next instanceof Node) || !event.currentTarget.contains(next)))
          setDropTarget("");
      },
      drop: (event) => {
        if (!canDrop(event)) return;
        event.preventDefault();
        const source = drag.current;
        stopDrag();
        if (source)
          void changeGroup("merge", {
            ids: source.ids.filter((id) => !ids.includes(id)),
            ...(row.group ? { group_id: row.group.id } : { target_id: row.id }),
          });
      },
    };
  }
  function paginate(next: number) {
    setPagination({ filter, offset: next });
    list.current?.scrollIntoView({ block: "start" });
  }
  const group = data?.items.find((row) => row.group?.id === groupId)?.group;
  const listError = infiniteScroll ? continuous.error : error;
  const showEmpty = standalone && !data?.items.length && !listError;
  if (!data?.items.length && !listError && !standalone && !managing) return null;
  return (
    <section className="local-library" ref={list}>
      <div className="local-management-bar">
        {!standalone && <h2>本地导入</h2>}
        <div className="local-actions">
          <Button variant="outline" size="sm" aria-pressed={managing} disabled={busy} onClick={toggleManaging}>
            {managing ? "退出管理" : "管理模式"}
          </Button>
          {managing && (
            <>
              <Button
                size="sm"
                disabled={busy || selected.length < 2}
                onClick={() => void changeGroup("merge", { ids: selected })}
              >
                <Layers size={15} />
                合并为卡片{selected.length ? ` (${selected.length})` : ""}
              </Button>
              {!!selected.length && (
                <Button variant="outline" size="sm" disabled={busy} onClick={() => setSelected([])}>
                  清除选择
                </Button>
              )}
            </>
          )}
          {undo && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void changeGroup("undo", { undo })}>
              撤销集合操作
            </Button>
          )}
        </div>
      </div>
      {showEmpty && (
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
      )}
      {!infiniteScroll && error && (
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
      <div className={infiniteScroll ? "local-grid continuous-cards" : "local-grid"} ref={grid}>
        {data?.items.map((item) => (
          <LocalCard
            key={rowKey(item)}
            item={item}
            revision={revision}
            open={() => item.group ? setGroupId(item.group.id) : open(item)}
            details={() => item.group ? setGroupId(item.group.id) : setDetails(item)}
            management={managing ? managementFor(item) : undefined}
            continuous={infiniteScroll}
          />
        ))}
      </div>
      {infiniteScroll && (continuous.rows.length > 0 || continuous.error) && (
        <ContinuousFooter loaded={continuous.rows.length} total={continuous.total}
          loading={continuous.loading} done={continuous.done} error={continuous.error}
          onLoad={continuous.loadNext} onRetry={continuous.retry} />
      )}
      {!infiniteScroll && data && data.total > pageSize && (
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
      {group && (
        <GroupDetails
          key={group.id}
          group={group}
          managing={managing}
          busy={busy}
          close={() => setGroupId(null)}
          open={open}
          details={setDetails}
          change={changeGroup}
        />
      )}
      {details && (
        <LocalDetails
          key={details.id}
          item={details}
          libraryId={libraryId}
          libraries={libraries}
          close={() => setDetails(null)}
          changed={changed}
          managing={managing}
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
