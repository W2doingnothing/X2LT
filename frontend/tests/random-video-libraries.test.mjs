import assert from "node:assert/strict";
import { test } from "node:test";
import { eligibleRandomVideoIds, initialRandomVideoIds } from "../src/randomVideoLibraries.ts";

const library = (id, online = true) => ({ id, online });

test("random video selection restores only unique IDs for available libraries", () => {
  const libraries = [library("a"), library("b"), library("offline", false)];
  const saved = ["b", "removed", "offline", "b"];
  assert.deepEqual(initialRandomVideoIds(libraries, "a", saved), ["b"]);
  assert.deepEqual(eligibleRandomVideoIds(libraries, ["offline", "b", "removed"]), ["b"]);
});

test("random video selection falls back to an available default or the available libraries", () => {
  const libraries = [library("a"), library("b"), library("offline", false)];
  assert.deepEqual(initialRandomVideoIds(libraries, "b"), ["b"]);
  assert.deepEqual(initialRandomVideoIds(libraries, "offline", ["removed"]), ["a", "b"]);
  assert.deepEqual(initialRandomVideoIds([], "b", ["b"]), []);
});
