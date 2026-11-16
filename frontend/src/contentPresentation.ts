import type { Tweet } from "./types";
/** Optional source metadata is presentation only. New sources need their own bridge adapters. */
export function contentPresentation(item: Tweet) {
  const platform = item.platform || "x";
  const kind =
    item.source?.kind ||
    (platform === "local"
      ? "local"
      : platform === "x"
        ? "social"
        : platform === "telegram"
          ? "message"
          : "website");
  const label =
    item.source?.label ||
    { x: "X", telegram: "Telegram", local: "本地" }[platform] ||
    item.source?.domain ||
    platform;
  const account = kind === "social" ? item.handle || "" : "";
  const person =
    kind === "social" || kind === "message"
      ? item.display_name || item.author || ""
      : "";
  const fallback =
    item.media[0]?.filename || (kind === "local" ? "本地文件" : "未命名内容");
  const heading = person || item.source?.title || item.title || fallback;
  return {
    platform,
    kind,
    label,
    heading,
    account,
    body: item.text || "",
    subtitle: item.source?.domain || label,
    hasAvatar: !!person && !!item.avatar_available,
    canProfile: platform === "x" && /^[A-Za-z0-9_]{1,15}$/.test(account),
    canSource: platform === "x",
  };
}
