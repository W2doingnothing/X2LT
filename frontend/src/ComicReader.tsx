import { useEffect, useRef, useState } from "react";
import { call } from "./api";
import { comicPages, turnComicPage } from "./comicNavigation";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { usePrivateReveal } from "./Privacy";
import { wheelStep, type WheelState } from "./stackMotion";
import "./comic-reader.css";

type Page = { id: string; name: string };
type Comic = {
  id: number;
  title: string;
  pages: Page[];
  page: number;
  spread: boolean;
  rtl: boolean;
  cover: string | null;
};
type Change = {
  page?: number;
  spread?: boolean;
  rtl?: boolean;
  order?: string[];
  cover?: string;
  custom_cover?: boolean;
};

// A reader can be closed and reopened before its last progress write finishes.
const writing = new Map<number, Promise<Comic>>();
function saveComic(id: number, change: Change) {
  const previous = writing.get(id);
  const next = Promise.resolve(previous).catch(() => {}).then(() =>
    call<Comic>("local_comic_update", { id, ...change }),
  );
  writing.set(id, next);
  void next.finally(() => {
    if (writing.get(id) === next) writing.delete(id);
  }).catch(() => {});
  return next;
}

function useComicImages(id: number, enabled: boolean) {
  const pages = useRef(new Map<string, Promise<string | null>>());
  const thumbnails = useRef(new Map<string, Promise<string | null>>());
  function load(pageId: string, thumbnail = false) {
    const cache = (thumbnail ? thumbnails : pages).current;
    const saved = cache.get(pageId);
    if (saved) {
      cache.delete(pageId);
      cache.set(pageId, saved);
      return saved;
    }
    const result = call<string | null>("local_comic_page", {
      id,
      page_id: pageId,
      ...(thumbnail ? { thumbnail: true } : {}),
    });
    cache.set(pageId, result);
    void result.then((src) => {
      if (!src && cache.get(pageId) === result) cache.delete(pageId);
    }, () => {
      if (cache.get(pageId) === result) cache.delete(pageId);
    });
    const oldest = cache.keys().next().value;
    if (oldest !== undefined && cache.size > (thumbnail ? 32 : 6)) cache.delete(oldest);
    return result;
  }
  useEffect(() => {
    if (!enabled) {
      pages.current.clear();
      thumbnails.current.clear();
    }
  }, [enabled]);
  return load;
}

