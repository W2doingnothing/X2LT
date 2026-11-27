export type Filter = "all" | "pending" | "completed" | "ignored";
export type Settings = {
  confirm_removal: boolean;
  privacy_mode: boolean;
  infinite_scroll?: boolean;
  overview_layout?: string;
  random_video_libraries?: string[];
  theme: "light" | "dark";
  accent: string;
  username: string;
  quality: string;
  connections: number;
  max_mb: number;
  media_mode: string;
  request_strategy: string;
};
export type Library = {
  emoji?: string;
  scan_mode?: "media" | "comic";
  comic_count?: number;
  video_count?: number;
  image_count?: number;
  id: string;
  name: string;
  path: string;
  message_count?: number;
  media_count?: number;
  cover?: { kind: "emoji" | "image"; value: string } | null;
  quota_bytes: number;
  default: boolean;
  online: boolean;
  capacity_pending?: boolean;
  capacity_stale?: boolean;
  capacity_checked_at?: number;
  used_bytes?: number;
  quota_remaining?: number | null;
  disk_free?: number;
  disk_total?: number;
  available_bytes?: number;
  reserved_bytes?: number;
  error?: string;
};
export type Media = {
  local_entry?: number;
  local_location?: number;
  content_id?: number;
  library_id?: string;
  has_source?: boolean;
  filename?: string;
  id: number;
  media_id: string;
  media_type: "video" | "image";
  extension: string;
  state: string;
  error: string;
  bytes: number;
  width: number;
  height: number;
  quality: string;
  output: string;
  verification: string;
};
export type Resource = {
  key: string;
  platform: string;
  source_label: string;
  message_id: string;
  media_id: string;
  media_type: "video" | "image";
  title: string;
  author: string;
  added_at: number | null;
  tags: string[];
  locations: Media[];
};
export type ResourcePage = {
  resources: Resource[];
  total: number;
  offset: number;
};
export type Tweet = {
  source?: {
    kind: "social" | "message" | "website" | "local";
    label?: string;
    title?: string;
    domain?: string;
  };
  id: string;
  author: string;
  handle: string;
  title: string;
  text: string;
  ignored: boolean;
  updated: number;
  media: Media[];
  tags?: string[];
  platform?: string;
  display_name?: string;
  avatar_available?: boolean;
  demoColor?: string;
};
export type Operation = {
  run_id: string;
  phase: "reading" | "downloading" | "paused" | "completed" | "failed";
  reading: boolean;
  discovered_tweets: number;
  discovered_media: number;
  queued: number;
  processed: number;
  successful: number;
  failed: number;
  skipped: number;
  total: number | null;
  message: string;
};
export type Snapshot = {
  library_cards?: boolean;
  sources?: Source[];
  authors?: Author[];
  collection_author_count?: number;
  operation?: Operation | null;
  tag_options?: { name: string; count: number }[];
  version: string;
  settings: Settings;
  has_credentials: boolean;
  credential_error: string;
  libraries: Library[];
  default_library: string | null;
  tasks: Tweet[];
  counts: Record<Filter, number>;
  total: number;
  offset: number;
  busy: boolean;
};
export type Source = {
  key: string;
  label: string;
  message_count: number;
  media_count: number;
};
export type Author = {
  starred?: boolean;
  in_other?: boolean;
  is_group?: boolean;
  member_count?: number;
  key: string;
  handle: string;
  display_name: string;
  avatar_available: boolean;
  message_id: string;
  media_count: number;
  message_count: number;
};
export type AuthorPreference = {
  key: string;
  starred: boolean;
  in_other: boolean;
};
export type Event = {
  scope?: "organize";
  run_id?: string;
  type: string;
  id?: number;
  done?: number;
  total?: number | null;
  time?: number;
  text?: string;
};
export type Payload = Record<string, unknown>;
export type Bridge = {
  call: (
    action: string,
    payload?: Payload,
  ) => Promise<{ ok: boolean; data?: unknown; error?: string }>;
};
declare global {
  interface Window {
    pywebview?: { api: Bridge };
  }
}
