import type { FleetService, FleetServicePreview, Receipt, ServiceOperation } from "../lib/types";

export const SERVICE_PAGE_SIZE = 40;

export function filterServices(services: FleetService[], query: string): FleetService[] {
  const text = query.trim().toLocaleLowerCase();
  return text ? services.filter((service) =>
    service.name.toLocaleLowerCase().includes(text) || service.id.toLocaleLowerCase().includes(text),
  ) : services;
}

export async function pollServiceReceipt(
  receiptId: string,
  read: (id: string) => Promise<{ receipt: Receipt }>,
  pause: () => Promise<void>,
  stillMounted: () => boolean,
): Promise<Receipt | null> {
  for (let attempt = 0; attempt < 30 && stillMounted(); attempt += 1) {
    await pause();
    if (!stillMounted()) return null;
    try {
      const { receipt } = await read(receiptId);
      if (!stillMounted()) return null;
      if (receipt.id === receiptId && receipt.outcome !== "running") return receipt;
    } catch {
      // A fresh receipt can briefly be unavailable while the operation is running.
    }
  }
  return null;
}

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
  response: FleetServicePreview,
  hostId: string,
  serviceId: string,
  operation: ServiceOperation,
): boolean {
  return typeof response?.receipt_id === "string" && response.receipt_id.length > 0 &&
    response.preview?.status === "dry-run" &&
    response.preview.mutation_count === 0 &&
    response.preview.requires_confirmation === true &&
    response.host_id === hostId &&
    response.service_id === serviceId &&
    response.operation === operation;
}

export function servicePreviewMatches(
  preview: ScopedServicePreview | null,
  hostId: string,
  serviceId: string,
  operation: ServiceOperation,
): preview is ScopedServicePreview {
  return preview?.hostId === hostId && preview.serviceId === serviceId && preview.operation === operation;
}
