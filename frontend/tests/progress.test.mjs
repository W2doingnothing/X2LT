import { test } from "node:test";
import assert from "node:assert/strict";
import { reconcileProgress } from "../src/progress.ts";
test("new run clears previous bytes and rejects stale run progress", () => {
  const previous = {
    7: { done: 90, total: 100, time: 1, speed: 10, received: 1 },
  };
  const events = [
    { type: "progress", run_id: "old", id: 7, done: 99, total: 100, time: 2 },
    { type: "progress", run_id: "new", id: 8, done: 5, total: 10, time: 3 },
  ];
  const result = reconcileProgress(previous, events, "new", "old", 123);
  assert.equal(result[7], undefined);
  assert.equal(result[8].done, 5);
  assert.equal(result[8].speed, 0);
  assert.deepEqual(reconcileProgress(previous, [], "new", "old", 123), {});
  assert.equal(
    reconcileProgress(
      result,
      [
        {
          type: "progress",
          run_id: "old",
          id: 8,
          done: 100,
          total: 100,
          time: 5,
        },
      ],
      "new",
      "new",
      125,
    )[8].done,
    5,
  );
});