function ComicPageImage({ page, load, thumbnail = false }: {
  page: Page;
  load: (id: string, thumbnail?: boolean) => Promise<string | null>;
  thumbnail?: boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  const loader = useRef(load);
  loader.current = load;
  const [result, setResult] = useState<{ id: string; src: string | null }>();
  useEffect(() => {
    let active = true;
    function read() {
      void loader.current(page.id, thumbnail).then(
        (src) => { if (active) setResult({ id: page.id, src }); },
        () => { if (active) setResult({ id: page.id, src: null }); },
      );
    }
    let observer: IntersectionObserver | undefined;
    if (thumbnail && element.current) {
      observer = new IntersectionObserver(([entry]) => {
        if (entry.isIntersecting) {
          observer?.disconnect();
          read();
        }
      });
      observer.observe(element.current);
    } else read();
    return () => {
      active = false;
      observer?.disconnect();
    };
  }, [page.id, thumbnail]);
  const image = result?.id === page.id ? result.src : undefined;
  return <div ref={element} className={thumbnail ? "comic-order-thumb" : "comic-page"}>
    {image ? <img src={image} alt={thumbnail ? "" : page.name} draggable={false}
      onError={() => setResult({ id: page.id, src: null })} /> :
      thumbnail ? <span aria-hidden="true" /> :
        <span role="status">{image === null ? "此页无法打开" : "正在读取页面…"}</span>}
  </div>;
}

const emptyWheel = (): WheelState => ({ sum: 0, seen: 0, fired: -Infinity });

type ReaderProps = {
  id: number;
  close: () => void;
  onChanged?: () => void;
};

export function ComicReader(props: ReaderProps) {
  return <Reader key={props.id} {...props} />;
}

function Reader({ id, close, onChanged }: ReaderProps) {
  const [comic, setComic] = useState<Comic>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [order, setOrder] = useState<Page[]>([]);
  const [jump, setJump] = useState("");
  const moved = useRef<string | undefined>(undefined);
  const mounted = useRef(false);
  const viewport = useRef<HTMLDivElement>(null);
  const wheel = useRef(emptyWheel());
  const timer = useRef<number | undefined>(undefined);
  const pending = useRef<number | undefined>(undefined);
  const { hidden, reveal } = usePrivateReveal("comic:" + id);
  const loadImage = useComicImages(id, !hidden);

  function flushProgress() {
    window.clearTimeout(timer.current);
    const change = pending.current;
    pending.current = undefined;
    if (change === undefined) return;
    void saveComic(id, { page: change }).catch((exception: Error) => {
      if (mounted.current) setError(exception.message);
    });
  }

  useEffect(() => {
    let active = true;
    mounted.current = true;
    void Promise.resolve(writing.get(id)).catch(() => {}).then(() =>
      call<Comic>("local_comic_get", { id }),
    ).then((value) => { if (active) setComic(value); })
      .catch((exception: Error) => { if (active) setError(exception.message); });
    window.addEventListener("pagehide", flushProgress);
    return () => {
      active = false;
      mounted.current = false;
      window.removeEventListener("pagehide", flushProgress);
      flushProgress();
    };
  }, [id]);

  function select(page: number) {
    if (!comic || !Number.isInteger(page) || page === comic.page || page < 0 || page >= comic.pages.length) return;
    setComic({ ...comic, page });
    setJump("");
    pending.current = page;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flushProgress, 250);
  }

  const positions = comic ? comicPages(comic.page, comic.pages.length, comic.spread) : [];

  function turn(direction: number) {
    if (!comic) return;
    select(turnComicPage(comic.page, comic.pages.length, comic.spread, direction));
  }

  useEffect(() => {
    const node = viewport.current;
    if (!node || hidden || editing) return;
    function scroll(event: WheelEvent) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
      event.preventDefault();
      const step = wheelStep(event.deltaY, event.deltaMode, performance.now(), wheel.current);
      wheel.current = step.state;
      if (step.delta) turn(step.delta);
    }
    function reset() { wheel.current = emptyWheel(); }
    node.addEventListener("wheel", scroll, { passive: false });
    node.addEventListener("pointerleave", reset);
    return () => {
      node.removeEventListener("wheel", scroll);
      node.removeEventListener("pointerleave", reset);
    };
  }, [comic, hidden, editing]);

  useEffect(() => {
    if (!comic || hidden || editing) return;
    function keys(event: KeyboardEvent) {
      if (event.shiftKey || event.altKey || event.ctrlKey || event.metaKey ||
          (event.target instanceof HTMLElement && event.target.closest("input, select, textarea, [contenteditable]"))) return;
      if (event.key === "ArrowRight" || event.key === "PageDown") {
        event.preventDefault();
        turn(event.key === "ArrowRight" && comic?.rtl ? -1 : 1);
      } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
        event.preventDefault();
        turn(event.key === "ArrowLeft" && comic?.rtl ? 1 : -1);
      }
    }
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [comic, hidden, editing]);

  useEffect(() => {
    if (!comic || hidden || editing || !positions.length) return;
    const following = comic.pages[positions[positions.length - 1] + 1];
    const previous = comic.pages[positions[0] - 1];
    const timer = window.setTimeout(() => {
      for (const page of [following, previous]) {
        if (page) void loadImage(page.id).catch(() => {});
      }
    }, 120);
    return () => window.clearTimeout(timer);
  }, [comic, hidden, editing]);

  async function change(change: Change, optimistic = false) {
    flushProgress();
    setError("");
    if (optimistic) setComic((value) => value && {
      ...value,
      spread: change.spread ?? value.spread,
      rtl: change.rtl ?? value.rtl,
    });
    else setBusy(true);
    try {
      const updated = await saveComic(id, change);
      if (mounted.current && !optimistic) {
        setComic(updated);
        setOrder(updated.pages);
        onChanged?.();
      }
    } catch (exception) {
      if (mounted.current) setError((exception as Error).message);
    } finally {
      if (mounted.current && !optimistic) setBusy(false);
    }
  }

  function reorder(from: number, to: number) {
    if (from < 0 || to < 0 || to >= order.length || from === to) return;
    const next = [...order];
    const [page] = next.splice(from, 1);
    next.splice(to, 0, page);
    setOrder(next);
  }

  function closeReader() {
    flushProgress();
    close();
  }

  const dirty = !!comic && editing && order.some((page, index) => page.id !== comic.pages[index]?.id);
  return <Modal open title="漫画阅读器" onClose={closeReader}>
    <div className="comic-reader">
      {error && <div role="alert" className="comic-reader-error">{error}</div>}
      {!comic && !error && <div role="status">正在读取漫画…</div>}
      {comic && hidden && <div className="comic-reader-private">
        <Button onClick={reveal}>显示漫画</Button>
      </div>}
      {comic && !hidden && <>
        <div className="comic-reader-bar">
          <strong className="comic-reader-title">{comic.title}</strong>
          {editing ? <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={busy}>
            {dirty ? "放弃未保存的页序并返回" : "返回阅读"}
          </Button> : <Button size="sm" variant="outline" onClick={() => {
            setOrder(comic.pages);
            setEditing(true);
          }}>编排与封面</Button>}
        </div>
        {editing ? <>
          <div className="comic-reader-bar comic-editor-actions">
            <Button size="sm" disabled={!dirty || busy} onClick={() => {
              const active = comic.pages[comic.page]?.id;
              const nextPage = order.findIndex((page) => page.id === active);
              void change({ order: order.map((page) => page.id), page: Math.max(0, nextPage) });
            }}>保存页序</Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void change({ order: [] })}>恢复默认页序</Button>
            <Button size="sm" variant="outline" disabled={busy || dirty} onClick={() => void change({ custom_cover: true })}>选择外部图片</Button>
            <Button size="sm" variant="outline" disabled={busy || dirty || !comic.cover} onClick={() => void change({ cover: "" })}>恢复首页封面</Button>
            {comic.cover === "__custom__" && <span>已使用外部封面</span>}
          </div>
          <ol className="comic-order" aria-label="漫画页序">
            {order.map((page, index) => <li key={page.id} draggable={!busy}
              onDragStart={(event) => {
                moved.current = page.id;
                event.dataTransfer.setData("text/plain", String(index + 1));
                event.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(event) => { if (moved.current && !busy) event.preventDefault(); }}
              onDrop={(event) => {
                if (!moved.current || busy) return;
                event.preventDefault();
                reorder(order.findIndex((item) => item.id === moved.current), index);
                moved.current = undefined;
              }}
              onDragEnd={() => { moved.current = undefined; }}>
              <ComicPageImage page={page} load={loadImage} thumbnail />
              <span className="comic-order-name">{index + 1}. {page.name}</span>
              <div className="comic-order-actions">
                <Button size="sm" variant="outline" aria-label={`上移第 ${index + 1} 页`} disabled={busy || index === 0} onClick={() => reorder(index, index - 1)}>上移</Button>
                <Button size="sm" variant="outline" aria-label={`下移第 ${index + 1} 页`} disabled={busy || index === order.length - 1} onClick={() => reorder(index, index + 1)}>下移</Button>
                {comic.cover === page.id || (!comic.cover && index === 0 && !dirty) ? <span>当前封面</span> :
                  <Button size="sm" variant="outline" disabled={busy || dirty} onClick={() => void change({ cover: page.id })}>设为封面</Button>}
              </div>
            </li>)}
          </ol>
        </> : <>
          <div className="comic-reader-bar comic-paging">
            <Button size="sm" variant="outline" onClick={() => turn(-1)}
              disabled={turnComicPage(comic.page, comic.pages.length, comic.spread, -1) === comic.page}>上一页</Button>
            <span className="comic-page-count" aria-live="polite">
              {positions.map((page) => page + 1).join("–")} / {comic.pages.length}
            </span>
            <Button size="sm" variant="outline" onClick={() => turn(1)}
              disabled={turnComicPage(comic.page, comic.pages.length, comic.spread, 1) === comic.page}>下一页</Button>
            <form className="comic-jump" onSubmit={(event) => {
              event.preventDefault();
              if (jump) select(Number(jump) - 1);
            }}>
              <input aria-label="跳到页码" type="number" min="1" max={comic.pages.length}
                placeholder="页码" value={jump} onChange={(event) => setJump(event.target.value)} />
              <Button size="sm" variant="outline" type="submit" disabled={!jump}>跳转</Button>
            </form>
            <label><input type="checkbox" checked={comic.spread} onChange={(event) => void change({ spread: event.target.checked }, true)} /> 双页模式</label>
            <label>阅读方向 <select value={comic.rtl ? "rtl" : "ltr"} onChange={(event) => void change({ rtl: event.target.value === "rtl" }, true)}>
              <option value="ltr">从左到右</option>
              <option value="rtl">从右到左</option>
            </select></label>
          </div>
          <div ref={viewport} className={`comic-viewport${positions.length > 1 ? " comic-spread" : ""}${comic.rtl ? " comic-rtl" : ""}`}
            tabIndex={0} aria-label="漫画画面">
            {positions.map((position) => <ComicPageImage key={comic.pages[position].id}
              page={comic.pages[position]} load={loadImage} />)}
            {!positions.length && <span>没有可阅读的页面</span>}
          </div>
        </>}
      </>}
    </div>
  </Modal>;
}
