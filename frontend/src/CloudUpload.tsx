import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, CloudUpload as UploadIcon } from "lucide-react";
import { call } from "./api";
import { bytes } from "./Preferences";
import { PrivateText } from "./Privacy";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import "./cloud-upload.css";

type Connection = {
  available: boolean;
  logged_in: boolean | null;
  state?: string | null;
  code?: string;
  user_agreement_url?: string;
  privacy_policy_url?: string;
};
type Selection = { id: string; names: string[]; bytes: number };
type Job = {
  id: string;
  kind: string;
  state: string;
  name: string;
  message: string;
  cancellable?: boolean;
  retryable?: boolean;
  progress?: number;
};
type Queue = { items: Job[]; counts: { cancellable: number; retryable: number } };
type Target = { id: string } | { all: true };
const emptyQueue: Queue = { items: [], counts: { cancellable: 0, retryable: 0 } };
const jobLabel: Record<string, string> = {
  queued: "排队中",
  running: "正在上传",
  completed: "已完成",
  failed: "失败",
  paused: "等待恢复",
  cancelling: "正在取消",
  cancelled: "已取消",
  cancel_unknown: "取消结果待确认",
};

function PolicyLink({ href, label }: { href?: string; label: string }) {
  try {
    if (href) {
      const url = new URL(href);
      if (url.protocol === "https:" && url.hostname === "app.guangyapan.com") {
        return <a href={url.href} target="_blank" rel="noopener noreferrer">{label}</a>;
      }
    }
  } catch {
    // An invalid policy address is displayed as plain text.
  }
  return <>{label}</>;
}

