import type { FleetService, FleetServicePreview, ServiceOperation } from "../lib/types";

export interface ScopedServicePreview {
  hostId: string;
  serviceId: string;
  operation: ServiceOperation;
  receiptId: string;
}

export function serviceOperationAllowed(service: FleetService | undefined, operation: ServiceOperation): boolean {
  if (!service) return false;
  return operation === "start" ? service.can_start : operation === "stop" ? service.can_stop : service.can_restart;
}

export function verifiedServicePreview(
  response: FleetServicePreview & { host_id?: string; service_id?: string; operation?: string },
  hostId: string,
  serviceId: string,
  operation: ServiceOperation,
): boolean {
  return typeof response?.receipt_id === "string" && response.receipt_id.length > 0 &&
    response.preview?.status === "dry-run" &&
    response.preview.mutation_count === 0 &&
    response.preview.requires_confirmation === true &&
    (response.host_id === undefined || response.host_id === hostId) &&
    (response.service_id === undefined || response.service_id === serviceId) &&
    (response.operation === undefined || response.operation === operation);
}

export function servicePreviewMatches(
  preview: ScopedServicePreview | null,
  hostId: string,
  serviceId: string,
  operation: ServiceOperation,
): preview is ScopedServicePreview {
  return preview?.hostId === hostId && preview.serviceId === serviceId && preview.operation === operation;
}
