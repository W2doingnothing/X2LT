import { useState, useEffect, useRef } from "react";
import type { Author } from "./types";
import { Avatar } from "./Collections";
import { ArrowUpRight, Star, MoreHorizontal } from "lucide-react";

export function Authors({
  authors,
  onOpen,
  onProfile,
  onStar,
  onArchive,
  archived = false,
  busy = false,
}: {
  authors: Author[];
  onOpen: (author: Author) => void;
  onProfile: (handle: string) => void;
  onStar: (author: Author) => void;
  onArchive: (author: Author) => void;
  archived?: boolean;
  busy?: boolean;
}) {
  const [menu, setMenu] = useState<{
    author: Author;
    x: number;
    y: number;
  } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  function close() {
    setMenu(null);
    trigger.current?.focus();
  }
  function show(author: Author, x: number, y: number, element: HTMLElement) {
    trigger.current = element;
    setMenu({
      author,
      x: Math.max(8, Math.min(x, window.innerWidth - 170)),
      y: Math.max(8, Math.min(y, window.innerHeight - 70)),
    });
  }
  useEffect(() => {
    if (!menu) return;
    menuRef.current?.querySelector("button")?.focus();
    const click = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(null);
    };
    const key = (e: KeyboardEvent) => {
      if (["Backspace", "Escape"].includes(e.key)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        close();
      }
    };
    document.addEventListener("pointerdown", click);
    window.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("pointerdown", click);
      window.removeEventListener("keydown", key, true);
    };
  }, [menu]);
  return (
    <div className="author-grid">
      {authors.map((a) => (
        <article
          className="author-card"
          key={a.key}
          onContextMenu={(e) => {
            e.preventDefault();
            show(
              a,
              e.clientX,
              e.clientY,
              e.currentTarget.querySelector(".author-more")!,
            );
          }}
        >
          <button
            className="icon-btn author-more"
            aria-label={"更多操作 " + (a.display_name || a.handle)}
            aria-haspopup="menu"
            onClick={(e) => {
              const box = e.currentTarget.getBoundingClientRect();
              show(a, box.left, box.bottom, e.currentTarget);
            }}
          >
            <MoreHorizontal size={18} />
          </button>
          {
            <button
              className={"author-star " + (a.starred ? "starred" : "")}
              disabled={busy}
              aria-pressed={!!a.starred}
              aria-label={`${a.starred ? "取消星标" : "星标"} ${a.display_name || a.handle || "未知作者"}`}
              onClick={() => onStar(a)}
            >
              <Star size={19} fill={a.starred ? "currentColor" : "none"} />
            </button>
          }
          <button
            className="author-collection"
            aria-label={`打开作者合集 ${a.display_name || a.handle || "未知作者"}`}
            onClick={() => onOpen(a)}
          >
            <Avatar
              tweet={{
                id: a.message_id,
                platform: "x",
                author: a.handle,
                handle: a.handle,
                display_name: a.display_name,
                avatar_available: a.avatar_available,
              }}
            />
            <strong>{a.display_name || a.handle || "未知作者"}</strong>
            <span>
              {a.media_count} 个媒体 · {a.message_count} 条内容
            </span>
          </button>
          {a.handle && (
            <button className="author-link" onClick={() => onProfile(a.handle)}>
              @{a.handle}
              <ArrowUpRight size={12} />
            </button>
          )}
        </article>
      ))}
      {menu && (
        <div
          ref={menuRef}
          className="author-menu"
          role="menu"
          style={{ left: menu.x, top: menu.y }}
        >
          <button
            role="menuitem"
            disabled={busy}
            onClick={() => {
              onArchive(menu.author);
              close();
            }}
          >
            {archived ? "取消归档" : "归档作者"}
          </button>
        </div>
      )}
    </div>
  );
}
