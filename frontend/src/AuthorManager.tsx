import { useEffect, useState } from "react";
import type { Author, Snapshot } from "./types";
import { call } from "./api";
import { Modal } from "./components/ui/dialog";
import { Button } from "./components/ui/button";

export function AuthorManager({
  libraryId,
  revision,
  onClose,
  onChange,
}: {
  libraryId: string;
  revision: number;
  onClose: () => void;
  onChange: (
    keys: string[],
    changes: { in_other: boolean },
  ) => Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Author[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    void call<Snapshot>("snapshot", {
      library_id: libraryId,
      collection: true,
      view: "authors",
      author_group: "all",
      query,
      offset,
      limit: 50,
    })
      .then((result) => {
        if (!alive) return;
        setRows(result.authors || []);
        setTotal(result.total);
        setOffset(result.offset);
        setLoading(false);
      })
      .catch((e) => {
        if (alive) {
          setError(String(e.message));
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [libraryId, query, offset, revision]);
  async function move(in_other: boolean) {
    setBusy(true);
    try {
      if (await onChange(selected, { in_other })) setSelected([]);
    } finally {
      setBusy(false);
    }
  }
  const all = !!rows.length && rows.every((a) => selected.includes(a.key));
  return (
    <Modal open title="管理分组" onClose={onClose}>
      <div className="author-manager">
        <input
          aria-label="搜索管理博主"
          placeholder="搜索名称、账号"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOffset(0);
          }}
        />
        <label>
          <input
            type="checkbox"
            aria-label="全选当前页博主"
            checked={all}
            disabled={loading || busy}
            onChange={() =>
              setSelected((old) =>
                all
                  ? old.filter((k) => !rows.some((a) => a.key === k))
                  : [...new Set([...old, ...rows.map((a) => a.key)])].slice(
                      0,
                      100,
                    ),
              )
            }
          />
          全选当前页
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="author-manager-list" aria-busy={loading}>
          {!loading && !rows.length && <p>暂无匹配博主</p>}
          {rows.map((a) => (
            <label key={a.key} className="author-manager-row">
              <input
                type="checkbox"
                aria-label={`选择博主 ${a.display_name || a.handle}`}
                checked={selected.includes(a.key)}
                disabled={
                  loading ||
                  busy ||
                  (selected.length >= 100 && !selected.includes(a.key))
                }
                onChange={() =>
                  setSelected((old) =>
                    old.includes(a.key)
                      ? old.filter((k) => k !== a.key)
                      : [...old, a.key],
                  )
                }
              />
              <span>
                <strong>{a.display_name || a.handle}</strong>
                <small>@{a.handle}</small>
              </span>
              <span>
                {a.in_other ? "其他博主" : "主列表"}
                {a.starred ? " · ★" : ""}
              </span>
            </label>
          ))}
        </div>
        <div className="pagination">
          <Button
            size="sm"
            variant="outline"
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            上一页
          </Button>
          <span>
            {total ? offset + 1 : 0}–{Math.min(offset + 50, total)} / {total}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={offset + 50 >= total || loading}
            onClick={() => setOffset(offset + 50)}
          >
            下一页
          </Button>
        </div>
        <footer>
          <span>已选 {selected.length} / 100 位</span>
          <Button
            disabled={
              !selected.length || selected.length > 100 || busy || loading
            }
            onClick={() => void move(true)}
          >
            归入其他博主
          </Button>
          <Button
            variant="outline"
            disabled={
              !selected.length || selected.length > 100 || busy || loading
            }
            onClick={() => void move(false)}
          >
            移出其他博主
          </Button>
        </footer>
      </div>
    </Modal>
  );
}
