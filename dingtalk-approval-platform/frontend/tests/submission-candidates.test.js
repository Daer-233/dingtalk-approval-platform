import assert from "node:assert/strict";
import { test } from "node:test";
import { eligibleApprovalPreviews } from "../src/submission-candidates.js";
const previews = ["new", "failed", "success", "uncertain"].map((group_id) => ({ group_id }));
const attempts = [{ group_id: "failed", status: "failed" }, { group_id: "success", status: "submitted" }, { group_id: "uncertain", status: "uncertain" }];
test("submission confirms only eligible previews", () => {
  assert.deepEqual(eligibleApprovalPreviews(previews, attempts).map((p) => p.group_id), ["new", "failed"]);
});
test("retry confirms only explicitly failed items", () => {
  assert.deepEqual(eligibleApprovalPreviews(previews, attempts, true).map((p) => p.group_id), ["failed"]);
});
test("already submitted and uncertain items cannot require new consent", () => {
  assert.equal(eligibleApprovalPreviews(previews.slice(2), attempts).length, 0);
  assert.equal(eligibleApprovalPreviews([], []).length, 0);
});
