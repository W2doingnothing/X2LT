import type { Tweet } from "./types";
import { Avatar } from "./Collections";
import { PrivateText } from "./Privacy";
import { contentPresentation } from "./contentPresentation";
import { ArrowUpRight, File, Globe, MessageCircle } from "lucide-react";
import type { Action } from "./Preferences";
export function ContentIdentity({
  content,
  action,
  body = true,
}: {
  content: Tweet;
  action: Action;
  body?: boolean;
}) {
  const view = contentPresentation(content);
  const identity =
    (view.kind === "social" || view.kind === "message") &&
    !!(content.display_name || content.author);
  return (
    <div className="content-identity">
      {identity ? (
        <Avatar
          tweet={{
            ...content,
            avatar_available:
              content.platform === "x" || !content.platform
                ? content.avatar_available
                : false,
          }}
        />
      ) : (
        <span className="content-symbol" aria-hidden="true">
          {view.kind === "local" ? (
            <File size={20} />
          ) : view.kind === "message" ? (
            <MessageCircle size={20} />
          ) : (
            <Globe size={20} />
          )}
        </span>
      )}
      <div className="tweet-title">
        <div className="author-heading">
          {identity ? (
            <strong>{view.heading}</strong>
          ) : (
            <PrivateText text={view.heading} compact />
          )}
          {view.account &&
            (view.canProfile ? (
              <button
                className="author-link"
                onClick={() =>
                  void action("open_profile", { handle: view.account })
                }
              >
                @{view.account}
                <ArrowUpRight size={11} />
              </button>
            ) : (
              <span className="author-link">@{view.account}</span>
            ))}
        </div>
        <span className="content-source">{view.subtitle}</span>
        {body && view.body && (
          <PrivateText
            key={content.platform + ":" + content.id}
            text={view.body}
            collapsible
          />
        )}
      </div>
    </div>
  );
}
