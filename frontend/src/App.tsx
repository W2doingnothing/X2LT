import { useMasonry } from "./Masonry";
import { DownloadDialog, type DownloadMode } from "./DownloadDialog";
import { MediaStack } from "./MediaStack";
import { CloudArchiveDialog, type CloudArchiveTarget } from "./CloudArchiveDialog";
import { LocalImportDialog, LocalLibrary } from "./LocalLibrary";
import {
  ContentMenu,
  ContentContextMenu,
  RemovalDialog,
  FiltersPanel,
  type ContextTarget,
} from "./Removal";
import { ContentIdentity } from "./ContentIdentity";
import { contentPresentation } from "./contentPresentation";
import { useState, useEffect, useRef, useCallback } from "react";
import {
  ArrowDownToLine,
  LayoutDashboard,
  Layers,
  FolderOpen,
  Settings as SettingsIcon,
  Sun,
  Moon,
  Plus,
  Search,
  ChevronLeft,
  ChevronRight,
  ArrowUpRight,
  Play,
  Pause,
  Check,
  CheckCheck,
  X,
  EyeOff,
  Undo2,
  Image,
  Film,
  RefreshCw,
  HardDrive,
  ShieldCheck,
  Clock,
  Download,
  CheckCircle2,
  Info,
  ExternalLink,
} from "lucide-react";
import type {
  Snapshot,
  Media,
  Tweet,
  Filter,
  Payload,
  Event,
  Operation,
  Author,
  AuthorPreference,
  Library,
  Source,
} from "./types";
import { call, desktop, demoProgress } from "./api";
import { applyTheme } from "./theme";
import { reconcileProgress, type Progress } from "./progress";
import {
  PrivacyProvider,
  PrivacyPanel,
  PrivateText,
  usePrivateReveal,
} from "./Privacy";
import { art } from "./art";
import {
  ThemePanel,
  LibraryPanel,
  AccountPanel,
  bytes,
  type Action,
} from "./Preferences";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { ScrollNavigation } from "./ScrollNavigation";
import { Authors } from "./Authors";
import { ResourceBrowser, type ResourceKind } from "./ResourceBrowser";
import {
  CloudLibrary,
  CloudLibraryCard,
  CloudItemTools,
  CloudLocateDialog,
  CloudStorageBadges,
  CloudUploadDialog,
  cloudLoginPending,
  isSavedLocation,
  type CloudItem,
  type CloudLocation,
  type CloudStatus,
} from "./CloudLibrary";
import { useBackNavigation } from "./BackNavigation";
import {
  Collections,
  Tags,
  TagFilters,
  Avatar,
  OperationStatus,
  LibraryCover,
  enqueuePreview,
} from "./Collections";
type Page =
  "overview" | "downloads" | "telegram" | "archive" | "organize" | "settings";
const titles: Record<Page, string> = {
  overview: "总览",
  downloads: "推特下载",
  telegram: "电报下载",
  archive: "媒体库",
  organize: "媒体工具",
  settings: "偏好设置",
};
const labels: Record<string, string> = {
  pending: "等待下载",
  downloading: "下载中",
  paused: "已暂停",
  failed: "失败可重试",
  completed: "已保存",
};
function groups(media: Media[]) {
  const map = new Map<string, Media[]>();
  media.forEach((m) => {
    const key = m.media_type + ":" + m.media_id;
    map.set(key, [...(map.get(key) || []), m]);
  });
  return [...map.values()].sort(
    (a, b) => Number(a[0].media_id) - Number(b[0].media_id),
  );
}
const representative = (rows: Media[]) =>
  rows.find((m) => m.state === "downloading") ||
  rows.find((m) => m.state !== "completed") ||
  rows[0];
