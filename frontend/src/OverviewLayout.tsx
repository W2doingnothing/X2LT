import { useRef, useState, type ReactNode } from "react";
import { GripVertical } from "lucide-react";
import { Button } from "./components/ui/button";
import "./overview-layout.css";

const names = {
  stats: "媒体统计",
  recent: "最近添加",
  libraries: "媒体库",
} as const;

type ModuleId = keyof typeof names;
type Width = "half" | "full";
type Module = { id: ModuleId; visible: boolean; width: Width };

const defaults: Module[] = [
  { id: "stats", visible: true, width: "full" },
  { id: "recent", visible: true, width: "full" },
  { id: "libraries", visible: true, width: "full" },
];

export function parseOverviewLayout(value?: string): Module[] {
  if (!value) return defaults;
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed) || parsed.length !== defaults.length)
      return defaults;
    const ids = new Set<ModuleId>();
    const result: Module[] = [];
    for (const entry of parsed) {
      if (!entry || !defaults.some(({ id }) => id === entry.id) ||
        ids.has(entry.id) || typeof entry.visible !== "boolean" ||
        (entry.width !== "half" && entry.width !== "full"))
        return defaults;
      ids.add(entry.id);
      result.push({ id: entry.id, visible: entry.visible, width: entry.width });
    }
    return result;
  } catch {
    return defaults;
  }
}

export function OverviewLayout({
  layout,
  modules,
  onSave,
}: {
  layout: Module[];
  modules: Record<ModuleId, ReactNode>;
  onSave: (value: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<Module[] | null>(null);
  const [saving, setSaving] = useState(false);
  const dragged = useRef<ModuleId | null>(null);
  const visible = layout.filter((item) => item.visible);

  function update(id: ModuleId, change: Partial<Omit<Module, "id">>) {
    setDraft((current) => current?.map((item) =>
      item.id === id ? { ...item, ...change } : item,
    ) || null);
  }

  function move(from: ModuleId, to: ModuleId) {
    if (from === to || saving) return;
    setDraft((current) => {
      if (!current) return current;
      const index = current.findIndex((item) => item.id === from);
      const target = current.findIndex((item) => item.id === to);
      if (index === -1 || target === -1) return current;
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
  }

  async function save() {
    if (!draft || saving) return;
    setSaving(true);
    try {
      const value = JSON.stringify(draft);
      if (await onSave(value === JSON.stringify(defaults) ? "" : value))
        setDraft(null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="overview-layout">
      {!draft && (
        <div className="overview-layout-actions">
          <Button size="sm" variant="outline" onClick={() => setDraft(layout)}>
            编辑布局
          </Button>
        </div>
      )}
      {draft && (
        <section className="overview-layout-editor" aria-label="编辑总览布局">
          <ol className="overview-module-list">
            {draft.map((item, index) => (
              <li
                key={item.id}
                data-module={item.id}
                onDragOver={(event) => {
                  if (dragged.current && !saving) event.preventDefault();
                }}
                onDrop={(event) => {
                  if (!dragged.current || saving) return;
                  event.preventDefault();
                  move(dragged.current, item.id);
                  dragged.current = null;
                }}
              >
                <span
                  className="overview-module-handle"
                  title="拖动排序"
                  aria-hidden="true"
                  draggable={!saving}
                  onDragStart={(event) => {
                    dragged.current = item.id;
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", item.id);
                  }}
                  onDragEnd={() => { dragged.current = null; }}
                >
                  <GripVertical size={17} />
                </span>
                <label className="overview-module-toggle">
                  <input type="checkbox" aria-label={`显示${names[item.id]}`}
                    checked={item.visible} disabled={saving}
                    onChange={(event) => update(item.id, { visible: event.target.checked })} />
                  {names[item.id]}
                </label>
                <select aria-label={`${names[item.id]}宽度`} value={item.width} disabled={saving}
                  onChange={(event) => update(item.id, { width: event.target.value as Width })}>
                  <option value="full">整行</option>
                  <option value="half">半宽</option>
                </select>
                <Button type="button" size="sm" variant="outline"
                  disabled={saving || index === 0} aria-label={`上移${names[item.id]}`}
                  onClick={() => move(item.id, draft[index - 1].id)}>上移</Button>
                <Button type="button" size="sm" variant="outline"
                  disabled={saving || index === draft.length - 1} aria-label={`下移${names[item.id]}`}
                  onClick={() => move(item.id, draft[index + 1].id)}>下移</Button>
              </li>
            ))}
          </ol>
          <div className="overview-layout-actions">
            <Button size="sm" variant="ghost" disabled={saving}
              onClick={() => setDraft(defaults)}>恢复默认</Button>
            <Button size="sm" variant="outline" disabled={saving}
              onClick={() => setDraft(null)}>取消</Button>
            <Button size="sm" disabled={saving} onClick={() => void save()}>
              {saving ? "正在保存…" : "保存布局"}
            </Button>
          </div>
        </section>
      )}
      <div className="overview-layout-grid">
        {visible.map((item) => (
          <div key={item.id} data-module={item.id} data-width={item.width}>
            {modules[item.id]}
          </div>
        ))}
      </div>
    </div>
  );
}
