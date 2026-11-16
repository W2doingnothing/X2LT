import type { Author } from "./types";
import { Avatar } from "./Collections";
import { ArrowUpRight } from "lucide-react";

export function Authors({
  authors,
  onOpen,
  onProfile,
}: {
  authors: Author[];
  onOpen: (author: Author) => void;
  onProfile: (handle: string) => void;
}) {
  return (
    <div className="author-grid">
      {authors.map((a) => (
        <article className="author-card" key={a.key}>
          <button
            className="author-collection"
            aria-label={`打开博主合集 ${a.display_name || a.handle || "未知推主"}`}
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
            <strong>{a.display_name || a.handle || "未知推主"}</strong>
            <span>
              {a.media_count} 个媒体 · {a.message_count} 条推文
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
    </div>
  );
}
