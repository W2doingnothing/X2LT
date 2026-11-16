import type { Author } from "./types";
import { Avatar } from "./Collections";
import { ArrowUpRight, Star, UsersRound } from "lucide-react";

export function Authors({
  authors,
  onOpen,
  onProfile,
  onStar,
  busy = false,
}: {
  authors: Author[];
  onOpen: (author: Author) => void;
  onProfile: (handle: string) => void;
  onStar: (author: Author) => void;
  busy?: boolean;
}) {
  return (
    <div className="author-grid">
      {authors.map((a) => (
        <article className="author-card" key={a.key}>
          {!a.is_group && (
            <button
              className={"author-star " + (a.starred ? "starred" : "")}
              disabled={busy}
              aria-pressed={!!a.starred}
              aria-label={`${a.starred ? "取消星标" : "星标"} ${a.display_name || a.handle || "未知推主"}`}
              onClick={() => onStar(a)}
            >
              <Star size={19} fill={a.starred ? "currentColor" : "none"} />
            </button>
          )}
          <button
            className="author-collection"
            aria-label={
              a.is_group
                ? "打开其他博主"
                : `打开博主合集 ${a.display_name || a.handle || "未知推主"}`
            }
            onClick={() => onOpen(a)}
          >
            {a.is_group ? (
              <div className="avatar">
                <UsersRound size={30} />
              </div>
            ) : (
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
            )}
            <strong>{a.display_name || a.handle || "未知推主"}</strong>
            <span>
              {a.is_group && `${a.member_count} 位博主 · `}
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
