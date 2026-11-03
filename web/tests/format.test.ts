import assert from "node:assert/strict";
import { bytes, dueLabel, exactTimestamp, receiptOutcomeSeverity, sourceReason, uptime } from "../src/lib/format.ts";

assert.equal(bytes(0), "0 B");
assert.equal(bytes(null), "—");
assert.equal(uptime(0), "0m");
assert.equal(uptime(undefined), "—");
assert.equal(exactTimestamp("not-a-date"), "—");
assert.match(exactTimestamp("2026-09-20T20:59:00Z"), /2026/);
assert.equal(dueLabel("not-a-date"), "data non valida");
assert.equal(
  sourceReason("RuntimeError: Public API failed object_not_found Could not find data_source"),
  "La fonte configurata non è disponibile nel workspace corrente.",
);

for (const [outcome, expected] of [
  ["succeeded", "ok"],
  ["failed", "crit"],
  ["dispatched", "warn"],
  ["running", "warn"],
  ["interrupted", "warn"],
  ["manual-gate", "warn"],
  ["dry-run", "unknown"],
  ["rolled-back", "unknown"],
  ["unexpected-outcome", "unknown"],
] as const) {
  assert.equal(receiptOutcomeSeverity(outcome), expected, `receipt ${outcome} must not imply success`);
}