export function CloudUpload() {
  const [open, setOpen] = useState(false);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [queue, setQueue] = useState<Queue>(emptyQueue);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [queueError, setQueueError] = useState("");
  const [notice, setNotice] = useState("");
  const [cancelTarget, setCancelTarget] = useState<Target | null>(null);
  const connectionRequest = useRef(0);
  const queueRequest = useRef(0);
  const waiting = connection?.code === "LOGIN_PENDING"
    || ["waiting", "login_pending", "pending"].includes(connection?.state?.toLowerCase() ?? "");

  const refreshConnection = useCallback(async (login = false) => {
    const request = ++connectionRequest.current;
    try {
      const result = await call<Connection>(login ? "cloud_login" : "cloud_status");
      if (request !== connectionRequest.current) return;
      setConnection((previous) => ({
        ...result,
        user_agreement_url: result.user_agreement_url || previous?.user_agreement_url,
        privacy_policy_url: result.privacy_policy_url || previous?.privacy_policy_url,
      }));
      setConnectionError("");
    } catch (reason) {
      if (request === connectionRequest.current) setConnectionError(String(reason));
    }
  }, []);

  const refreshQueue = useCallback(async () => {
    const request = ++queueRequest.current;
    try {
      const result = await call<Queue>("cloud_jobs");
      if (request !== queueRequest.current) return;
      setQueue({ ...result, items: result.items.filter((job) => job.kind === "upload") });
      setQueueError("");
    } catch (reason) {
      if (request === queueRequest.current) setQueueError(String(reason));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void refreshConnection();
    return () => { connectionRequest.current++; };
  }, [open, refreshConnection]);

  useEffect(() => {
    if (!open || !waiting) return;
    let active = true;
    let timer = window.setTimeout(poll, 2400);
    async function poll() {
      await refreshConnection();
      if (active) timer = window.setTimeout(poll, 2400);
    }
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [open, waiting, refreshConnection]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    let timer: number;
    async function poll() {
      await refreshQueue();
      if (active) timer = window.setTimeout(poll, 2200);
    }
    void poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
      queueRequest.current++;
    };
  }, [open, refreshQueue]);

  async function perform(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  function pickFiles() {
    void perform(async () => {
      const files = await call<Selection | null>("cloud_pick_upload");
      if (files) setSelection(files);
    });
  }

  function uploadFiles(id: string) {
    void perform(async () => {
      const result = await call<{ count: number }>("cloud_start_upload", { id });
      setSelection(null);
      setNotice(result.count ? `已加入队列，共 ${result.count} 个文件` : "没有需要加入队列的文件");
      await refreshQueue();
    });
  }

  function controlJobs(action: "cancel" | "retry", target: Target) {
    void perform(async () => {
      await call(`cloud_${action}`, target);
      setCancelTarget(null);
      setNotice(action === "cancel" ? "已请求取消" : "已请求重试");
      await refreshQueue();
    });
  }

  let status = "正在检查连接…";
  if (connection?.logged_in) status = "已连接";
  else if (waiting) status = "等待登录";
  else if (connection) status = connection.available ? "未连接" : "光鸭连接组件不可用";
  else if (connectionError) status = "连接状态不可用";

  return (
    <details className="cloud-upload" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="tool-entry">
        <UploadIcon size={30} aria-hidden="true" />
        <strong>光鸭上传</strong>
        <ChevronDown size={18} aria-hidden="true" />
      </summary>
      <section className="cloud-upload-body" aria-label="上传文件">
        <div className="cloud-upload-actions">
          <span>{status}</span>
          {connection?.available && !connection.logged_in && !waiting && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void perform(() => refreshConnection(true))}>
              连接光鸭
            </Button>
          )}
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void perform(() => refreshConnection())}>
            检查连接
          </Button>
        </div>
        {waiting && (
          <p>请在浏览器完成登录。登录即授权 X2LT 访问光鸭，并表示同意<PolicyLink href={connection?.user_agreement_url} label="用户协议" />及<PolicyLink href={connection?.privacy_policy_url} label="隐私政策" />。</p>
        )}
        {connection && !connection.available && !waiting && (
          <p>连接需要 Windows、Node.js 20 或以上及已安装的官方光鸭组件。</p>
        )}
        {connectionError && <p role="alert">{connectionError}</p>}
        {connection?.logged_in && <>
          <p>文件上传到「X2LT上传」，云端使用随机文件名。</p>
          <div className="cloud-upload-actions">
            <Button variant="outline" disabled={busy} onClick={pickFiles}>选择本地文件</Button>
            {selection && (
              <Button disabled={busy} onClick={() => uploadFiles(selection.id)}>
                上传 {selection.names.length} 个文件
              </Button>
            )}
          </div>
          {selection && <div className="cloud-upload-selection">
            <span>共 {bytes(selection.bytes)}</span>
            <ul>{selection.names.map((name, index) => <li key={index}><PrivateText text={name} compact /></li>)}</ul>
          </div>}
        </>}
        <h2>上传任务</h2>
        {(queue.counts.cancellable > 0 || queue.counts.retryable > 0) && (
          <div className="cloud-upload-actions" aria-label="批量任务控制">
            {queue.counts.cancellable > 0 && (
              <Button size="sm" variant="outline" disabled={busy}
                onClick={() => { setError(""); setCancelTarget({ all: true }); }}>取消待处理</Button>
            )}
            {queue.counts.retryable > 0 && (
              <Button size="sm" variant="outline" disabled={busy}
                onClick={() => controlJobs("retry", { all: true })}>批量重试</Button>
            )}
          </div>
        )}
        {notice && <p role="status">{notice}</p>}
        {queueError && <p role="alert">{queueError}</p>}
        {error && !cancelTarget && <p role="alert">{error}</p>}
        {queue.items.length ? <ul className="cloud-upload-jobs">
          {queue.items.map((job) => <li key={job.id}>
            <div><strong>{jobLabel[job.state] ?? job.state}</strong></div>
            {job.name && <PrivateText text={job.name} compact />}
            {job.message && <PrivateText text={job.message} compact />}
            {["queued", "running", "cancelling"].includes(job.state) && (
              <progress aria-label="上传进度" max={100} value={job.progress} />
            )}
            {(job.cancellable || job.retryable) && <div className="cloud-upload-actions">
              {job.cancellable && <Button size="sm" variant="outline" disabled={busy}
                onClick={() => { setError(""); setCancelTarget({ id: job.id }); }}>取消</Button>}
              {job.retryable && <Button size="sm" variant="outline" disabled={busy}
                onClick={() => controlJobs("retry", { id: job.id })}>{job.state === "paused" ? "继续" : "重试"}</Button>}
            </div>}
          </li>)}
        </ul> : <p>暂无上传任务。</p>}
      </section>
      {cancelTarget && <Modal open title={"all" in cancelTarget ? "取消待处理上传" : "取消上传"}
        onClose={() => { if (!busy) setCancelTarget(null); }}>
        <p>已经上传的文件将保留。正在执行的任务以最终状态为准。</p>
        {error && <p role="alert">{error}</p>}
        <div className="cloud-upload-actions">
          <Button variant="outline" disabled={busy} onClick={() => setCancelTarget(null)}>返回</Button>
          <Button disabled={busy} onClick={() => controlJobs("cancel", cancelTarget)}>确认取消</Button>
        </div>
      </Modal>}
    </details>
  );
}
