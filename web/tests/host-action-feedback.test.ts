import assert from "node:assert/strict";
import test from "node:test";
import { hostActionFeedback } from "../src/components/host-action-feedback.ts";

test("only a succeeded host receipt gets positive feedback", () => {
  for (const [outcome, kind] of [
    ["succeeded", "ok"],
    ["dispatched", "warn"],
    ["resolved", "warn"],
    ["interrupted", "warn"],
    ["failed", "error"],
    ["running", "warn"],
    ["unrecognized", "warn"],
  ] as const) {
    const feedback = hostActionFeedback({ id: "receipt-1", outcome }, "Riavvio agente");
    assert.equal(feedback.kind, kind, `outcome=${outcome}`);
    assert.match(feedback.text, /receipt-1/);
  }
});

test("an interrupted receipt communicates that completion is unverified", () => {
  const feedback = hostActionFeedback({ id: "r-interrupted", outcome: "interrupted" }, "Riavvio agente");
  assert.match(feedback.text, /interrott|non verificat/i);
  assert.doesNotMatch(feedback.text, /completat[ao]/i);
});
