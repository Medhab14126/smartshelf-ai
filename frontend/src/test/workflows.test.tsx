import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdjustStockDialog, BatchDetailPage } from "@/pages/BatchDetailPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { InventoryPage } from "@/pages/InventoryPage";
import { InvoiceReviewPage } from "@/pages/InvoiceReviewPage";
import { InvoicesPage } from "@/pages/InvoicesPage";
import { LoginPage } from "@/pages/AuthPages";
import { ScanReviewPage } from "@/pages/ScanReviewPage";
import type { DashboardSummary, Invoice, Scan } from "@/lib/types";
import { formatDate, formatDateLong, parseDayFirstDate } from "@/lib/utils";

import { ME, makeBatch, mockApi, renderApp } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 25 });

describe("login", () => {
  it("validates input and signs in", async () => {
    const api = mockApi({
      "GET /api/auth/me": { status: 401, body: { detail: "Not authenticated" } },
      "POST /api/auth/login": () => ({ ...ME, access_token: "t" }),
    });
    const user = userEvent.setup();
    renderApp(<LoginPage />, { path: "/login", route: "/login" });

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();

    // Mandatory fields show * and are announced as required.
    expect(screen.getByRole("textbox", { name: "Email (required)" })).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Email/), "owner@example.com");
    await user.type(screen.getByLabelText(/^Password/), "secret123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("navigated");
    const login = api.calls.find((c) => c.path === "/api/auth/login");
    expect(login?.body).toEqual({ email: "owner@example.com", password: "secret123" });
  });

  it("shows the server error on wrong credentials", async () => {
    mockApi({
      "GET /api/auth/me": { status: 401, body: { detail: "Not authenticated" } },
      "POST /api/auth/login": { status: 401, body: { detail: "Incorrect email or password" } },
    });
    const user = userEvent.setup();
    renderApp(<LoginPage />, { path: "/login", route: "/login" });
    await user.type(screen.getByLabelText(/^Email/), "owner@example.com");
    await user.type(screen.getByLabelText(/^Password/), "bad");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Incorrect email or password")).toBeInTheDocument();
  });
});

