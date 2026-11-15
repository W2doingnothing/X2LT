export type Filter = "all" | "pending" | "completed" | "ignored";
export type Settings = {
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
export type Event = {
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
