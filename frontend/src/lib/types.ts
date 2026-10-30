// Types mirror the FastAPI response schemas (see /docs on the backend).

export type ExpiryStatus =
  | "depleted"
  | "no_expiry_date"
  | "requires_confirmation"
  | "expired"
  | "expiring_today"
  | "expiring_soon"
  | "fresh";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Business {
  id: string;
  name: string;
  timezone: string;
  currency: string;
  date_format: "DMY" | "MDY";
}

export interface User {
  id: string;
  email: string;
  full_name: string;
}

export interface Me {
  user: User;
  business: Business;
  role: "owner" | "staff";
}

export interface Product {
  id: string;
  name: string;
  brand: string | null;
  category: string | null;
  default_unit: string | null;
  package_size: string | null;
  barcode: string | null;
  product_code: string | null;
  notes: string | null;
  created_at: string;
  active_batches: number;
  total_quantity: string;
}

export interface Supplier {
  id: string;
  name: string;
}

export interface Batch {
  id: string;
  product: Product;
  supplier: Supplier | null;
  invoice_id: string | null;
  invoice_item_id: string | null;
  invoice_number: string | null;
  batch_number: string | null;
  quantity_received: string;
  current_quantity: string;
  unit: string;
  pack_size: string | null;
  pack_unit: string | null;
  manufacturing_date: string | null;
  expiry_date: string | null;
  expiry_type: "expiry" | "best_before";
  expiry_date_status: "missing" | "unconfirmed" | "confirmed";
  expiry_confirmed_at: string | null;
  purchase_price: string | null;
  received_date: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
  status: ExpiryStatus;
  days_until_expiry: number | null;
  image_scan_id: string | null;
  created_by_name: string | null;
}

export type MovementType = "received" | "consumed" | "sold" | "discarded" | "returned" | "correction";

export interface Movement {
  id: string;
  batch_id: string;
  movement_type: MovementType;
  quantity_delta: string;
  quantity_after: string;
  reason: string | null;
  created_at: string;
  created_by_name: string | null;
  product_name: string | null;
}

export type InvoiceStatus = "uploaded" | "processing" | "awaiting_review" | "confirmed" | "failed" | "rejected";

export interface InvoiceSummary {
  id: string;
  status: InvoiceStatus;
  original_filename: string;
  supplier_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  currency: string | null;
  total: string | null;
  created_at: string;
  item_count: number;
  error_message: string | null;
}

export type InvoiceItemStatus = "pending" | "accepted" | "rejected" | "added_to_inventory";

export interface InvoiceItem {
  id: string;
  line_number: number;
  product_name: string;
  product_code: string | null;
  quantity: string | null;
  unit: string | null;
  pack_size: string | null;
  pack_unit: string | null;
  unit_price: string | null;
  line_total: string | null;
  confidence: number | null;
  warnings: string[];
  extracted: Record<string, string | null> | null;
  edited: boolean;
  status: InvoiceItemStatus;
  product_id: string | null;
  quantity_added: string;
  suggested_product_id: string | null;
  suggested_product_name: string | null;
}

export interface Invoice extends InvoiceSummary {
  content_type: string;
  file_size: number;
  page_count: number | null;
  ocr_engine: string | null;
  extraction_method: string | null;
  raw_text: string | null;
  warnings: string[];
  confidence: number | null;
  attempts: number;
  subtotal: string | null;
  tax: string | null;
  processed_at: string | null;
  confirmed_at: string | null;
  items: InvoiceItem[];
}

export type FieldStatus = "detected" | "uncertain" | "ambiguous" | "not_found";

export interface ExtractedField {
  value: string | null;
  status: FieldStatus;
  source: string | null;
  reasons: string[];
  options?: string[];
}

export interface DateCandidate {
  raw: string;
  line: string;
  field: string;
  label: string | null;
  parsed: { value: string | null; alternatives: string[]; status: string; notes: string[] };
}

export type ScanStatus = "uploaded" | "processing" | "awaiting_confirmation" | "confirmed" | "failed";

export interface Scan {
  id: string;
  status: ScanStatus;
  original_filename: string;
  content_type: string;
  product_id: string | null;
  batch_id: string | null;
  invoice_item_id: string | null;
  quantity_hint: string | null;
  ocr_engine: string | null;
  raw_text: string | null;
  barcode: string | null;
  barcode_format: string | null;
  extraction: {
    fields: Record<string, ExtractedField>;
    date_candidates: DateCandidate[];
    warnings: string[];
    model_source: string | null;
  } | null;
  warnings: string[];
  error_message: string | null;
  attempts: number;
  confirmed: Record<string, unknown> | null;
  confirmed_at: string | null;
  created_at: string;
  processed_at: string | null;
  matched_product_id: string | null;
  matched_product_name: string | null;
}

export interface NotificationItem {
  id: string;
  batch_id: string;
  kind: "expiry_reminder" | "expired";
  offset_days: number;
  expiry_date: string;
  recipient_email: string;
  status: "pending" | "sent" | "failed" | "cancelled";
  subject: string | null;
  attempts: number;
  last_error: string | null;
  next_attempt_at: string | null;
  sent_at: string | null;
  created_at: string;
  product_name: string | null;
  batch_number: string | null;
}

export interface NotificationSettings {
  enabled: boolean;
  recipient_emails: string[];
  reminder_days: number[];
  notify_on_expired: boolean;
  send_hour_local: number;
  smtp_configured: boolean;
}

export interface DashboardSummary {
  today: string;
  soon_days: number;
  active_batches: number;
  expiring_within_7_days: number;
  expiring_today: number;
  expired_needing_attention: number;
  requires_confirmation: number;
  no_expiry_date: number;
  status_counts: Record<ExpiryStatus, number>;
  recent_batches: Batch[];
  recent_invoices: InvoiceSummary[];
  recent_movements: Movement[];
  recent_notifications: NotificationItem[];
  notifications_configured: boolean;
  smtp_configured: boolean;
  is_empty: boolean;
}

export interface UpcomingExpiry {
  batch_id: string;
  product_name: string;
  batch_number: string | null;
  current_quantity: string;
  unit: string;
  expiry_date: string | null;
  expiry_type: string;
  days_until_expiry: number | null;
  status: ExpiryStatus;
  updated_at: string;
}

export interface AiStatus {
  ocr: { available: boolean; engine: string | null; detail?: string };
  llm: {
    enabled: boolean;
    model: string;
    vision_model: string | null;
    reachable: boolean;
    model_available: boolean;
    vision_model_available: boolean;
    detail?: string;
  };
  email: { smtp_configured: boolean; host: string | null };
}