describe("invoice upload", () => {
  it("uploads a PDF and opens its review page", async () => {
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/invoices": () => page([]),
      "POST /api/invoices/upload": () => ({ invoice: { id: "inv-1", status: "uploaded" }, duplicate: false }),
    });
    const user = userEvent.setup();
    renderApp(<InvoicesPage />, { path: "/invoices", route: "/invoices" });
    const file = new File(["%PDF-1.4"], "invoice.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Choose invoice file"), file);
    await screen.findByText("navigated");
    const upload = api.calls.find((c) => c.path === "/api/invoices/upload");
    expect(upload?.body).toBeInstanceOf(FormData);
    expect((upload?.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("rejects unsupported files before uploading", async () => {
    const api = mockApi({ "GET /api/auth/me": () => ME, "GET /api/invoices": () => page([]) });
    const user = userEvent.setup({ applyAccept: false });
    renderApp(<InvoicesPage />, { path: "/invoices", route: "/invoices" });
    await user.upload(screen.getByLabelText("Choose invoice file"), new File(["x"], "notes.txt", { type: "text/plain" }));
    expect(await screen.findByText("Please choose a PDF, JPEG or PNG file")).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/api/invoices/upload")).toBe(false);
  });
});

const INVOICE: Invoice = {
  id: "inv-1", status: "awaiting_review", original_filename: "invoice.pdf", supplier_name: "GreenLeaf Dairy Supplies",
  invoice_number: "GLD-2026-0457", invoice_date: "2026-10-27", currency: "INR", total: "1050.00", created_at: "2026-10-29T10:00:00Z",
  item_count: 2, error_message: null, content_type: "application/pdf", file_size: 1000, page_count: 1, ocr_engine: "pdf-text",
  extraction_method: "rules", raw_text: "GreenLeaf…", warnings: ["Line totals add up to 1060.00, but the subtotal is 1050.00"],
  confidence: 0.8, attempts: 1, subtotal: "1050.00", tax: null, processed_at: "2026-10-29T10:00:01Z", confirmed_at: null,
  items: [
    { id: "it-1", line_number: 1, product_name: "Toned Milk 500ml", product_code: null, quantity: "20.000", unit: "packet", pack_size: "500.000", pack_unit: "ml", unit_price: "30.0000", line_total: "600.00", confidence: 0.85, warnings: [], extracted: null, edited: false, status: "pending", product_id: null, quantity_added: "0", suggested_product_id: null, suggested_product_name: null },
    { id: "it-2", line_number: 2, product_name: "Paneer 200g", product_code: null, quantity: "5.000", unit: "pcs", pack_size: null, pack_unit: null, unit_price: "92.0000", line_total: "450.00", confidence: 0.45, warnings: ["quantity × unit price = 460.00, but line total is 450.00"], extracted: null, edited: false, status: "pending", product_id: null, quantity_added: "0", suggested_product_id: null, suggested_product_name: null },
  ],
};

describe("invoice review", () => {
  it("shows warnings, lets the user exclude a line and confirms", async () => {
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/invoices/inv-1": () => INVOICE,
      "POST /api/invoices/inv-1/confirm": () => ({
        ...INVOICE,
        status: "confirmed",
        items: [{ ...INVOICE.items[0], status: "added_to_inventory" }, { ...INVOICE.items[1], status: "rejected" }],
      }),
    });
    const user = userEvent.setup();
    renderApp(<InvoiceReviewPage />, { path: "/invoices/:invoiceId", route: "/invoices/inv-1" });

    expect(await screen.findByText(/Line totals add up to 1060.00/)).toBeInTheDocument();
    expect(screen.getByText(/quantity × unit price = 460.00/)).toBeInTheDocument();
    expect(screen.getByLabelText("Supplier")).toHaveValue("GreenLeaf Dairy Supplies");

    const paneer = screen.getByText(/2\. Paneer 200g/).closest("li")!;
    await user.click(within(paneer).getByRole("button", { name: /Exclude/ }));
    await user.clear(screen.getByLabelText("Invoice number"));
    await user.type(screen.getByLabelText("Invoice number"), "GLD-457");

    // Dates are shown and typed day-first, whatever the browser locale.
    const date = screen.getByLabelText("Invoice date");
    expect(date).toHaveValue("27/10/2026");
    await user.clear(date);
    await user.type(date, "31/02/2026");
    expect(screen.getByRole("button", { name: /Confirm invoice/ })).toBeDisabled();
    await user.clear(date);
    await user.type(date, "28/10/26");
    await user.tab();
    expect(date).toHaveValue("28/10/2026");

    await user.click(screen.getByRole("button", { name: /Confirm invoice/ }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm invoice" }));

    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/invoices/inv-1/confirm")).toBe(true));
    const body = api.calls.find((c) => c.path === "/api/invoices/inv-1/confirm")!.body as Record<string, unknown>;
    expect(body.rejected_item_ids).toEqual(["it-2"]);
    expect(body.invoice_number).toBe("GLD-457");
    expect(body.total).toBe(1050);
    expect(body.invoice_date).toBe("2026-10-28");
    expect(await screen.findByText(/1 line added to inventory/)).toBeInTheDocument();
  });
});

describe("invoice deletion", () => {
  it("warns that a confirmed invoice's stock is removed and deletes it", async () => {
    let deleted = false;
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/invoices": () => page(deleted ? [] : [{ ...INVOICE, status: "confirmed" }]),
      "DELETE /api/invoices/inv-1": () => {
        deleted = true;
        return { batches_cleared: 2 };
      },
    });
    const user = userEvent.setup();
    renderApp(<InvoicesPage />, { path: "/invoices", route: "/invoices" });

    await user.click(await screen.findByRole("button", { name: "Delete invoice GLD-2026-0457" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/still in inventory will be removed too/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete invoice" }));

    expect(await screen.findByText(/remaining stock was removed from 2 batches/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/api/invoices/inv-1")).toBe(true);
    expect(await screen.findByText("No invoices yet")).toBeInTheDocument();
  });
});

describe("scan deletion", () => {
  it("deletes a scan from the review page and returns to the scan list", async () => {
    let deleted = false;
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/scans/scan-1": () => SCAN,
      "DELETE /api/scans/scan-1": () => {
        deleted = true;
        return { batches_cleared: 0 };
      },
    });
    const user = userEvent.setup();
    renderApp(<ScanReviewPage />, { path: "/scans/:scanId", route: "/scans/scan-1" });

    await user.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete scan" }));
    await screen.findByText("navigated");
    expect(deleted).toBe(true);
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/api/scans/scan-1")).toBe(true);
  });
});

describe("day-first dates", () => {
  it("parses dd/mm/yyyy and rejects impossible dates", () => {
    expect(parseDayFirstDate("05/04/2027")).toBe("2027-04-05");
    expect(parseDayFirstDate("5-4-27")).toBe("2027-04-05");
    expect(parseDayFirstDate("31.12.2026")).toBe("2026-12-31");
    expect(parseDayFirstDate("31/02/2026")).toBeNull();
    expect(parseDayFirstDate("13/13/2026")).toBeNull();
    expect(parseDayFirstDate("2026-10-27")).toBeNull();
    expect(formatDate("2026-10-27")).toBe("27/10/2026");
    expect(formatDateLong("2027-05-04")).toBe("4 May 2027");
  });
});

const SCAN: Scan = {
  id: "scan-1", status: "awaiting_confirmation", original_filename: "label.jpg", content_type: "image/jpeg", product_id: null,
  batch_id: null, invoice_item_id: null, quantity_hint: null, ocr_engine: "tesseract",
  raw_text: "Fresh Cream\nPKD ON 03/04/2027\nUSE BY 05/04/2027", barcode: null, barcode_format: null,
  extraction: {
    fields: {
      product_name: { value: "Fresh Cream", status: "uncertain", source: "Fresh Cream", reasons: [] },
      expiry_date: { value: "2027-04-05", status: "ambiguous", source: "USE BY 05/04/2027", reasons: ["'05/04/2027' could be read with day and month swapped"], options: ["2027-04-05", "2027-05-04"] },
      packing_date: { value: "2027-04-03", status: "ambiguous", source: "PKD ON 03/04/2027", reasons: [], options: ["2027-04-03", "2027-03-04"] },
      batch_number: { value: null, status: "not_found", source: null, reasons: [] },
      package_size: { value: "200 ml", status: "uncertain", source: "Net Qty: 200 ml", reasons: [] },
    },
    date_candidates: [], warnings: [], model_source: null,
  },
  warnings: [], error_message: null, attempts: 1, confirmed: null, confirmed_at: null, created_at: "2026-10-29T10:00:00Z",
  processed_at: "2026-10-29T10:00:02Z", matched_product_id: null, matched_product_name: null,
};

describe("expiry confirmation from a scan", () => {
  it("requires explicit confirmation and lets the user pick the right reading", async () => {
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/scans/scan-1": () => SCAN,
      "GET /api/products": () => page([]),
      "POST /api/scans/scan-1/confirm": () => ({ ...SCAN, status: "confirmed", batch_id: "batch-9" }),
    });
    const user = userEvent.setup();
    renderApp(<ScanReviewPage />, { path: "/scans/:scanId", route: "/scans/scan-1" });

    expect(await screen.findAllByText("Ambiguous")).not.toHaveLength(0);
    const confirmButton = screen.getByRole("button", { name: /Save and confirm date/ });
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText(/^Quantity/), "6");
    // The pack size printed on the label fills an editable value box and a g/kg/ml/ltr picker.
    const size = screen.getByLabelText("Size per pack");
    expect(size).toHaveValue(200);
    const packUnit = screen.getByLabelText("Pack size unit");
    expect(packUnit).toHaveValue("ml");
    expect(within(packUnit).queryByRole("option", { name: /pcs/ })).toBeNull();
    await user.clear(size);
    await user.type(size, "250");
    // Choose the other interpretation of 05/04/2027.
    const options = screen.getByRole("group", { name: /expiry \/ use-by date readings/i });
    await user.click(within(options).getByRole("button", { name: /4 May 2027|May 4, 2027/ }));
    expect(confirmButton).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I compared the expiry date/ }));
    expect(confirmButton).toBeEnabled();
    await user.click(confirmButton);

    await screen.findByText("navigated");
    const body = api.calls.find((c) => c.path === "/api/scans/scan-1/confirm")!.body as Record<string, unknown>;
    expect(body).toMatchObject({
      expiry_date: "2027-05-04",
      confirm_expiry: true,
      quantity: 6,
      pack_size: 250,
      pack_unit: "ml",
      new_product: { name: "Fresh Cream" },
    });
  });
});

