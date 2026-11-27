import { useEffect, useRef, useState } from "react";
import { FolderOpen } from "lucide-react";
import { call } from "./api";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { bytes } from "./Preferences";
import type { Library } from "./types";

type Migration = {
  id: string;
  source: string;
  destination: string;
  status:
    | "planned"
    | "copying"
    | "rebinding"
    | "cleaning"
    | "paused"
    | "done"
    | "failed"
    | "abandoned";
  file_count: number;
  total_bytes: number;
  copied_files: number;
  copied_bytes: number;
  free_bytes: number;
  error?: string;
  can_abandon?: boolean;
};

const migrationLabel: Record<Migration["status"], string> = {
  planned: "等待确认",
  copying: "正在迁移文件",
  rebinding: "正在更新媒体库位置",
  cleaning: "正在清理已迁移的原文件",
  paused: "迁移已暂停",
  done: "整库迁移已完成",
  failed: "迁移需要处理",
  abandoned: "迁移已放弃",
};

function running(status: Migration["status"] | undefined) {
  return (
    status === "copying" || status === "rebinding" || status === "cleaning"
  );
}

export function LibraryMigration({
  library,
  changed,
}: {
  library: Library | undefined;
  changed: () => void;
}) {
  const id = library?.id;
  const [status, setStatus] = useState<Migration | null>(null);
  const [plan, setPlan] = useState<Migration | null>(null);
  const [busy, setBusy] = useState(false);
  const [abandon, setAbandon] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(changed);
  latest.current = changed;

  useEffect(() => {
    setStatus(null);
    setPlan(null);
    setAbandon(false);
    setError("");
    if (!id) return;
    let alive = true;
    let lastStatus: Migration["status"] | undefined;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await call<Migration | null>(
          "library_migration_status",
          { id },
        );
        if (!alive) return;
        setStatus(result);
        if (result?.status === "done" && lastStatus && lastStatus !== "done")
          latest.current();
        lastStatus = result?.status;
      } catch (reason) {
        if (alive)
          setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (alive) timer = setTimeout(poll, 750);
      }
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id]);

  async function run(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const active = running(status?.status);
  const resumable = status?.status === "failed" || status?.status === "paused";

  return (
    <div className="settings-card">
      <h2>整库迁移</h2>
      {library && <p className="library-path">{library.path}</p>}
      {status && (
        <div className="organize-progress" aria-live="polite">
          <span>
            {migrationLabel[status.status]} · {status.copied_files} /{" "}
            {status.file_count} 个文件
          </span>
          {(active || resumable) && (
            <progress
              aria-label="整库迁移进度"
              max={Math.max(1, status.file_count)}
              value={status.copied_files}
            />
          )}
        </div>
      )}
      {(error || status?.error) && <p role="alert">{error || status?.error}</p>}
      <div className="row-actions">
        <Button
          disabled={!id || !library?.online || busy || active || resumable}
          onClick={() =>
            void run(async () => {
              const path = await call<string | null>("choose_folder");
              if (path && id)
                setPlan(
                  await call<Migration>("library_migration_plan", { id, path }),
                );
            })
          }
        >
          <FolderOpen size={16} />
          选择迁移位置
        </Button>
        {active && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await call("pause");
              })
            }
          >
            暂停迁移
          </Button>
        )}
        {resumable && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await call("library_migration_retry", { id });
              })
            }
          >
            继续迁移
          </Button>
        )}
        {status?.can_abandon && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setAbandon(true)}
          >
            放弃迁移
          </Button>
        )}
      </div>
      <Modal
        open={abandon}
        onClose={() => setAbandon(false)}
        title="确认放弃迁移"
      >
        <p>媒体库将继续使用原路径。原文件和已经复制到目标的文件都会保留。</p>
        {error && <p role="alert">{error}</p>}
        <div className="modal-footer">
          <Button variant="outline" onClick={() => setAbandon(false)}>
            返回
          </Button>
          <Button
            disabled={busy || !id || !status?.can_abandon}
            onClick={() =>
              void run(async () => {
                await call("library_migration_abandon", { id });
                setAbandon(false);
              })
            }
          >
            确认放弃
          </Button>
        </div>
      </Modal>
      <Modal open={!!plan} onClose={() => setPlan(null)} title="确认整库迁移">
        {plan && (
          <>
            <dl className="capacity-list">
              <div>
                <dt>原路径</dt>
                <dd>{plan.source}</dd>
              </div>
              <div>
                <dt>新路径</dt>
                <dd>{plan.destination}</dd>
              </div>
              <div>
                <dt>将迁移</dt>
                <dd>
                  {plan.file_count} 个文件 · {bytes(plan.total_bytes)}
                </dd>
              </div>
              <div>
                <dt>目标可用空间</dt>
                <dd>{bytes(plan.free_bytes)}</dd>
              </div>
            </dl>
            <p>
              文件按当前目录层级迁移。校验通过后更新媒体库路径并清理原文件，库外引用保留在原位。
            </p>
            {error && <p role="alert">{error}</p>}
            <div className="modal-footer">
              <Button variant="outline" onClick={() => setPlan(null)}>
                返回
              </Button>
              <Button
                disabled={busy || !id}
                onClick={() =>
                  void run(async () => {
                    await call("library_migration_start", {
                      id,
                      path: plan.destination,
                    });
                    setPlan(null);
                  })
                }
              >
                开始迁移
              </Button>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}

export function AutoFlattenSettings({
  libraries,
  busy,
  applying,
  progress,
  apply,
}: {
  libraries: Library[];
  busy: boolean;
  applying: boolean;
  progress: { done: number; total: number; text: string } | null;
  apply: (id: string, enabled: boolean) => Promise<boolean>;
}) {
  const [confirm, setConfirm] = useState<{
    id: string;
    enabled: boolean;
  } | null>(null);
  const library = libraries.find(({ id }) => id === confirm?.id);
  return (
    <section className="settings-card">
      <h2>自动脱壳</h2>
      {libraries.map((item) => (
        <div className="row-actions" key={item.id}>
          <label className="privacy-setting">
            <input
              type="checkbox"
              checked={item.auto_flatten === true}
              onChange={(event) =>
                setConfirm({
                  id: item.id,
                  enabled: event.currentTarget.checked,
                })
              }
              disabled={applying || !item.online || !!item.auto_flatten_pending}
            />
            {item.name}
          </label>
          {item.auto_flatten_pending && (
            <>
              <span role="status">
                {busy
                  ? item.auto_flatten
                    ? "正在切换到集中布局"
                    : "正在回退原目录"
                  : "待继续整理"}
              </span>
              {!busy && (
                <Button
                  variant="outline"
                  disabled={applying || !item.online}
                  onClick={() => void apply(item.id, !!item.auto_flatten)}
                >
                  继续整理
                </Button>
              )}
            </>
          )}
          {item.auto_flatten_error && (
            <span role="alert">{item.auto_flatten_error}</span>
          )}
        </div>
      ))}
      {progress && libraries.some((item) => item.auto_flatten_pending) && (
        <div className="organize-progress" aria-live="polite">
          <span>
            {progress.text} {progress.done} / {progress.total}
          </span>
          <progress
            aria-label="脱壳进度"
            max={Math.max(1, progress.total)}
            value={progress.done}
          />
        </div>
      )}
      <Modal
        open={!!library}
        onClose={() => setConfirm(null)}
        title={confirm?.enabled ? "确认开启自动脱壳" : "确认关闭自动脱壳"}
      >
        <p>
          {confirm?.enabled
            ? `开启后会立即把「${library?.name}」内已下载的媒体整理到集中目录。之后下载的媒体也会集中保存，JSON 留在原目录。`
            : `关闭后会把「${library?.name}」里已脱壳的媒体移回原来的目录，之后按原目录结构保存。`}
        </p>
        <div className="modal-footer">
          <Button variant="outline" onClick={() => setConfirm(null)}>
            返回
          </Button>
          <Button
            disabled={!confirm || applying}
            onClick={() => {
              if (confirm)
                void apply(confirm.id, confirm.enabled).then((ok) => {
                  if (ok) setConfirm(null);
                });
            }}
          >
            {confirm?.enabled ? "开启并脱壳" : "关闭并回退"}
          </Button>
        </div>
      </Modal>
    </section>
  );
}
