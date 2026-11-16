export type Filter = "all" | "pending" | "completed" | "ignored";
export type Settings = {
  privacy_mode: boolean;
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
  used_bytes?: number;
  quota_remaining?: number | null;
  disk_free?: number;
  disk_total?: number;
  available_bytes?: number;
  reserved_bytes?: number;
  error?: string;
};
export type Media = {
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
