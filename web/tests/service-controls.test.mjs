import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/lib/api.ts";
import { serviceOperationAllowed, servicePreviewMatches, verifiedServicePreview } from "../src/components/service-controls.ts";

const service = {
  id: "demo.service", name: "Demo", state: "active", substate: "running",
  description: "Example", can_start: false, can_stop: true, can_restart: true,
};

test("service controls respect capability flags and bind confirmation to one exact scope", () => {
  assert.equal(serviceOperationAllowed(service, "start"), false);
  assert.equal(serviceOperationAllowed(service, "stop"), true);
  const selected = { hostId: "studio", serviceId: service.id, operation: "restart", receiptId: "r1" };
  assert.equal(servicePreviewMatches(selected, "studio", service.id, "restart"), true);
  assert.equal(servicePreviewMatches(selected, "minipc", service.id, "restart"), false);
  assert.equal(servicePreviewMatches(selected, "studio", "another.service", "restart"), false);
  assert.equal(servicePreviewMatches(selected, "studio", service.id, "stop"), false);
});

test("only an exact no-mutation preview with confirmation can be applied", () => {
  const valid = { receipt_id: "preview-1", preview: { status: "dry-run", mutation_count: 0, requires_confirmation: true } };
  assert.equal(verifiedServicePreview(valid, "studio", service.id, "restart"), true);
  assert.equal(verifiedServicePreview({ ...valid, service_id: "other" }, "studio", service.id, "restart"), false);
  assert.equal(verifiedServicePreview({ ...valid, operation: "stop" }, "studio", service.id, "restart"), false);
  assert.equal(verifiedServicePreview({ ...valid, preview: { ...valid.preview, requires_confirmation: false } }, "studio", service.id, "restart"), false);
  assert.equal(verifiedServicePreview({ ...valid, preview: { ...valid.preview, mutation_count: 1 } }, "studio", service.id, "restart"), false);
});

test("service API uses encoded same-origin URLs, CSRF, fixed confirmation and paged logs", async () => {
  const previousFetch = globalThis.fetch;
  const previousDocument = globalThis.document;
  const calls = [];
  try {
    globalThis.document = { cookie: "__Host-controlcenter_csrf=example-csrf" };
    globalThis.fetch = async (input, init) => {
      calls.push({ input, init });
      return new Response(JSON.stringify({
        host_id: "studio", service_id: "svc/a", entries: [], next_cursor: null, has_more: false,
        receipt_id: "r1", preview: { status: "dry-run", mutation_count: 0, requires_confirmation: true },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    };
    await api.serviceLogs("studio", "svc/a", "next+page");
    await api.previewServiceOperation("studio", "svc/a", "restart");
    await api.dispatchServiceOperation("studio", "svc/a", "restart", "key-123", "preview-1");
    assert.equal(calls[0].input, "/api/v1/fleet/studio/services/svc%2Fa/logs?limit=20&cursor=next%2Bpage");
    assert.equal(calls[0].init.credentials, "same-origin");
    assert.equal(calls[1].input, "/api/v1/fleet/studio/services/svc%2Fa/restart/preview");
    assert.equal(calls[1].init.headers.get("X-CSRF-Token"), "example-csrf");
    assert.equal(calls[2].input, "/api/v1/fleet/studio/services/svc%2Fa/restart");
    assert.deepEqual(JSON.parse(calls[2].init.body), {
      confirmation: "CONFERMA", idempotency_key: "key-123", preview_receipt_id: "preview-1",
    });
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.document = previousDocument;
  }
});

test("service API preserves 403/409 errors from the authenticated request client", async () => {
  const previousFetch = globalThis.fetch;
  const previousDocument = globalThis.document;
  try {
    globalThis.document = { cookie: "__Host-controlcenter_csrf=example-csrf" };
    for (const [status, message] of [[403, "Operation denied"], [409, "Stale preview"]]) {
      globalThis.fetch = async () => new Response(JSON.stringify({ detail: message }), {
        status, headers: { "Content-Type": "application/json" },
      });
      await assert.rejects(api.previewServiceOperation("studio", service.id, "stop"), new RegExp(message));
    }
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.document = previousDocument;
  }
});