describe("inventory", () => {
  it("lists batches with their expiry status", async () => {
    mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/inventory/batches": () => page([makeBatch(), makeBatch({ id: "batch-2", batch_number: "B", status: "expired", expiry_date: "2026-10-20", days_until_expiry: -9 })]),
      "GET /api/categories": () => ["Dairy"],
      "GET /api/suppliers": () => [{ id: "s1", name: "GreenLeaf Dairy" }],
    });
    renderApp(<InventoryPage />, { path: "/inventory", route: "/inventory" });
    expect((await screen.findAllByText("Milk 500ml")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Expired").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Expiring soon").length).toBeGreaterThan(0);
    expect(screen.getByText("2 batches")).toBeInTheDocument();
  });

  it("prevents removing more stock than available and records a sale", async () => {
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "POST /api/inventory/batches/batch-1/adjust-stock": () => makeBatch({ current_quantity: "7.000" }),
    });
    const user = userEvent.setup();
    renderApp(<AdjustStockDialog batch={makeBatch()} open onOpenChange={() => undefined} />);
    await user.selectOptions(screen.getByLabelText(/^What happened\?/), "sold");
    await user.type(screen.getByLabelText(/Quantity/), "12");
    expect(screen.getByText("Only 10 packet in stock")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save change" })).toBeDisabled();

    await user.clear(screen.getByLabelText(/Quantity/));
    await user.type(screen.getByLabelText(/Quantity/), "3");
    expect(screen.getByText("Stock after this change: 7 packet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith("/adjust-stock"))).toBe(true));
    expect(api.calls.find((c) => c.path.endsWith("/adjust-stock"))!.body).toEqual({ movement_type: "sold", quantity: 3, reason: null });
  });

  it("asks to confirm an unverified expiry date on the batch page", async () => {
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/inventory/batches/batch-1": () => makeBatch({ status: "requires_confirmation", expiry_date_status: "unconfirmed" }),
      "GET /api/inventory/batches/batch-1/movements": () => page([]),
      "GET /api/notifications": () => page([]),
      "PATCH /api/inventory/batches/batch-1": () => makeBatch(),
    });
    const user = userEvent.setup();
    renderApp(<BatchDetailPage />, { path: "/inventory/:batchId", route: "/inventory/batch-1" });
    expect(await screen.findByText("Expiry date not confirmed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /I checked/ }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm date" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ expiry_confirmed: true }));
  });
});

