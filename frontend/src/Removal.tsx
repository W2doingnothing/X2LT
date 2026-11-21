import { useEffect, useRef, useState } from "react";
import { Trash2, MoreHorizontal } from "lucide-react";
import { call } from "./api";
import { PrivateText } from "./Privacy";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { bytes } from "./Preferences";
import type { Payload } from "./types";

type Plan = {
  content_count: number;
  media_count: number;
  bytes: number;
  unknown: number;
};
export type ContextTarget = { ids: string[]; x: number; y: number };
export function ContentMenu({ onRemove }: { onRemove: () => void }) {
  return (
    <details className="content-menu">
      <summary aria-label="内容操作" title="内容操作">
        <MoreHorizontal size={16} />
      </summary>
      <div role="menu">
        <button
          role="menuitem"
          onClick={(e) => {
            e.currentTarget.closest("details")?.removeAttribute("open");
            onRemove();
          }}
        >
          <Trash2 size={14} />
          移除
        </button>
      </div>
    </details>
  );
}
export function ContentContextMenu({
  target,
  close,
  remove,
}: {
  target: ContextTarget | null;
  close: () => void;
  remove: (ids: string[]) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!target) return;
    element.current?.querySelector("button")?.focus();
    const hide = (e: globalThis.MouseEvent) => {
      if (!element.current?.contains(e.target as Node)) close();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    document.addEventListener("pointerdown", hide);
    document.addEventListener("keydown", key, true);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", hide);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("scroll", close, true);
    };
  }, [target]);
  return target ? (
    <div
      className="content-context-menu"
      role="menu"
      ref={element}
      style={{
        left: Math.min(target.x, window.innerWidth - 160),
        top: Math.min(target.y, window.innerHeight - 65),
      }}
    >
      <button
        role="menuitem"
        onClick={() => {
          remove(target.ids);
          close();
        }}
      >
        <Trash2 size={15} />
        移除
      </button>
    </div>
  ) : null;
}
export function RemovalDialog({
  ids,
  libraryId,
  libraryName,
  confirm,
  close,
  changed,
  save,
}: {
  ids: string[] | null;
  libraryId: string;
  libraryName: string;
  confirm: boolean;
  close: () => void;
  changed: () => void;
  save: (p: Payload) => Promise<boolean>;
}) {
  const [plan, setPlan] = useState<Plan | null>(null),
    [error, setError] = useState(""),
    [working, setWorking] = useState(false),
    [skip, setSkip] = useState(false);
  const revision = useRef(0);
  const run = async (selected: string[], stopAsking = false) => {
    setWorking(true);
    setError("");
    try {
      await call("removal_start", { ids: selected, library_id: libraryId });
      if (stopAsking) await save({ confirm_removal: false });
      changed();
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  };
  useEffect(() => {
    const n = ++revision.current;
    setPlan(null);
    setError("");
    setSkip(false);
    if (!ids) return;
    void call<Plan>("removal_plan", { ids, library_id: libraryId })
      .then((p) => {
        if (n !== revision.current) return;
        setPlan(p);
        if (!confirm) void run(ids);
      })
      .catch((e) => {
        if (n === revision.current) setError((e as Error).message);
      });
    return () => {
      revision.current++;
    };
  }, [ids, libraryId]);
  return (
    <Modal
      open={!!ids}
      onClose={() => {
        if (!working) close();
      }}
      title="移除内容"
    >
      {plan ? (
        <>
          <p>
            移除 {plan.content_count} 条内容、{plan.media_count} 个媒体（
            {bytes(plan.bytes)}
            {plan.unknown ? "，部分体积未知" : ""}）。
          </p>
          <p>
            删除“{libraryName}
            ”内的对应文件，并过滤后续扫描。其他库已有副本保留。
          </p>
          <p>完整文件移入回收站；取消过滤不会立即重新下载。</p>
          <label className="removal-checkbox">
            <input
              type="checkbox"
              checked={skip}
              onChange={(e) => setSkip(e.target.checked)}
              disabled={working}
            />
            以后不再询问
          </label>
        </>
      ) : (
        !error && <p>正在检查移除范围…</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-footer">
        <Button variant="outline" disabled={working} onClick={close}>
          取消
        </Button>
        <Button
          disabled={!plan || working}
          onClick={() => ids && run(ids, skip)}
        >
          <Trash2 size={14} />
          {working ? "正在处理…" : "确认移除"}
        </Button>
      </div>
    </Modal>
  );
}

type FilterItem = {
  key: string;
  platform: string;
  message_id: string;
  author: string;
  text: string;
  created: number;
  reason: string;
  notes?: string[];
  operations: { operation: string; status: string; error: string }[];
};
type FilterPage = { items: FilterItem[]; total: number; offset: number };
export function FiltersPanel({
  confirm,
  save,
}: {
  confirm: boolean;
  save: (p: Payload) => Promise<boolean>;
}) {
  const [page, setPage] = useState<FilterPage>({
      items: [],
      total: 0,
      offset: 0,
    }),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<string[]>([]),
    [error, setError] = useState(""),
    [working, setWorking] = useState(false),
    [revision, setRevision] = useState(0);
  const request = useRef(0),
    scroll = useRef(false);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const n = ++request.current;
      try {
        const p = await call<FilterPage>("filters", { query, offset });
        if (!alive || n !== request.current) return;
        setPage(p);
        setError("");
        if (p.offset !== offset) {
          scroll.current = true;
          setOffset(p.offset);
        }
        if (scroll.current) {
          scroll.current = false;
          requestAnimationFrame(() => window.scrollTo(0, 0));
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    void load();
    const timer = setInterval(() => void load(), 1500);
    return () => {
      alive = false;
      clearInterval(timer);
      request.current++;
    };
  }, [query, offset, revision]);
  const change = async (action: string, p: Payload) => {
    setWorking(true);
    setError("");
    try {
      await call(action, p);
      setSelected([]);
      setRevision((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const paginate = (n: number) => {
    scroll.current = true;
    setSelected([]);
    setOffset(n);
  };
  return (
    <section className="settings-card filter-panel">
      <h2>媒体管理</h2>
      <label className="removal-checkbox">
        <input
          type="checkbox"
          checked={confirm}
          onChange={(e) => void save({ confirm_removal: e.target.checked })}
        />
        移除前确认（单项与批量）
      </label>
      <hr />
      <h3>
        过滤媒体名单 <small>{page.total}</small>
      </h3>
      <div className="filter-toolbar">
        <input
          aria-label="搜索过滤名单"
          placeholder="搜索作者、内容或编号"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOffset(0);
            setSelected([]);
          }}
        />
        <Button
          variant="outline"
          disabled={!selected.length || working}
          onClick={() => void change("filters_clear", { keys: selected })}
        >
          取消所选过滤
        </Button>
        <Button
          variant="outline"
          disabled={!page.total || working}
          onClick={() => void change("filters_clear", { all: true })}
        >
          清空过滤名单
        </Button>
      </div>
      <p className="muted-text">
        取消过滤仅允许下次扫描重新获取，不立即下载或恢复文件。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!!page.items.length && (
        <label className="removal-checkbox">
          <input
            type="checkbox"
            aria-label="全选过滤名单当前页"
            checked={page.items.every((i) => selected.includes(i.key))}
            onChange={(e) =>
              setSelected(e.target.checked ? page.items.map((i) => i.key) : [])
            }
          />
          全选当前页
        </label>
      )}
      <div className="filter-items">
        {page.items.map((i) => (
          <article className="filter-item" key={i.key}>
            <input
              type="checkbox"
              aria-label={"选择过滤内容 " + i.message_id}
              checked={selected.includes(i.key)}
              onChange={(e) =>
                setSelected((prev) =>
                  e.target.checked
                    ? [...prev, i.key]
                    : prev.filter((k) => k !== i.key),
                )
              }
            />
            <div className="filter-copy">
              <strong>{i.author || "未知作者"}</strong>
              <small>
                {i.platform.toUpperCase()} · {i.message_id} ·{" "}
                {new Date(i.created * 1000).toLocaleDateString()} ·{" "}
                {i.reason === "removed" ? "已移除" : "已忽略"}
              </small>
              <PrivateText compact text={i.text || "内容 " + i.message_id} />
              {i.notes?.map((note) => (
                <p className="muted-text" key={note}>
                  {note}
                </p>
              ))}
              {i.operations.map((o, n) => (
                <div className="filter-failure" key={o.operation + n}>
                  <span>{o.error || "正在移除文件…"}</span>
                  {o.status === "error" && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={working}
                      onClick={() =>
                        void change("removal_retry", { id: o.operation })
                      }
                    >
                      重试移除
                    </Button>
                  )}
                </div>
              ))}
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={working || !!i.operations.length}
              onClick={() => void change("filters_clear", { keys: [i.key] })}
            >
              取消过滤
            </Button>
          </article>
        ))}
      </div>
      {!page.total && <p>暂无过滤内容</p>}
      {page.total > 50 && (
        <div className="pagination">
          <Button
            variant="outline"
            disabled={!offset}
            onClick={() => paginate(Math.max(0, offset - 50))}
          >
            上一页
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + 50, page.total)} / {page.total}
          </span>
          <Button
            variant="outline"
            disabled={offset + 50 >= page.total}
            onClick={() => paginate(offset + 50)}
          >
            下一页
          </Button>
        </div>
      )}
    </section>
  );
}
