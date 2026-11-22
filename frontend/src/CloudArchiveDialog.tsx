import { useState } from "react";
import { call } from "./api";
import { CloudFolderBrowser, CloudLoginConsent, cloudLoginPending, type CloudStatus } from "./CloudLibrary";
import { Button } from "./components/ui/button";
import { Modal } from "./components/ui/dialog";
import { bytes } from "./Preferences";
import { PrivateText, usePrivateReveal } from "./Privacy";

type ArchiveMode = "tag" | "source";
type ArchivePlan = {
  id: string;
  media_count: number;
  queued_count: number;
  known_count: number;
  bytes: number;
  unavailable_count: number;
  folders: { path: string[]; count: number; known: number }[];
  aliases: { key: string; label: string; path: string[] }[];
  tag_choices: { id: string; title: string; tags: string[]; selected: string }[];
};

export type CloudArchiveTarget = {
  libraryId: string;
  libraryName: string;
  selected: string[];
  useSelection?: boolean;
};

export function CloudArchiveDialog({ target, status, refreshStatus, close, started }: {
  target: CloudArchiveTarget;
  status: CloudStatus;
  refreshStatus: (login?: boolean) => Promise<void>;
  close: () => void;
  started: (count: number) => void;
}) {
  const [folder, setFolder] = useState<{ id: string; name: string }>();
  const [choosingFolder, setChoosingFolder] = useState(true);
  const [selectionOnly, setSelectionOnly] = useState(!!target.useSelection);
  const [mode, setMode] = useState<ArchiveMode>("source");
  const [mergeSmall, setMergeSmall] = useState(false);
  const [threshold, setThreshold] = useState("5");
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const [tags, setTags] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<{ fingerprint: string; plan: ArchivePlan }>();
  const [working, setWorking] = useState<"connect" | "preview" | "upload">();
  const [error, setError] = useState("");
  const { hidden, reveal } = usePrivateReveal("cloud-archive-layout");
  const merge = mode === "source" && mergeSmall;
  const limit = Number(threshold);
  const validThreshold = !merge || (/^[0-9]+$/.test(threshold) && limit >= 1 && limit <= 100000);
  const payload = {
    library_id: target.libraryId,
    parent: folder?.id ?? "",
    mode,
    ...(selectionOnly ? { ids: target.selected } : {}),
    ...(merge && validThreshold ? { threshold: limit } : {}),
    aliases,
    ...(mode === "tag" ? { tag_overrides: tags } : {}),
  };
  const fingerprint = JSON.stringify(payload);
  const current = preview?.fingerprint === fingerprint;
  const plan = preview?.plan;
  const canPreview = !!folder && !choosingFolder && validThreshold && !working;

  async function previewArchive() {
    if (!canPreview) return;
    setWorking("preview");
    setError("");
    try {
      setPreview({ fingerprint, plan: await call<ArchivePlan>("cloud_archive_plan", payload) });
    } catch (err) {
      setPreview(undefined);
      setError((err as Error).message);
    } finally {
      setWorking(undefined);
    }
  }

  async function upload() {
    if (!plan || !current || working || choosingFolder) return;
    setWorking("upload");
    setError("");
    try {
      const result = await call<{ queued: number }>("cloud_archive_start", { id: plan.id });
      started(result.queued);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(undefined);
    }
  }

  return <Modal open title="按层级上传到光鸭" onClose={() => { if (working !== "upload") close(); }}>
    {!status.logged_in ? <>
      <Button variant="outline" disabled={!!working} onClick={async () => {
        setWorking("connect");
        try { await refreshStatus(!cloudLoginPending(status)); } finally { setWorking(undefined); }
      }}>{cloudLoginPending(status) ? "检查连接" : "连接光鸭"}</Button>
      <CloudLoginConsent status={status} />
    </> : <div className="cloud-archive-configuration">
      <div className="cloud-actions"><span>来源库</span><PrivateText text={target.libraryName} compact /></div>
      <label>上传范围 <select value={selectionOnly ? "selection" : "library"} disabled={!!working}
        onChange={(event) => setSelectionOnly(event.target.value === "selection")}>
        <option value="library">整个媒体库</option>
        {!!target.selected.length && <option value="selection">已选 {target.selected.length} 条内容</option>}
      </select></label>
      <label>整理方式 <select value={mode} disabled={!!working}
        onChange={(event) => setMode(event.target.value === "tag" ? "tag" : "source")}>
        <option value="source">按来源和作者／频道</option>
        <option value="tag">按标签</option>
      </select></label>
      {mode === "source" && <div className="cloud-actions">
        <label><input type="checkbox" checked={mergeSmall} disabled={!!working}
          onChange={(event) => setMergeSmall(event.target.checked)} /> 合并小分组</label>
        {mergeSmall && <label>媒体数少于 <input aria-label="合并阈值" type="number" min="1" max="100000" step="1"
          value={threshold} disabled={!!working} onChange={(event) => setThreshold(event.target.value)} /> 个</label>}
      </div>}
      <div className="cloud-actions">
        <span>归档根目录</span>
        {folder && <PrivateText text={folder.name} compact />}
        {folder && <Button size="sm" variant="outline" disabled={!!working} onClick={() => setChoosingFolder(!choosingFolder)}>
          {choosingFolder ? "取消更换" : "更换目录"}
        </Button>}
      </div>
      {choosingFolder && <CloudFolderBrowser start={folder?.id} mode="upload" submitLabel="使用此目录"
        submit={async (destination) => {
          setFolder(folder?.id === destination.id ? folder : destination);
          setChoosingFolder(false);
          return true;
        }} />}
      {!validThreshold && <p role="alert">合并阈值需要是 1 到 100000 之间的整数。</p>}
      <div className="cloud-actions">
        <Button variant="outline" disabled={!canPreview} onClick={() => void previewArchive()}>
          {working === "preview" ? "正在计算…" : plan ? "重新生成预览" : "生成目录预览"}
        </Button>
        {plan && <Button disabled={!current || !validThreshold || !!working || choosingFolder || !plan.media_count}
          onClick={() => void upload()}>
          {working === "upload" ? "正在加入队列…" : "确认并加入上传队列"}
        </Button>}
      </div>
      {plan && <>
        <p>{plan.media_count} 个媒体 · 预计上传 {plan.queued_count} 个 / {bytes(plan.bytes)} · 已有归档记录 {plan.known_count} 个
          {plan.unavailable_count > 0 ? ` · 本地不可用 ${plan.unavailable_count} 个` : ""}</p>
        {!current && <p role="status">设置已修改，重新生成预览后可以提交。</p>}
        {hidden ? <Button variant="outline" onClick={reveal}>显示目录层级和标签</Button> : <>
          <ul className="cloud-archive-folders" aria-label="归档目录预览">
            {plan.folders.map((item) => <li key={JSON.stringify(item.path)}>
              <span>{item.path.join(" / ")}</span>
              <small>{item.count} 个媒体{item.known > 0 ? ` · 已有记录 ${item.known}` : ""}</small>
            </li>)}
          </ul>
          {!!plan.aliases.length && <details className="cloud-archive-editor">
            <summary>编辑目录别名</summary>
            <p>目录名会显示在云盘上，可用别名隐藏标签、作者或频道名称。</p>
            {plan.aliases.map((item, index) => <label key={item.key}>
              <span>{item.path.join(" / ") || item.label}</span>
              <input aria-label={`目录别名 ${index + 1}`} placeholder={item.label} value={aliases[item.key] || ""} maxLength={200}
                disabled={!!working} onChange={(event) => setAliases((previous) => {
                  const next = { ...previous };
                  const value = event.target.value;
                  if (value) next[item.key] = value;
                  else delete next[item.key];
                  return next;
                })} />
            </label>)}
          </details>}
          {mode === "tag" && !!plan.tag_choices.length && <details className="cloud-archive-editor">
            <summary>调整多标签内容的归档标签</summary>
            {plan.tag_choices.map((item, index) => <label key={item.id}>
              <span>{item.title}</span>
              <select aria-label={`归档标签 ${index + 1}`} value={item.tags.includes(tags[item.id]) ? tags[item.id] : item.selected}
                disabled={!!working} onChange={(event) => setTags((previous) => ({ ...previous, [item.id]: event.target.value }))}>
                {item.tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
              </select>
            </label>)}
          </details>}
        </>}
      </>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </Modal>;
}
