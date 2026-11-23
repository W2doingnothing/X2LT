import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import "./continuous-pages.css";

type Page<T> = { rows: T[]; total: number; offset: number };
type Request = { offset: number; through: number };
type Options<T> = {
  enabled: boolean;
  active?: boolean;
  scope: string;
  revision?: string | number;
  pageSize: number;
  load: (offset: number) => Promise<Page<T>>;
  key: (row: T) => string;
};
type View<T> = {
  scope: string;
  revision?: string | number;
  rows: T[];
  total: number;
  next: number;
  ready: boolean;
  loading: boolean;
  done: boolean;
  error: string;
  retry?: Request;
};

function emptyView<T>(scope: string, revision?: string | number): View<T> {
  return { scope, revision, rows: [], total: 0, next: 0, ready: false, loading: false, done: false, error: "" };
}

/** Offset accounting follows the server page, so repeated IDs never stall the list. */
export function useContinuousPages<T>(options: Options<T>) {
  const { enabled, active = true, scope, revision, pageSize } = options;
  const [view, setView] = useState<View<T> | null>(null);
  const current = useRef<View<T> | null>(null);
  const generation = useRef(0);
  const config = useRef(options);
  config.current = options;

  const update = useCallback((value: View<T> | null) => {
    current.current = value;
    setView(value);
  }, []);

  const request = useCallback(async (original: View<T>, plan: Request) => {
    const version = generation.current;
    const { load, key, pageSize, revision } = config.current;
    const { offset, through } = plan;
    update({ ...original, loading: true, error: "", retry: undefined });
    const rows = new Map((offset ? original.rows : []).map((row) => [key(row), row]));
    let next = offset;
    let total = original.total;
    let done = false;
    try {
      do {
        const page = await load(next);
        if (version !== generation.current) return;
        total = page.total;
        for (const row of page.rows) rows.set(key(row), row);
        done = page.offset !== next || !page.rows.length || next + pageSize >= total;
        next += pageSize;
      } while (!done && next < through);
      update({ ...original, revision, rows: Array.from(rows.values()), total, next,
        loading: false, ready: true, done, error: "", retry: undefined });
    } catch (reason) {
      if (version !== generation.current) return;
      const error = reason instanceof Error ? reason.message : String(reason);
      update({ ...original, loading: false, error, retry: plan });
    }
  }, [update]);

  useEffect(() => {
    if (!enabled) {
      if (current.current) update(null);
      return;
    }
    if (!active) return;
    const previous = current.current;
    const original = previous?.scope === scope ? previous : emptyView<T>(scope, revision);
    if (original.revision !== revision || !original.ready || original.loading) {
      void request(original, { offset: 0, through: Math.max(pageSize, original.next) });
    }
    return () => { generation.current++; };
  }, [active, enabled, scope, revision, pageSize, request, update]);

  const loadNext = useCallback(() => {
    const latest = current.current;
    const configValue = config.current;
    if (!configValue.enabled || configValue.active === false || latest?.scope !== configValue.scope ||
      !latest.ready || latest.loading || latest.done || latest.error) return;
    void request(latest, { offset: latest.next, through: latest.next + configValue.pageSize });
  }, [request]);
  const retry = useCallback(() => {
    const latest = current.current;
    const configValue = config.current;
    if (!configValue.enabled || configValue.active === false || latest?.scope !== configValue.scope ||
      latest.loading || !latest.retry) return;
    void request(latest, latest.retry);
  }, [request]);

  const value = enabled && view?.scope === scope ? view : emptyView<T>(scope, revision);
  return { ...value, loadNext, retry };
}

export function ContinuousFooter({
  loaded, total, loading, done, error, onLoad, onRetry,
}: {
  loaded: number;
  total: number;
  loading: boolean;
  done: boolean;
  error: string;
  onLoad: () => void;
  onRetry: () => void;
}) {
  const footer = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!footer.current || !window.IntersectionObserver || loading || done || error) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onLoad();
    }, { rootMargin: "380px 0px" });
    observer.observe(footer.current);
    return () => observer.disconnect();
  }, [loading, done, error, onLoad, loaded]);
  return (
    <div className="pagination continuous-footer" ref={footer} aria-live="polite">
      {error ? (
        <>
          <span role="alert">{error}</span>
          <Button size="sm" variant="outline" onClick={onRetry}>重试加载</Button>
        </>
      ) : loading ? (
        <span>正在加载媒体…</span>
      ) : done ? (
        <span>已加载全部 · {loaded} 项</span>
      ) : (
        <>
          <span>已加载 {loaded} / {total} 项</span>
          <Button size="sm" variant="outline" onClick={onLoad}>加载更多</Button>
        </>
      )}
    </div>
  );
}