describe("dashboard", () => {
  const summary: DashboardSummary = {
    today: "2026-10-29", soon_days: 7, active_batches: 4, expiring_within_7_days: 2, expiring_today: 1,
    expired_needing_attention: 1, requires_confirmation: 1, no_expiry_date: 0,
    status_counts: { depleted: 0, no_expiry_date: 0, requires_confirmation: 1, expired: 1, expiring_today: 1, expiring_soon: 1, fresh: 0 },
    recent_batches: [], recent_invoices: [], recent_movements: [], recent_notifications: [],
    notifications_configured: true, smtp_configured: true, is_empty: false,
  };

  it("shows real counts and the upcoming expiry list with filters", async () => {
    const api = mockApi({
      "GET /api/auth/me": () => ME,
      "GET /api/dashboard/summary": () => summary,
      "GET /api/dashboard/upcoming-expiry": (_b, url) =>
        url.searchParams.get("window") === "expired"
          ? [{ batch_id: "b2", product_name: "Croissant", batch_number: null, current_quantity: "4", unit: "pcs", expiry_date: "2026-10-27", expiry_type: "expiry", days_until_expiry: -2, status: "expired", updated_at: "" }]
          : [{ batch_id: "b1", product_name: "Milk 500ml", batch_number: "A", current_quantity: "8", unit: "packet", expiry_date: "2026-11-01", expiry_type: "expiry", days_until_expiry: 3, status: "expiring_soon", updated_at: "" }],
    });
    const user = userEvent.setup();
    renderApp(<DashboardPage />);
    expect(await screen.findByText("Milk 500ml")).toBeInTheDocument();
    expect(screen.getByText("In 3 days")).toBeInTheDocument();
    expect(screen.getByText("Expiring in 7 days").nextSibling).toHaveTextContent("2");
    await user.click(screen.getByRole("tab", { name: "Already expired" }));
    expect(await screen.findByText("Croissant")).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/api/dashboard/upcoming-expiry")).toBe(true);
  });

  it("shows onboarding for an empty workspace", async () => {
    mockApi({ "GET /api/auth/me": () => ME, "GET /api/dashboard/summary": () => ({ ...summary, is_empty: true }), "GET /api/dashboard/upcoming-expiry": () => [] });
    renderApp(<DashboardPage />);
    expect(await screen.findByText("Welcome to SmartShelf AI")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upload your first invoice/ })).toBeInTheDocument();
  });
});
