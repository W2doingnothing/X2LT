import type { Event } from "./types";
export type Progress = {
  done: number;
  total: number | null;
  time: number;
  speed: number;
  received: number;
};
export function reconcileProgress(
  previous: Record<number, Progress>,
  events: Event[],
  runId: string | null,
  previousRun: string | null,
  now: number,
): Record<number, Progress> {
  const next = { ...(runId === previousRun ? previous : {}) };
  for (const event of events) {
    if (event.type !== "progress" || event.id === undefined) continue;
    if (runId && event.run_id !== runId) continue;
    if (!runId && event.run_id) continue;
    const old = next[event.id],
      time = event.time || 0;
    next[event.id] = {
      done: event.done || 0,
      total: event.total || null,
      time,
      received: now,
      speed:
        old && time > old.time
          ? Math.max(0, ((event.done || 0) - old.done) / (time - old.time))
          : 0,
    };
  }
  return next;
}
