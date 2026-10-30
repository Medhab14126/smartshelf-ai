import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { ExpiryStatus, InvoiceStatus, ScanStatus } from "@/lib/types";

type Tone = NonNullable<BadgeProps["tone"]>;

export const EXPIRY_STATUS: Record<ExpiryStatus, { label: string; tone: Tone; color: string }> = {
  expired: { label: "Expired", tone: "red", color: "#dc2626" },
  expiring_today: { label: "Expires today", tone: "orange", color: "#ea580c" },
  expiring_soon: { label: "Expiring soon", tone: "amber", color: "#d97706" },
  requires_confirmation: { label: "Confirm date", tone: "violet", color: "#7c3aed" },
  no_expiry_date: { label: "No expiry date", tone: "neutral", color: "#94a3b8" },
  fresh: { label: "Fresh", tone: "green", color: "#16a34a" },
  depleted: { label: "Depleted", tone: "neutral", color: "#cbd5e1" },
};

export function ExpiryBadge({ status }: { status: ExpiryStatus }) {
  const config = EXPIRY_STATUS[status];
  return <Badge tone={config.tone}>{config.label}</Badge>;
}

const PROCESS_STATUS: Record<string, { label: string; tone: Tone }> = {
  uploaded: { label: "Queued", tone: "blue" },
  processing: { label: "Processing", tone: "blue" },
  awaiting_review: { label: "Needs review", tone: "amber" },
  awaiting_confirmation: { label: "Needs confirmation", tone: "amber" },
  confirmed: { label: "Confirmed", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  rejected: { label: "Rejected", tone: "neutral" },
  pending: { label: "Pending", tone: "blue" },
  sent: { label: "Sent", tone: "green" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  accepted: { label: "Accepted", tone: "green" },
  added_to_inventory: { label: "In inventory", tone: "green" },
};

export function ProcessBadge({ status }: { status: InvoiceStatus | ScanStatus | string }) {
  const config = PROCESS_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={config.tone}>{config.label}</Badge>;
}