const done = (t: Tweet) => t.media.every((m) => m.state === "completed");
const previewCache = new Map<string, string>();
function Thumbnail({
  media,
  tweet,
  index,
  progress,
  onClick,
  detail = false,
  compact = false,
  onSwipe,
}: {
  media: Media;
  tweet: Tweet;
  index: number;
  progress?: Progress;
  onClick: () => void;
  detail?: boolean;
  compact?: boolean;
  onSwipe?: (delta: number) => void;
}) {
  const { hidden, reveal } = usePrivateReveal(String(media.id));
  const [image, setImage] = useState<string | null>(null),
    [visible, setVisible] = useState(false);
  const [dimensions, setDimensions] = useState<[number, number] | null>(null);
  useEffect(() => setDimensions(null), [media.id]);
  const [previewWidth, previewHeight] = dimensions || [
    media.width,
    media.height,
  ];
  const element = useRef<HTMLButtonElement>(null);
  const startX = useRef(0),
    swiped = useRef(false);
  useEffect(() => {
    if (!desktop || !element.current) return;
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      { rootMargin: "100px" },
    );
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [media.id]);
  useEffect(() => {
    let mounted = true;
    const key = media.id + ":" + media.state;
    if (!desktop || !visible) return;
    const cached = previewCache.get(key);
    if (cached) {
      setImage(cached);
      return;
    }
    void enqueuePreview(async () => {
      if (!mounted) return;
      const image = await call<string | null>("preview", { job_id: media.id });
      if (image) {
        previewCache.set(key, image);
        if (previewCache.size > 200)
          previewCache.delete(previewCache.keys().next().value!);
      }
      if (mounted) setImage(image);
    }).catch(() => null);
    return () => {
      mounted = false;
    };
  }, [media.id, media.state, visible]);
  const percent =
    media.state === "completed"
      ? 100
      : progress?.total
        ? Math.min(100, (100 * progress.done) / progress.total)
        : desktop
          ? 0
          : demoProgress(media);
  const state =
    media.state === "downloading" && progress && !progress.total
      ? "下载中"
      : labels[media.state];
  const determinate = !!progress?.total || (!desktop && percent > 0);
  return (
    <button
      ref={element}
      style={
        !detail
          ? {
              aspectRatio:
                previewWidth > 0 && previewHeight > 0
                  ? `${previewWidth} / ${previewHeight}`
                  : "16 / 9",
              width: `min(100%, ${(compact ? 280 : 420) * (previewWidth > 0 && previewHeight > 0 ? previewWidth / previewHeight : 16 / 9)}px)`,
            }
          : undefined
      }
      className={
        "media-tile " +
        (media.state !== "completed" && !detail ? "unfinished " : "") +
        (detail ? "detail-tile" : "")
      }
      onPointerDown={(e) => {
        startX.current = e.clientX;
        swiped.current = false;
      }}
      onPointerUp={(e) => {
        const delta = e.clientX - startX.current;
        if (Math.abs(delta) > 30 && onSwipe) {
          swiped.current = true;
          onSwipe(delta > 0 ? -1 : 1);
        }
      }}
      onClick={() => {
        if (hidden) {
          reveal();
          return;
        }
        if (!swiped.current) onClick();
      }}
      aria-label={`${hidden ? "显示" : "预览"}第 ${index + 1} 个媒体`}
    >
      {image || !desktop ? (
        <img
          draggable={false}
          onLoad={(e) => {
            if (
              desktop &&
              image &&
              e.currentTarget.naturalWidth &&
              e.currentTarget.naturalHeight
            )
              setDimensions([
                e.currentTarget.naturalWidth,
                e.currentTarget.naturalHeight,
              ]);
          }}
          src={image || art(tweet.demoColor || "gold", index)}
          alt={hidden ? "已模糊的媒体" : `${tweet.author}的媒体 ${index + 1}`}
          className={hidden ? "privacy-blurred" : ""}
        />
      ) : (
        <div className="no-preview">
          <Image size={26} />
          <span>暂无预览</span>
        </div>
      )}
      {hidden && (
        <>
          <span className="privacy-shield" />
          <span className="privacy-hint">点击显示</span>
        </>
      )}
      <span className="media-type">
        {media.media_type === "video" ? (
          <Film size={12} />
        ) : (
          <Image size={12} />
        )}{" "}
        {media.extension.toUpperCase()}
      </span>
      {media.state === "completed" ? (
        <span className="media-done">
          <Check size={12} /> 已保存
        </span>
      ) : (
        !hidden &&
        !detail && (
          <div className="media-cover">
            <div
              className="media-progress-ring"
              role="progressbar"
              aria-label={state}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={determinate ? Math.floor(percent) : undefined}
            >
              <svg viewBox="0 0 64 64">
                <circle cx="32" cy="32" r="27" />
                <circle
                  className={
                    media.state === "downloading" && !determinate
                      ? "indeterminate"
                      : ""
                  }
                  cx="32"
                  cy="32"
                  r="27"
                  strokeDasharray={`${determinate ? percent * 1.696 : media.state === "downloading" ? 55 : 0} 170`}
                />
              </svg>
              <span>
                {determinate ? (
                  `${Math.floor(percent)}%`
                ) : media.state === "downloading" ? (
                  <Download size={18} />
                ) : (
                  <Clock size={18} />
                )}
              </span>
            </div>
            <small>{state}</small>
          </div>
        )
      )}
      <span className="media-size">
        {media.bytes ? bytes(media.bytes) : ""}
      </span>
    </button>
  );
}
export default function App() {
  const [data, setData] = useState<Snapshot | null>(null),
    [page, setPage] = useState<Page>("downloads"),
    [filter, setFilter] = useState<Filter>("all"),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<string[]>([]),
    [offsets, setOffsets] = useState<Record<string, number>>({}),
    [progress, setProgress] = useState<Record<number, Progress>>({}),
    [detail, setDetail] = useState<Tweet | null>(null),
    [detailIndex, setDetailIndex] = useState(0),
    [adding, setAdding] = useState<DownloadMode>(null),
    [library, setLibrary] = useState(""),
    [settingsTab, setSettingsTab] = useState("appearance"),
    [toast, setToast] = useState<{ text: string; undo?: () => void } | null>(
      null,
    ),
    [loadError, setLoadError] = useState(""),
    [busy, setBusy] = useState(false),
    [transition, setTransition] = useState(false);
  const progressRun = useRef<string | null>(null);
  const dataRef = useRef(data);
  dataRef.current = data;
  const [scope, setScope] = useState<string | null>(null);
  const [cloudPage, setCloudPage] = useState<Page | null>(null);
  const [cloudStatus, setCloudStatus] = useState<CloudStatus | null>(null);
  const [cloudPositions, setCloudPositions] = useState<Record<string, CloudItem>>({});
  const [cloudLinked, setCloudLinked] = useState(false);
  const [cloudTarget, setCloudTarget] = useState<string | null>(null);
  const [cloudUpload, setCloudUpload] = useState<Media[] | null>(null);
  const [cloudArchive, setCloudArchive] = useState<CloudArchiveTarget | null>(null);
  const [cloudRevision, setCloudRevision] = useState(0);
  const [localImport, setLocalImport] = useState<Library | null>(null);
  const [localScan, setLocalScan] = useState<Library | null>(null);
  const [localRevision, setLocalRevision] = useState(0);
  const [localCount, setLocalCount] = useState<{ key: string; total: number } | null>(null);
  const inCloud = cloudPage === page;
  const [removeIds, setRemoveIds] = useState<string[] | null>(null);
  const [contextTarget, setContextTarget] = useState<ContextTarget | null>(
    null,
  );
  type CollectionRoute = {
    mode: "tweets" | "authors" | "sources" | "local";
    author: Author | null;
    source?: Source | null;
    group?: "active" | "archive";
  };
  const [collectionRoute, setCollectionRoute] = useState<CollectionRoute>({
    mode: "tweets",
    author: null,
  });
  const libraryRoutes = useRef<Record<string, CollectionRoute>>({});
  const [resourceKind, setResourceKind] = useState<ResourceKind | null>(null);
  const overviewRootScroll = useRef(0);
  const resourceScroll = useRef<Partial<Record<ResourceKind, number>>>({});
  function chooseResourceKind(kind: ResourceKind | null) {
    if (resourceKind === null) overviewRootScroll.current = window.scrollY;
    else resourceScroll.current[resourceKind] = window.scrollY;
    setResourceKind(kind);
    const y =
      kind === null
        ? overviewRootScroll.current
        : resourceScroll.current[kind] || 0;
    requestAnimationFrame(() =>
      requestAnimationFrame(() => window.scrollTo(0, y)),
    );
  }
  const [toolOpen, setToolOpen] = useState(false);
  const [authorBusy, setAuthorBusy] = useState(false);
  const isCollection = (page === "overview" || page === "archive") && !!scope;
  const authorView =
    isCollection &&
    collectionRoute.mode === "authors" &&
    !collectionRoute.author;
  const sourceView =
    isCollection &&
    collectionRoute.mode === "sources" &&
    !collectionRoute.source;
  const localView = isCollection && collectionRoute.mode === "local";
  const contentList = useRef<HTMLDivElement>(null);
  useMasonry(
    contentList,
    isCollection && !authorView && !sourceView && !localView,
    (data?.tasks.map((t) => t.id).join(",") || "") + ":" + page + ":" + scope,
  );
  const routeKey = (p: Page, s: string | null, route: CollectionRoute) =>
    p +
    ":" +
    (s || "") +
    ((p === "overview" || p === "archive") && s
      ? ":" +
        route.mode +
        ":" +
        (route.group || "active") +
        ":" +
        (route.author?.key || route.source?.key || "")
      : "");
  const [tagFilters, setTagFilters] = useState<string[]>([]);
  const [untagged, setUntagged] = useState(false);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [organizeProgress, setOrganizeProgress] = useState<{
    done: number;
    total: number;
    text: string;
  } | null>(null);
  type View = {
    filter: Filter;
    query: string;
    offset: number;
    tags: string[];
    untagged: boolean;
    selected: string[];
    offsets: Record<string, number>;
    detail: Tweet | null;
    detailIndex: number;
    scroll: number;
    library: string;
  };
  const views = useRef<Record<string, View>>({});
  const sectionScope = useRef<Partial<Record<Page, string | null>>>({});
  const pendingScroll = useRef<number | null>(null);
  const [downloadsOpen, setDownloadsOpen] = useState(true);
  const request = useRef(0);
  const detailId = useRef<string | null>(null);
  detailId.current = detail?.id || null;
  const notify = useCallback(
    (text: string, undo?: () => void) => setToast({ text, undo }),
    [],
  );
  const refresh = useCallback(async () => {
    const revision = ++request.current;
    try {
      const result = await call<Snapshot>("snapshot", {
        filter: page === "archive" ? "completed" : filter,
        query,
        offset,
        library_id: scope || undefined,
        tags: tagFilters,
        untagged,
        collection: isCollection,
        view: authorView ? "authors" : sourceView ? "sources" : "tweets",
        source_key: isCollection ? collectionRoute.source?.key : undefined,
        author_key: isCollection ? collectionRoute.author?.key : undefined,
        author_group: collectionRoute.group || "active",
      });
      if (revision !== request.current) return;
      applyTheme(result.settings.accent, result.settings.theme === "dark");
      setData(result);
      setSelected((prev) =>
        prev.filter((id) => result.tasks.some((t) => t.id === id)),
      );
      if (result.offset !== offset) {
        pendingScroll.current = 0;
        setSelected([]);
        setOffset(result.offset);
      }
      const snapshotRun = result.operation?.run_id || null;
      if (progressRun.current !== snapshotRun) {
        setProgress({});
        progressRun.current = snapshotRun;
      }
      setOperation(result.operation || null);
      setBusy(result.busy);
      setLoadError("");
      if (pendingScroll.current !== null) {
        const y = pendingScroll.current;
        pendingScroll.current = null;
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            if (revision === request.current) window.scrollTo(0, y);
          }),
        );
      }
      setLibrary((prev) =>
        result.libraries.some((l) => l.id === prev)
          ? prev
          : result.default_library || "",
      );
      if (detailId.current) {
        const updated = result.tasks.find((t) => t.id === detailId.current);
        if (updated) setDetail(updated);
      }
    } catch (e) {
      if (revision === request.current) setLoadError((e as Error).message);
    }
  }, [
    filter,
    query,
    offset,
    page,
    scope,
    tagFilters,
    untagged,
    isCollection,
    authorView,
    sourceView,
    collectionRoute,
  ]);
  const latestRefresh = useRef(refresh);
  latestRefresh.current = refresh;
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await call<{
          events: Event[];
          busy: boolean;
          operation?: Operation | null;
        }>("poll");
        if (!alive) return;
        setBusy(result.busy);
        if (result.operation !== undefined) setOperation(result.operation);
        const runId = result.operation?.run_id || null;
        const priorRun = progressRun.current;
        const events = result.events.filter(
          (e) => !e.run_id || e.run_id === runId,
        );
        setProgress((prev) =>
          reconcileProgress(prev, result.events, runId, priorRun, Date.now()),
        );
        progressRun.current = runId;
        events
          .filter(
            (e) =>
              e.type === "message" &&
              e.text &&
              (e.scope === "organize"
                ? page === "organize"
                : page === "downloads"),
          )
          .forEach((e) => notify(e.text!));
        for (const event of events)
          if (event.scope === "organize" && event.type === "operation_progress")
            setOrganizeProgress({
              done: event.done || 0,
              total: event.total || 0,
              text: event.text || "整理",
            });
        if (
          events.some((e) => ["changed", "finished", "resync"].includes(e.type))
        )
          void refresh();
      } catch (e) {
        if (alive) setLoadError((e as Error).message);
      } finally {
        if (alive) timer = setTimeout(poll, 600);
      }
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [refresh, notify]);
  useEffect(() => {
    if (data) applyTheme(data.settings.accent, data.settings.theme === "dark");
  }, [data?.settings.accent, data?.settings.theme]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  const action: Action = async (name, payload = {}, message) => {
    try {
      setTransition(true);
      const oldTags =
        name === "tags_set"
          ? dataRef.current?.tasks.find((t) => t.id === payload.id)?.tags || []
          : [];
      const result = await call(name, payload);
      if ((name === "cloud_backup" || name === "cloud_restore") && result === false) return false;
      if (name === "start") {
        setProgress({});
        progressRun.current = null;
      }
      if (name === "tags_set")
        setDetail((prev) =>
          prev && prev.id === payload.id
            ? { ...prev, tags: payload.tags as string[] }
            : prev,
        );
      if (name === "library_default") setLibrary(String(payload.id));
      if (name === "tag_rename")
        setTagFilters((prev) =>
          prev.map((t) => (t === payload.old ? String(payload.new) : t)),
        );
      if (name === "tag_delete")
        setTagFilters((prev) => prev.filter((t) => t !== payload.name));
      if (name === "tag_rename" || name === "tag_delete")
        setLocalRevision((value) => value + 1);
      if (message) notify(message);
      await latestRefresh.current();
      if (
        name === "tags_set" &&
        oldTags.length > (payload.tags as string[]).length
      )
        notify("标签已移除", () => {
          void action(
            "tags_set",
            { id: payload.id, tags: oldTags },
            "已恢复标签",
          );
        });
      return true;
    } catch (e) {
      notify((e as Error).message);
      return false;
    } finally {
      setTransition(false);
    }
  };
  const save = async (p: Payload) => action("settings", p, "设置已保存");
  async function refreshCloudStatus(login = false) {
    try {
      const status = await call<CloudStatus>(login ? "cloud_login" : "cloud_status");
      if (status && typeof status.available === "boolean") setCloudStatus((previous) => ({
        ...status,
        user_agreement_url: status.user_agreement_url || previous?.user_agreement_url,
        privacy_policy_url: status.privacy_policy_url || previous?.privacy_policy_url,
      }));
    } catch (e) {
      if (login) notify((e as Error).message);
    }
  }
  useEffect(() => { if (desktop) void refreshCloudStatus(); }, []);
  const cloudWaiting = cloudStatus && cloudLoginPending(cloudStatus);
  useEffect(() => {
    if (!cloudWaiting) return;
    const timer = setInterval(() => void refreshCloudStatus(), 2400);
    return () => clearInterval(timer);
  }, [cloudWaiting]);
  function cloudChanged() { setCloudRevision((value) => value + 1); }
  const cloudJobIds = [
    ...new Set((data?.tasks || []).flatMap((task) => task.media.map((media) => media.id))),
    ...(detail?.media || []).map((media) => media.id),
  ].filter((id) => id > 0).join(",");
  const cloudEnabled = cloudStatus !== null;
  useEffect(() => {
    if (!cloudEnabled || !cloudJobIds) {
      setCloudPositions({});
      return;
    }
    let alive = true;
    const ids = [...new Set(cloudJobIds.split(",").map(Number))];
    async function fetchPositions() {
      try {
        const responses = await Promise.all(Array.from({ length: Math.ceil(ids.length / 50) }, (_, index) =>
          call<Record<string, CloudItem>>("cloud_positions", { ids: ids.slice(index * 50, index * 50 + 50) }),
        ));
        if (alive) setCloudPositions(Object.assign({}, ...responses));
      } catch { /* Cloud availability should not interrupt local browsing. */ }
    }
    void fetchPositions();
    const timer = setInterval(fetchPositions, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [cloudEnabled, cloudJobIds, cloudRevision]);
  async function locateCloudPosition(location: CloudLocation) {
    try {
      const result = await call<{ parent?: string }>("cloud_locate", { id: location.id });
      if (typeof result?.parent === "string") setCloudTarget(result.parent);
    } catch (e) { notify((e as Error).message); }
  }
  function showCloud() {
    overviewRootScroll.current = window.scrollY;
    setCloudPage(page);
    requestAnimationFrame(() => window.scrollTo(0, 0));
  }
  function leaveCloud() {
    setCloudPage(null);
    requestAnimationFrame(() => window.scrollTo(0, overviewRootScroll.current));
  }
  function rememberView() {
    views.current[routeKey(page, scope, collectionRoute)] = {
      filter,
      query,
      offset,
      tags: tagFilters,
      untagged,
      selected: [...selected],
      offsets: { ...offsets },
      detail,
      detailIndex,
      scroll: window.scrollY,
      library,
    };
    sectionScope.current[page] = scope;
    if (isCollection && scope)
      libraryRoutes.current[page + ":" + scope] = collectionRoute;
  }
  function restoreView(
    p: Page,
    nextScope: string | null,
    targetRoute?: CollectionRoute,
  ) {
    request.current++;
    const route = targetRoute ||
      (nextScope ? libraryRoutes.current[p + ":" + nextScope] : null) || {
        mode: "tweets",
        author: null,
      };
    const view = views.current[routeKey(p, nextScope, route)];
    setCollectionRoute(route);
    setPage(p);
    setScope(nextScope);
    sectionScope.current[p] = nextScope;
    setFilter(view?.filter || (nextScope ? "completed" : "all"));
    setQuery(view?.query || "");
    setOffset(view?.offset || 0);
    setTagFilters(view?.tags || []);
    setUntagged(view?.untagged || false);
    setSelected(view?.selected || []);
    setOffsets(view?.offsets || {});
    setDetail(view?.detail || null);
    setDetailIndex(view?.detailIndex || 0);
    setAdding(null);
    if (nextScope) setLibrary(nextScope);
    else if (view?.library) setLibrary(view.library);
    pendingScroll.current = view?.scroll || 0;
  }
  function enterLibrary(id: string) {
    setCloudLinked(false);
    rememberView();
    restoreView(page === "archive" ? "archive" : "overview", id);
  }
  function changeCollection(
    mode: CollectionRoute["mode"],
    author: Author | null = null,
    group: "active" | "archive" = mode === "authors"
      ? collectionRoute.group || "active"
      : "active",
    source: Source | null = null,
  ) {
    rememberView();
    restoreView(page, scope, { mode, author, group, source });
    const enteringOther =
      group === "archive" && collectionRoute.group !== "archive" && !author;
    if (author || source || enteringOther) {
      const previous =
        views.current[routeKey(page, scope, { mode, author, group, source })];
      setTagFilters([...tagFilters]);
      setUntagged(untagged);
      if (enteringOther) {
        setQuery(query);
        setOffset(0);
        pendingScroll.current = 0;
      }
      if (
        !previous ||
        previous.untagged !== untagged ||
        JSON.stringify([...previous.tags].sort()) !==
          JSON.stringify([...tagFilters].sort())
      ) {
        setOffset(0);
        pendingScroll.current = 0;
      }
    }
  }
  async function authorPreferences(
    keys: string[],
    changes: { starred?: boolean; archived?: boolean },
  ) {
    if (!scope || authorBusy) return false;
    const libraryId = scope;
    setAuthorBusy(true);
    try {
      const before = await call<AuthorPreference[]>("author_preferences", {
        library_id: libraryId,
        keys,
        ...changes,
      });
      await latestRefresh.current();
      notify("作者设置已更新", () => {
        void (async () => {
          try {
            await call("author_preferences", {
              library_id: libraryId,
              keys,
              restore: before,
            });
            await latestRefresh.current();
            notify("已撤销");
          } catch (e) {
            notify((e as Error).message);
          }
        })();
      });
      return true;
    } catch (e) {
      notify((e as Error).message);
      return false;
    } finally {
      setAuthorBusy(false);
    }
  }
  function navigate(p: Page, root = false) {
    if (inCloud && p === page) leaveCloud();
    setCloudLinked(false);
    rememberView();
    const remembered = sectionScope.current[p] || null;
    const target =
      root || ((p === "overview" || p === "archive") && p === page && scope)
        ? null
        : remembered;
    restoreView(
      p,
      target && dataRef.current?.libraries.some((l) => l.id === target)
        ? target
        : null,
    );
    if (p === "overview" && root) {
      setResourceKind(null);
      pendingScroll.current = overviewRootScroll.current;
    }
    if (p === "downloads" || p === "telegram") setDownloadsOpen(true);
  }
  async function ignore(ids: string[], value = true) {
    if (await action("ignore", { ids, ignored: value })) {
      setSelected([]);
      notify(
        value
          ? "已忽略；已开始的媒体允许完成，其余跳过"
          : "已取消忽略，请手动继续下载",
        () => {
          void action("ignore", { ids, ignored: !value }, "已撤销忽略设置");
        },
      );
    }
  }
  function paginate(next: number) {
    pendingScroll.current = 0;
    setSelected([]);
    setOffset(next);
  }
  function openDetail(t: Tweet, index = 0) {
    setDetail(t);
    setDetailIndex(index);
  }
  async function start(mode: string, extra: Payload = {}) {
    await action(
      "start",
      { mode, library_id: library, ...extra },
      mode === "verify" ? "正在检查本地文件" : "任务已开始",
    );
  }
  useBackNavigation(() => {
    if (inCloud) leaveCloud();
    else if (cloudLinked) setCloudLinked(false);
    else if (scope && (page === "overview" || page === "archive")) {
      if (localView) changeCollection("tweets");
      else if (collectionRoute.source) changeCollection("sources");
      else if (collectionRoute.author)
        changeCollection("authors", null, collectionRoute.group);
      else if (collectionRoute.group === "archive")
        changeCollection("authors", null, "active");
      else {
        rememberView();
        restoreView(page, null);
      }
    } else if (page === "overview" && resourceKind) chooseResourceKind(null);
    else if (page === "organize" && toolOpen) setToolOpen(false);
    else if (page !== "overview") navigate("overview", true);
  });
  if (!data)
    return (
      <div className="boot-screen">
        <ArrowDownToLine size={32} />
        <h2>X2LT</h2>
        <p>{loadError || "正在载入收藏并整理旧记录…"}</p>
        {loadError && <Button onClick={refresh}>重试</Button>}
      </div>
    );
  const currentLibrary =
    data.libraries.find((l) => l.id === (scope || library)) ||
    data.libraries.find((l) => l.default);
  const showLocalContent = desktop && isCollection && !inCloud && !cloudLinked
    && !authorView && !sourceView && !collectionRoute.author && !collectionRoute.source;
  const localQueryKey = JSON.stringify([scope, query, tagFilters, untagged]);
  const visibleLocalCount = showLocalContent && localCount?.key === localQueryKey ? localCount.total : 0;
  function localChanged() {
    setLocalRevision((value) => value + 1);
    void latestRefresh.current();
  }
  function openCloudArchive(useSelection = false) {
    if (!currentLibrary) return;
    setCloudArchive({
      libraryId: currentLibrary.id,
      libraryName: currentLibrary.name,
      selected: [...selected],
      useSelection,
    });
  }
  const selectedMedia = detail?.media[detailIndex];
  const cloudDetail = selectedMedia && cloudPositions[selectedMedia.id];
  const selectedUpload = data.tasks.filter((task) => selected.includes(task.id))
    .flatMap((task) => groups(task.media))
    .map((versions) => representative(versions))
    .filter((media) => media.state === "completed" && (!cloudPositions[media.id] || cloudPositions[media.id].locations.some((location) => location.kind === "local" && isSavedLocation(location))));
  const speed = Object.values(progress).reduce(
    (n, p) => n + (Date.now() - p.received < 2000 ? p.speed : 0),
    0,
  );
  const pickLibrary = (
    <select
      aria-label="目标媒体库"
      className="library-select"
      value={library}
      onChange={(e) => setLibrary(e.target.value)}
    >
      {!data.libraries.length && <option value="">请先添加媒体库</option>}
      {data.libraries.map((l) => (
        <option value={l.id} key={l.id}>
          {l.emoji || "📁"} {l.name}
          {l.online ? "" : "（离线）"}
        </option>
      ))}
    </select>
  );
  function card(t: Tweet) {
    const media = groups(t.media);
    const shift = (delta: number) =>
      setOffsets((prev) => ({
        ...prev,
        [t.id]: Math.max(
          0,
          Math.min(media.length - 3, (prev[t.id] || 0) + delta),
        ),
      }));
    const off = Math.min(offsets[t.id] || 0, Math.max(0, media.length - 3));
    return (
      <article
        key={t.id}
        className={"tweet-card " + (selected.includes(t.id) ? "selected" : "")}
        onContextMenu={(e) => {
          if ((e.target as HTMLElement).closest("input,textarea,a")) return;
          e.preventDefault();
          setContextTarget({
            ids: selected.includes(t.id) ? selected : [t.id],
            x: e.clientX,
            y: e.clientY,
          });
        }}
      >
        <Tags
          tweet={t}
          options={data!.tag_options || []}
          action={action}
          onFilter={(tag) => {
            setTagFilters((prev) =>
              prev.includes(tag) ? prev : [...prev, tag],
            );
            setOffset(0);
          }}
        />
        <div className="tweet-head">
          {(page === "downloads" || isCollection) && (
            <button
              className={
                "checkbox " + (selected.includes(t.id) ? "checked" : "")
              }
              aria-label={`选择内容 ${t.id}`}
              aria-pressed={selected.includes(t.id)}
              onClick={() =>
                setSelected((prev) =>
                  prev.includes(t.id)
                    ? prev.filter((id) => id !== t.id)
                    : [...prev, t.id],
                )
              }
            >
              {selected.includes(t.id) && <Check size={12} />}
            </button>
          )}
          <ContentIdentity content={t} action={action} />
          {page === "downloads" && (
            <span className={"status " + (done(t) ? "success" : "muted")}>
              {t.ignored ? (
                <EyeOff size={12} />
              ) : done(t) ? (
                <CheckCircle2 size={12} />
              ) : (
                <span className="dot" />
              )}
              {t.ignored
                ? "已忽略"
                : done(t)
                  ? "已完成"
                  : t.media.some((m) => m.state === "downloading")
                    ? "下载中"
                    : t.media.some((m) => m.state === "completed")
                      ? "部分完成"
                      : "待完成"}
            </span>
          )}
        </div>
        {isCollection ? (
          <MediaStack
            count={media.length}
            index={offsets[t.id] || 0}
            onChange={(index) =>
              setOffsets((prev) => ({ ...prev, [t.id]: index }))
            }
            render={(index, swipe) => {
              const versions = media[index];
              const m = representative(versions);
              return (
                <div key={m.id} className="tile-wrap">
                  <Thumbnail
                    media={m}
                    tweet={t}
                    index={index}
                    progress={progress[m.id]}
                    onSwipe={swipe}
                    onClick={() =>
                      openDetail(
                        t,
                        t.media.findIndex((row) => row.id === m.id),
                      )
                    }
                  />
                  {versions.length > 1 && (
                    <span className="version-count">
                      {versions.length} 个版本
                    </span>
                  )}
                </div>
              );
            }}
          />
        ) : (
          <>
            <div
              className="media-grid"
              onWheel={(e) => {
                if (e.shiftKey || Math.abs(e.deltaX) > 20)
                  shift((e.deltaX || e.deltaY) > 0 ? 1 : -1);
              }}
            >
              {media.slice(off, off + 3).map((versions, i) => {
                const m = representative(versions);
                return (
                  <div
                    className="tile-wrap"
                    key={m.media_id + ":" + m.media_type}
                  >
                    <Thumbnail
                      media={m}
                      tweet={t}
                      index={off + i}
                      compact
                      progress={progress[m.id]}
                      onSwipe={shift}
                      onClick={() =>
                        openDetail(
                          t,
                          t.media.findIndex((row) => row.id === m.id),
                        )
                      }
                    />
                    {versions.length > 1 && (
                      <span className="version-count">
                        {versions.length} 个版本
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
        <footer className="tweet-footer">
          <span>
            <Film size={13} />
            {media.filter((g) => g[0].media_type === "video").length} 视频
            <i>·</i>
            <Image size={13} />
            {media.filter((g) => g[0].media_type === "image").length} 图片
            <span className="footer-divider" />
            {media.filter((g) => g.some((m) => m.state === "completed")).length}
            /{media.length} 已保存
          </span>
          {cloudEnabled && t.media.some((item) => cloudPositions[item.id]) && <CloudStorageBadges
            groups={media.map((versions) => versions.flatMap((version) => cloudPositions[version.id]?.locations || []))}
            total={media.length}
            onOpen={locateCloudPosition}
          />}
          <div className="card-actions">
            <ContentMenu onRemove={() => setRemoveIds([t.id])} />
            {!isCollection && media.length > 3 && (
              <div className="pager">
                <small>
                  {off + 1}–{Math.min(off + 3, media.length)} / {media.length}
                </small>
                <button
                  className="icon-btn"
                  aria-label="上一组媒体"
                  disabled={!off}
                  onClick={() => setOffsets({ ...offsets, [t.id]: off - 1 })}
                >
                  <ChevronLeft size={14} />
                </button>
                <button
                  className="icon-btn"
                  aria-label="下一组媒体"
                  disabled={off + 3 >= media.length}
                  onClick={() => setOffsets({ ...offsets, [t.id]: off + 1 })}
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            )}
            {page === "downloads" && (
              <button
                className="text-btn"
                onClick={() => ignore([t.id], !t.ignored)}
              >
                {t.ignored ? <Undo2 size={13} /> : <EyeOff size={13} />}{" "}
                {t.ignored ? "取消忽略" : "忽略"}
              </button>
            )}
            <button className="text-btn" onClick={() => openDetail(t)}>
              详情
              <ArrowUpRight size={13} />
            </button>
          </div>
        </footer>
      </article>
    );
  }
  return (
    <PrivacyProvider
      enabled={!!data.settings.privacy_mode}
      scopeKey={
        routeKey(page, scope, collectionRoute) +
        ":" +
        (resourceKind || "") +
        ":" +
        toolOpen
      }
    >
      <div className="app">
        <aside className="sidebar">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              navigate("downloads");
            }}
          >
            <span className="brand-mark">
              <ArrowDownToLine size={22} />
            </span>
            <strong>X2LT</strong>
          </a>
          <div className="workspace-label">工作空间</div>
          <nav>
            {(
              [
                ["overview", LayoutDashboard, "总览"],
                ["archive", FolderOpen, "媒体库"],
                ["organize", Layers, "媒体工具"],
              ] as const
            ).map(([key, Icon, label], index) => (
              <div key={key}>
                {index === 1 && (
                  <>
                    <button
                      className="nav-download-group"
                      aria-label="下载任务"
                      aria-expanded={downloadsOpen}
                      onClick={() => setDownloadsOpen((v) => !v)}
                    >
                      <ArrowDownToLine size={18} />
                      下载任务
                      <ChevronRight
                        size={14}
                        style={{
                          transform: downloadsOpen
                            ? "rotate(90deg)"
                            : undefined,
                        }}
                      />
                    </button>
                    {downloadsOpen && (
                      <div className="download-children">
                        <button
                          className={page === "downloads" ? "active" : ""}
                          onClick={() => navigate("downloads")}
                        >
                          推特下载
                        </button>
                        <button
                          className={page === "telegram" ? "active" : ""}
                          onClick={() => navigate("telegram")}
                        >
                          电报下载
                        </button>
                      </div>
                    )}
                  </>
                )}
                <button
                  className={page === key ? "active" : ""}
                  onClick={() => navigate(key)}
                >
                  <Icon size={18} />
                  {label}
                </button>
              </div>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <button
              className={
                page === "settings" ? "side-settings active" : "side-settings"
              }
              onClick={() => navigate("settings")}
            >
              <SettingsIcon size={18} />
              设置
              <ChevronRight size={14} />
            </button>
            <div className="sidebar-foot">
              <span className="user-avatar">XL</span>
              <div>
                <strong>本地工作空间</strong>
                <small>{desktop ? "Windows 桌面版" : "交互原型"}</small>
              </div>
              <button
                className="icon-btn"
                aria-label={
                  data.settings.theme === "dark" ? "切换浅色" : "切换深色"
                }
                onClick={() =>
                  save({
                    theme: data.settings.theme === "dark" ? "light" : "dark",
                  })
                }
              >
                {data.settings.theme === "dark" ? (
                  <Sun size={17} />
                ) : (
                  <Moon size={17} />
                )}
              </button>
            </div>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="breadcrumb">
              工作空间
              <ChevronRight size={13} />
              <span>{inCloud ? "光鸭云盘" : scope ? currentLibrary?.name : titles[page]}</span>
            </div>
            <div className="top-right">
              <span className="prototype-pill">
                <span className="dot" />
                {desktop ? "本地连接 · " + data.version : "交互原型 · 模拟数据"}
              </span>
              {!desktop && (
                <button
                  className="icon-btn"
                  aria-label="重置演示"
                  onClick={() => action("demo_reset", {}, "演示设置已重置")}
                >
                  <RefreshCw size={16} />
                </button>
              )}
            </div>
          </header>
          <main>
            {inCloud && (
              <div className="library-breadcrumb">
                <button onClick={leaveCloud}>{titles[page]}</button>
                <span>/ 光鸭云盘</span>
              </div>
            )}
            {page === "downloads" && (
              <OperationStatus
                operation={operation}
                onPause={() => void action("pause")}
                onOpen={() => navigate("downloads")}
              />
            )}
            {scope && (
              <>
                <div className="library-breadcrumb">
                  <button onClick={() => navigate(page, true)}>
                    {titles[page]}
                  </button>
                  {collectionRoute.author ||
                  collectionRoute.group === "archive" ? (
                    <>
                      <button
                        aria-label={
                          collectionRoute.group === "archive"
                            ? "返回库作者列表"
                            : "返回作者列表"
                        }
                        onClick={() =>
                          changeCollection("authors", null, "active")
                        }
                      >
                        / {currentLibrary?.name}
                      </button>
                      {collectionRoute.group === "archive" &&
                        (collectionRoute.author ? (
                          <button
                            aria-label="返回作者列表"
                            onClick={() =>
                              changeCollection("authors", null, "archive")
                            }
                          >
                            / 归档作者
                          </button>
                        ) : (
                          <span>/ 归档作者</span>
                        ))}
                      {collectionRoute.author && (
                        <span>
                          /{" "}
                          {collectionRoute.author.display_name ||
                            collectionRoute.author.handle}
                        </span>
                      )}
                    </>
                  ) : (
                    <span>/ {currentLibrary?.name}</span>
                  )}
                </div>
                {collectionRoute.source && (
                  <div className="library-breadcrumb">
                    <button onClick={() => changeCollection("sources")}>
                      返回来源列表
                    </button>
                    <span>/ {collectionRoute.source.label}</span>
                  </div>
                )}
                {currentLibrary && (
                  <div className="library-summary">
                    <LibraryCover library={currentLibrary} />
                    <div>
                      <strong>{currentLibrary.name}</strong>
                      <p>
                        {currentLibrary.message_count ?? 0} 条内容 ·{" "}
                        {currentLibrary.media_count ?? 0} 个媒体 · 可用{" "}
                        {bytes(currentLibrary.available_bytes)}
                      </p>
                      {!currentLibrary.online && (
                        <p>媒体库离线 · 可浏览已有记录</p>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
            <div className="page-heading">
              <div>
                <h1>
                  {inCloud ? "光鸭云盘" : scope
                    ? collectionRoute.source?.label ||
                      collectionRoute.author?.display_name ||
                      collectionRoute.author?.handle ||
                      (collectionRoute.group === "archive" ? "归档作者" : "") ||
                      currentLibrary?.name
                    : page === "overview" && resourceKind
                      ? "所有资源"
                      : page === "organize" && toolOpen
                        ? "媒体转移"
                        : titles[page]}
                </h1>
              </div>
              {page === "downloads" && (
                <div className="download-entry-actions">
                  <Button variant="outline" onClick={() => setAdding("link")}>
                    链接下载
                  </Button>
                  <Button onClick={() => setAdding("likes")}>
                    <RefreshCw size={16} />
                    同步点赞
                  </Button>
                </div>
              )}
              {isCollection && !inCloud && currentLibrary && (
                <div className="local-heading-actions">
                  {cloudEnabled && currentLibrary.online && (
                    <Button variant="outline" onClick={() => openCloudArchive()}>
                      按层级上传到光鸭
                    </Button>
                  )}
                  {showLocalContent && (
                    <>
                      <Button variant="outline" disabled={!currentLibrary.online} onClick={() => setLocalScan(currentLibrary)}>
                        扫描此库
                      </Button>
                      <Button onClick={() => setLocalImport(currentLibrary)}>导入本地</Button>
                    </>
                  )}
                </div>
              )}
            </div>
            {(loadError || data.credential_error) && (
              <div className="error-banner">
                {loadError || data.credential_error}
              </div>
            )}
            {isCollection && (
              <div className="collection-stats">
                <span>
                  收藏内容 <strong>{currentLibrary?.message_count ?? 0}</strong>
                </span>
                <span>
                  已保存媒体 <strong>{currentLibrary?.media_count ?? 0}</strong>
                </span>
                {!!data.collection_author_count && (
                  <span>
                    作者 <strong>{data.collection_author_count}</strong>
                  </span>
                )}
              </div>
            )}
            {page === "downloads" && (
              <div className="stats">
                <div className="stat">
                  <span className="stat-icon">
                    <ArrowDownToLine size={19} />
                  </span>
                  <div>
                    <span>当前下载</span>
                    <strong>
                      {busy ? (speed / 1024 ** 2).toFixed(1) : "0.0"}
                      <small>MB/s</small>
                    </strong>
                  </div>
                  <div className={"spark " + (busy ? "moving" : "")}>
                    {[8, 12, 9, 17, 13, 23, 16, 27, 21, 30, 25, 19].map(
                      (n, i) => (
                        <i key={i} style={{ height: n }} />
                      ),
                    )}
                  </div>
                </div>
                <div className="stat">
                  <span className="stat-icon">
                    <Layers size={19} />
                  </span>
                  <div>
                    <span>待完成内容</span>
                    <strong>
                      {data.counts.pending}
                      <small>条</small>
                    </strong>
                  </div>
                </div>
                <div className="stat">
                  <span className="stat-icon">
                    <CheckCheck size={19} />
                  </span>
                  <div>
                    <span>已收藏内容</span>
                    <strong>
                      {data.counts.completed}
                      <small>条</small>
                    </strong>
                  </div>
                  <span className="stat-note positive">
                    <ShieldCheck size={13} />
                    本地保存
                  </span>
                </div>
              </div>
            )}
            {isCollection && cloudStatus && (
              <div className="cloud-actions" aria-label="媒体库内容类型">
                <Button variant={cloudLinked ? "outline" : "default"} size="sm" onClick={() => setCloudLinked(false)}>库内内容</Button>
                <Button variant={cloudLinked ? "default" : "outline"} size="sm" onClick={() => setCloudLinked(true)}>跨库收藏</Button>
              </div>
            )}
            {cloudStatus && (inCloud || (isCollection && cloudLinked)) && (
              <CloudLibrary
                key={inCloud ? "guangyapan" : scope}
                status={cloudStatus}
                libraries={data.libraries}
                libraryId={inCloud ? undefined : scope ?? undefined}
                action={action}
                locate={locateCloudPosition}
                refreshStatus={refreshCloudStatus}
              />
            )}
            {(page === "downloads" || (isCollection && !cloudLinked)) && (
              <>
                <div className="list-toolbar">
                  <div className="tabs">
                    {isCollection ? (
                      <>
                        <button
                          className={
                            collectionRoute.mode === "tweets" ? "active" : ""
                          }
                          onClick={() =>
                            changeCollection("tweets", null, "active")
                          }
                        >
                          内容
                        </button>
                        {desktop && (
                          <button
                            className={localView ? "active" : ""}
                            onClick={() => changeCollection("local")}
                          >
                            本地导入
                          </button>
                        )}
                        {!!data.collection_author_count && (
                          <>
                            <button
                              aria-label="按作者聚类"
                              className={
                                collectionRoute.mode === "authors"
                                  ? "active"
                                  : ""
                              }
                              onClick={() =>
                                changeCollection("authors", null, "active")
                              }
                            >
                              作者
                            </button>
                          </>
                        )}
                        <button
                          aria-label="按来源分类"
                          className={
                            collectionRoute.mode === "sources" ? "active" : ""
                          }
                          onClick={() => changeCollection("sources")}
                        >
                          来源
                        </button>
                      </>
                    ) : (
                      (page === "archive"
                        ? [["completed", "已保存"]]
                        : [
                            ["all", "全部"],
                            ["pending", "未完成"],
                            ["completed", "已完成"],
                            ["ignored", "已忽略"],
                          ]
                      ).map(([key, label]) => (
                        <button
                          className={
                            page === "archive" || filter === key ? "active" : ""
                          }
                          key={key}
                          onClick={() => {
                            setFilter(key as Filter);
                            setOffset(0);
                            setSelected([]);
                          }}
                        >
                          {label}
                          <span>{data.counts[key as Filter]}</span>
                        </button>
                      ))
                    )}
                  </div>
                  <div className="toolbar-right">
                    {authorView && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          changeCollection(
                            "authors",
                            null,
                            collectionRoute.group === "archive"
                              ? "active"
                              : "archive",
                          )
                        }
                      >
                        {collectionRoute.group === "archive"
                          ? "返回作者"
                          : "打开归档"}
                      </Button>
                    )}
                    <div className="search">
                      <Search size={15} />
                      <input
                        aria-label={
                          authorView
                            ? "搜索作者"
                            : sourceView
                              ? "搜索来源"
                              : "搜索内容"
                        }
                        placeholder={
                          authorView
                            ? "搜索作者名称、账号…"
                            : sourceView
                              ? "搜索来源…"
                              : "搜索内容、作者、文件名…"
                        }
                        value={query}
                        onChange={(e) => {
                          setQuery(e.target.value);
                          setOffset(0);
                        }}
                      />
                      {query && (
                        <button
                          aria-label="清除搜索"
                          onClick={() => setQuery("")}
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
                <TagFilters
                  options={data.tag_options || []}
                  selected={tagFilters}
                  untagged={untagged}
                  onChange={(tags) => {
                    setTagFilters(tags);
                    setOffset(0);
                  }}
                  onUntagged={(value) => {
                    setUntagged(value);
                    setOffset(0);
                  }}
                  action={action}
                />
                {(page === "downloads" ||
                  (isCollection && !authorView && !sourceView && !localView && !!data.tasks.length)) && (
                  <div className="selection-toolbar">
                    <button
                      className={
                        "checkbox " +
                        (data.tasks.length &&
                        data.tasks.every((t) => selected.includes(t.id))
                          ? "checked"
                          : "")
                      }
                      aria-label="全选当前页"
                      onClick={() =>
                        setSelected(
                          data.tasks.every((t) => selected.includes(t.id))
                            ? []
                            : [
                                ...new Set([
                                  ...selected,
                                  ...data.tasks.map((t) => t.id),
                                ]),
                              ],
                        )
                      }
                    >
                      {!!data.tasks.length &&
                        data.tasks.every((t) => selected.includes(t.id)) && (
                          <Check size={12} />
                        )}
                    </button>
                    <span>
                      {selected.length
                        ? `已选 ${selected.length} 条`
                        : `${data.total} 条内容`}
                    </span>
                    {!!selected.length && (
                      <>
                        {isCollection && cloudEnabled && currentLibrary?.online ? (
                          <Button size="sm" variant="outline" onClick={() => openCloudArchive(true)}>按层级上传所选</Button>
                        ) : cloudEnabled && !!selectedUpload.length && (
                          <Button size="sm" variant="outline" onClick={() => setCloudUpload(selectedUpload)}>批量上传到光鸭</Button>
                        )}
                        {page === "downloads" && (
                          <button
                            className="text-btn"
                            onClick={() =>
                              ignore(selected, filter !== "ignored")
                            }
                          >
                            {filter === "ignored" ? (
                              <Undo2 size={13} />
                            ) : (
                              <EyeOff size={13} />
                            )}
                            批量{filter === "ignored" ? "取消忽略" : "忽略"}
                          </button>
                        )}
                        <button
                          className="text-btn"
                          onClick={() => setRemoveIds(selected)}
                        >
                          批量移除
                        </button>
                        <button
                          className="text-btn"
                          onClick={() => setSelected([])}
                        >
                          取消选择
                        </button>
                      </>
                    )}
                    <span className="selection-spacer" />
                    {page === "downloads" && filter !== "ignored" && (
                      <>
                        <div className="target-picker">
                          <FolderOpen size={13} />
                          {pickLibrary}
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={transition}
                          onClick={() =>
                            busy
                              ? action("pause", {}, "正在暂停并保存进度")
                              : start("continue")
                          }
                        >
                          {busy ? <Pause size={14} /> : <Play size={14} />}{" "}
                          {busy ? "暂停下载" : "继续下载"}
                        </Button>
                      </>
                    )}
                  </div>
                )}
                {isCollection && (
                  <div className="collection-result-count">
                    {(localView ? 0 : data.total) + visibleLocalCount}{" "}
                    {sourceView
                      ? "个来源"
                      : authorView
                        ? collectionRoute.group === "archive"
                          ? "位作者"
                          : "个条目"
                        : "条内容"}
                  </div>
                )}
                <div
                  className={
                    isCollection && !authorView && !sourceView
                      ? "content-region"
                      : ""
                  }
                >
                  {showLocalContent && currentLibrary && (
                    <LocalLibrary
                      key={currentLibrary.id}
                      libraryId={currentLibrary.id}
                      libraries={data.libraries}
                      query={query}
                      tags={tagFilters}
                      untagged={untagged}
                      revision={localRevision}
                      changed={localChanged}
                      onTotal={(total) => setLocalCount({ key: localQueryKey, total })}
                      notify={notify}
                      standalone={localView}
                    />
                  )}
                  {!localView && <div
                    ref={contentList}
                    className={
                      "task-list " +
                      (isCollection && !authorView && !sourceView
                        ? "collection-content-list"
                        : "")
                    }
                  >
                    {sourceView ? (
                      <div className="source-grid">
                        {data.sources?.map((source) => (
                          <button
                            className="source-card"
                            key={source.key}
                            aria-label={"查看来源 " + source.label}
                            onClick={() =>
                              changeCollection(
                                "sources",
                                null,
                                "active",
                                source,
                              )
                            }
                          >
                            <strong>{source.label}</strong>
                            <small>
                              {source.message_count} 条内容 ·{" "}
                              {source.media_count} 个媒体
                            </small>
                          </button>
                        ))}
                      </div>
                    ) : authorView ? (
                      <Authors
                        authors={data.authors || []}
                        onOpen={(author) => changeCollection("authors", author)}
                        archived={collectionRoute.group === "archive"}
                        onArchive={(author) =>
                          void authorPreferences([author.key], {
                            archived: collectionRoute.group !== "archive",
                          })
                        }
                        onProfile={(handle) =>
                          void action("open_profile", { handle })
                        }
                        busy={authorBusy}
                        onStar={(author) =>
                          void authorPreferences([author.key], {
                            starred: !author.starred,
                          })
                        }
                      />
                    ) : (
                      data.tasks.map(card)
                    )}
                    {!(sourceView
                      ? data.sources?.length
                      : authorView
                        ? data.authors?.length
                        : data.tasks.length) && !visibleLocalCount && (
                      <div className="empty">
                        <FolderOpen size={35} />
                        <h3>
                          {sourceView
                            ? "暂无匹配的来源"
                            : authorView
                              ? "暂无匹配的作者"
                              : query
                                ? "没有匹配的内容"
                                : "这里暂时没有内容"}
                        </h3>
                        <p>
                          {isCollection
                            ? "调整搜索或标签，或导入本地媒体。"
                            : "切换分类，或添加一条下载任务。"}
                        </p>
                      </div>
                    )}
                  </div>}
                </div>
                {!localView && data.total > 50 && (
                  <div className="pagination">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!offset}
                      onClick={() => paginate(Math.max(0, offset - 50))}
                    >
                      上一页
                    </Button>
                    <span>
                      {offset + 1}–{Math.min(offset + 50, data.total)} /{" "}
                      {data.total}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={offset + 50 >= data.total}
                      onClick={() => paginate(offset + 50)}
                    >
                      下一页
                    </Button>
                  </div>
                )}
              </>
            )}
            <div hidden={page !== "overview" || !!scope || inCloud}>
              <ResourceBrowser
                libraries={data.libraries}
                kind={resourceKind}
                onKind={chooseResourceKind}
                onLibraries={() => navigate("archive", true)}
                options={data.tag_options || []}
                revision={JSON.stringify(
                  data.libraries.map((l) => [l.id, l.media_count, l.path]),
                )}
                active={page === "overview" && !scope && !inCloud}
              />
            </div>
            {(page === "overview" || page === "archive") &&
              !scope &&
              !inCloud &&
              !(page === "overview" && resourceKind) && (
                <Collections
                  libraries={data.libraries}
                  action={action}
                  onEnter={enterLibrary}
                  onScan={desktop ? setLocalScan : undefined}
                  cloudCard={cloudStatus ? <CloudLibraryCard status={cloudStatus} onOpen={showCloud}
                    name={data.settings.cloud_card_name} emoji={data.settings.cloud_card_emoji} save={save} /> : undefined}
                  onAddCloud={cloudStatus ? showCloud : undefined}
                  onAdd={() => {
                    navigate("settings");
                    setSettingsTab("libraries");
                  }}
                />
              )}
            {page === "organize" && !toolOpen && (
              <button className="tool-entry" onClick={() => setToolOpen(true)}>
                <Layers size={30} />
                <strong>媒体转移</strong>
                <ChevronRight size={18} />
              </button>
            )}
            {page === "organize" && toolOpen && (
              <>
                <div className="library-breadcrumb">
                  <button onClick={() => setToolOpen(false)}>媒体工具</button>
                  <span>/ 媒体转移</span>
                </div>
                <section className="settings-card">
                  {organizeProgress && (
                    <div className="organize-progress" aria-live="polite">
                      <span>
                        {organizeProgress.text} {organizeProgress.done} /{" "}
                        {organizeProgress.total}
                      </span>
                      <progress
                        aria-label="整理进度"
                        max={Math.max(1, organizeProgress.total)}
                        value={organizeProgress.done}
                      />
                    </div>
                  )}
                  <div className="section-icon">
                    <Layers size={26} />
                  </div>
                  <div className="folder-path">
                    <FolderOpen size={18} />
                    {pickLibrary}
                  </div>
                  <div className="row-actions">
                    <Button
                      disabled={busy || transition}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library },
                          "开始整理媒体",
                        )
                      }
                    >
                      <Layers size={15} />
                      集中转移
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || transition}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library, rollback: true },
                          "开始回退",
                        )
                      }
                    >
                      <Undo2 size={15} />
                      转移回退
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => start("verify")}
                    >
                      完整性检查
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library, rollback: true, search: true },
                          "开始查找并回退",
                        )
                      }
                    >
                      查找并回退
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        action(
                          "organize",
                          { library_id: library, custom_target: true },
                          "开始整理；目标需位于已配置媒体库内",
                        )
                      }
                    >
                      选择整理位置
                    </Button>
                  </div>
                </section>
              </>
            )}
            {page === "telegram" && (
              <section className="telegram-placeholder">
                <h2>电报下载暂未开发</h2>
              </section>
            )}
            {page === "settings" && (
              <>
                <div className="settings-tabs tabs">
                  {[
                    ["appearance", "外观"],
                    ["libraries", "媒体库"],
                    ["cloud", "光鸭云盘"],
                    ["account", "账号与下载"],
                    ["privacy", "隐私"],
                    ["management", "媒体管理"],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      className={settingsTab === id ? "active" : ""}
                      onClick={() => setSettingsTab(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {settingsTab === "management" ? (
                  <FiltersPanel
                    confirm={data.settings.confirm_removal !== false}
                    save={save}
                  />
                ) : settingsTab === "privacy" ? (
                  <PrivacyPanel
                    enabled={!!data.settings.privacy_mode}
                    save={save}
                  />
                ) : settingsTab === "cloud" ? (
                  <section className="settings-card">
                    <h2>目录缓存</h2>
                    <label className="privacy-setting">
                      <input
                        type="checkbox"
                        checked={!!data.settings.cloud_prefetch}
                        disabled={transition}
                        onChange={(event) => void save({ cloud_prefetch: event.target.checked })}
                      />
                      启动后自动预读光鸭目录
                    </label>
                    <p>连接光鸭后在后台读取目录和子目录，缓存保留到软件退出。</p>
                  </section>
                ) : settingsTab === "appearance" ? (
                  <ThemePanel settings={data.settings} save={save} />
                ) : settingsTab === "libraries" ? (
                  <LibraryPanel libraries={data.libraries} action={action} />
                ) : (
                  <AccountPanel
                    settings={data.settings}
                    save={save}
                    action={action}
                    hasCredentials={data.has_credentials}
                  />
                )}
              </>
            )}
            <div className="page-foot">
              <span>X2LT / {data.version}</span>
            </div>
          </main>
        </div>
        <ContentContextMenu
          target={contextTarget}
          close={() => setContextTarget(null)}
          remove={setRemoveIds}
        />
        <RemovalDialog
          ids={removeIds}
          libraryId={scope || library}
          libraryName={currentLibrary?.name || "当前媒体库"}
          confirm={data.settings.confirm_removal !== false}
          close={() => setRemoveIds(null)}
          save={save}
          changed={() => {
            setSelected([]);
            setDetail(null);
            void latestRefresh.current();
            notify("正在移除；结果与重试可在设置的过滤媒体名单查看");
          }}
        />
        <DownloadDialog
          mode={adding}
          close={() => setAdding(null)}
          library={library}
          picker={pickLibrary}
          current={currentLibrary}
          action={action}
          busy={busy}
          transition={transition}
          hasCredentials={data.has_credentials}
          username={data.settings.username}
          configure={() => {
            setAdding(null);
            navigate("settings");
            setSettingsTab("account");
          }}
          downloaded={() => navigate("downloads")}
        />
        <Modal
          drawer
          open={!!detail}
          onClose={() => setDetail(null)}
          title="内容详情"
        >
          {detail && selectedMedia && (
            <>
              <div className="drawer-body">
                <div className="detail-author">
                  <ContentIdentity
                    content={detail}
                    action={action}
                    body={false}
                  />
                </div>
                <Tags
                  tweet={detail}
                  options={data.tag_options || []}
                  action={action}
                  onFilter={(tag) => {
                    setTagFilters((prev) =>
                      prev.includes(tag) ? prev : [...prev, tag],
                    );
                    setOffset(0);
                  }}
                />
                <PrivateText text={detail.text || ""} />
                <Thumbnail
                  detail
                  media={selectedMedia}
                  tweet={detail}
                  index={detailIndex}
                  progress={progress[selectedMedia.id]}
                  onClick={() =>
                    action(
                      "open_media",
                      { job_id: selectedMedia.id },
                      desktop ? undefined : "原型播放演示：未打开本地文件",
                    )
                  }
                />
                <div className="detail-navigation">
                  <button
                    className="icon-btn"
                    aria-label="上一个媒体"
                    disabled={!detailIndex}
                    onClick={() => setDetailIndex(detailIndex - 1)}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <span>
                    {detailIndex + 1} / {detail.media.length}（含画质及库版本）
                  </span>
                  <button
                    className="icon-btn"
                    aria-label="下一个媒体"
                    disabled={detailIndex + 1 >= detail.media.length}
                    onClick={() => setDetailIndex(detailIndex + 1)}
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
                <select
                  className="detail-version"
                  aria-label="媒体版本"
                  value={detailIndex}
                  onChange={(e) => setDetailIndex(Number(e.target.value))}
                >
                  {detail.media.map((m, i) => (
                    <option key={m.id} value={i}>
                      {m.media_type === "video" ? "视频" : "图片"} {m.media_id}{" "}
                      · {m.quality} · {m.output}
                    </option>
                  ))}
                </select>
                <h3>媒体信息</h3>
                <dl>
                  <div>
                    <dt>状态</dt>
                    <dd>{labels[selectedMedia.state]}</dd>
                  </div>
                  <div>
                    <dt>大小</dt>
                    <dd>{bytes(selectedMedia.bytes)}</dd>
                  </div>
                  <div>
                    <dt>完整性检查</dt>
                    <dd>{selectedMedia.verification || "下载后校验"}</dd>
                  </div>
                  <div>
                    <dt>保存位置</dt>
                    <dd className="path">{selectedMedia.output}</dd>
                  </div>
                </dl>
                {selectedMedia.error && (
                  <div className="error-banner">{selectedMedia.error}</div>
                )}
                <div className="detail-tools">
                  {cloudEnabled && selectedMedia.state === "completed" && (!cloudDetail || cloudDetail.locations.some((location) => location.kind === "local" && isSavedLocation(location))) && (
                    <Button size="sm" variant="outline" onClick={() => setCloudUpload([selectedMedia])}>上传到光鸭</Button>
                  )}
                  {page === "downloads" && (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          start("enrich", { job_id: selectedMedia.id })
                        }
                        disabled={busy}
                      >
                        补全预览
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          start("force", { job_id: selectedMedia.id })
                        }
                        disabled={busy || detail.ignored}
                      >
                        强制重新下载
                      </Button>
                    </>
                  )}
                  {contentPresentation(detail).canSource && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        action("open_tweet", { job_id: selectedMedia.id })
                      }
                    >
                      <ExternalLink size={12} />
                      查看来源
                    </Button>
                  )}
                </div>
                {cloudDetail && <CloudItemTools
                  item={cloudDetail}
                  libraries={data.libraries}
                  action={action}
                  locate={locateCloudPosition}
                  refresh={cloudChanged}
                />}
                {page === "downloads" && (
                  <div className="info-note">
                    <Info size={15} />
                    忽略不删除已有文件。取消忽略后，手动继续下载。
                  </div>
                )}
              </div>
              <footer>
                {page === "downloads" && (
                  <Button
                    variant="outline"
                    onClick={() => ignore([detail.id], !detail.ignored)}
                  >
                    {detail.ignored ? (
                      <Undo2 size={15} />
                    ) : (
                      <EyeOff size={15} />
                    )}{" "}
                    {detail.ignored ? "取消忽略" : "忽略内容"}
                  </Button>
                )}
                {(!cloudDetail || cloudDetail.locations.some((position) => position.kind === "local" && isSavedLocation(position))) && <Button
                  onClick={() =>
                    action(
                      "open_folder",
                      { job_id: selectedMedia.id },
                      desktop ? undefined : "示例文件夹 · 未访问本地文件",
                    )
                  }
                >
                  <FolderOpen size={15} />
                  打开文件夹
                </Button>}
              </footer>
            </>
          )}
        </Modal>
        {cloudTarget !== null && <CloudLocateDialog parent={cloudTarget} close={() => setCloudTarget(null)} />}
        {localImport && (
          <LocalImportDialog library={localImport} close={() => setLocalImport(null)} changed={localChanged} />
        )}
        {localScan && (
          <LocalImportDialog library={localScan} scanCurrentPath close={() => setLocalScan(null)} changed={localChanged} />
        )}
        {cloudArchive && cloudStatus && <CloudArchiveDialog
          target={cloudArchive}
          status={cloudStatus}
          refreshStatus={refreshCloudStatus}
          close={() => setCloudArchive(null)}
          started={(count) => {
            setCloudArchive(null);
            cloudChanged();
            notify(count > 0 ? `已加入光鸭队列，共 ${count} 个待核对媒体` : "没有需要上传或核对的媒体");
          }}
        />}
        {cloudUpload && cloudStatus && <CloudUploadDialog
          count={cloudUpload.length}
          size={cloudUpload.reduce((sum, item) => sum + item.bytes, 0)}
          close={() => setCloudUpload(null)}
          status={cloudStatus}
          refreshStatus={refreshCloudStatus}
          send={async (parent) => {
            const ok = await action("cloud_upload", { ids: cloudUpload.map((media) => media.id), parent }, "已加入光鸭上传队列");
            if (ok) cloudChanged();
            return ok;
          }}
        />}
        {!adding && !detail && <ScrollNavigation />}
        {toast && (
          <div className="toast" role="status">
            <CheckCircle2 size={18} />
            <span>{toast.text}</span>
            {toast.undo && <button onClick={toast.undo}>撤销</button>}
            <button aria-label="关闭提示" onClick={() => setToast(null)}>
              <X size={14} />
            </button>
          </div>
        )}
      </div>
    </PrivacyProvider>
  );
}
