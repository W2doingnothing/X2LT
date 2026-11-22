import assert from "node:assert/strict";
import { test } from "node:test";
import { comicPages, turnComicPage } from "../src/comicNavigation.ts";

test("two-page reading keeps the cover and an unmatched last page alone", () => {
  assert.deepEqual(comicPages(0, 6, true), [0]);
  assert.deepEqual(comicPages(2, 6, true), [1, 2]);
  assert.deepEqual(comicPages(4, 6, true), [3, 4]);
  assert.deepEqual(comicPages(5, 6, true), [5]);
  assert.deepEqual(comicPages(4, 5, true), [3, 4]);
  assert.deepEqual(comicPages(2, 6, false), [2]);
  assert.deepEqual(comicPages(0, 0, true), []);
});

test("page turns do not skip the first spread or cross the ends", () => {
  assert.equal(turnComicPage(0, 6, true, 1), 1);
  assert.equal(turnComicPage(2, 6, true, 1), 3);
  assert.equal(turnComicPage(1, 6, true, -1), 0);
  assert.equal(turnComicPage(5, 6, true, -1), 3);
  assert.equal(turnComicPage(4, 5, true, 1), 4);
  assert.equal(turnComicPage(0, 1, true, 1), 0);
  assert.equal(turnComicPage(3, 6, false, -1), 2);
});
