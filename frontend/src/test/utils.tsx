import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import type { Batch, Me } from "@/lib/types";

type Handler = (body: unknown, url: URL) => unknown | Promise<unknown>;
export interface MockCall {
  method: string;
  path: string;
  body: unknown;
}

/** Route-table fetch mock: `{"GET /api/x": () => data}`. Unknown routes 404. */
export function mockApi(routes: Record<string, Handler | { status: number; body: unknown }>) {
  const calls: MockCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ method, path: url.pathname, body });
    const route = routes[`${method} ${url.pathname}`];
    if (!route) return new Response(JSON.stringify({ detail: `No mock for ${method} ${url.pathname}` }), { status: 404 });
    if (typeof route === "object" && "status" in route) {
      return new Response(JSON.stringify(route.body), { status: route.status, headers: { "Content-Type": "application/json" } });
    }
    const data = await route(body, url);
    return new Response(JSON.stringify(data ?? null), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function renderApp(ui: ReactElement, { path = "/", route = "/" }: { path?: string; route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <Routes>
            <Route path={path} element={ui} />
            <Route path="*" element={<div>navigated</div>} />
          </Routes>
          <Toaster />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export const ME: Me = {
  user: { id: "u1", email: "owner@example.com", full_name: "Asha Rao" },
  business: { id: "b1", name: "Corner Cup Cafe", timezone: "Asia/Kolkata", currency: "INR", date_format: "DMY" },
  role: "owner",
};

export function makeBatch(overrides: Partial<Batch> = {}): Batch {
  return {
    id: "batch-1",
    product: {
      id: "p1", name: "Milk 500ml", brand: null, category: "Dairy", default_unit: "packet", package_size: null, barcode: null,
      product_code: null, notes: null, created_at: "2026-10-29T10:00:00Z", active_batches: 1, total_quantity: "10",
    },
    supplier: { id: "s1", name: "GreenLeaf Dairy" },
    invoice_id: null, invoice_item_id: null, invoice_number: null, batch_number: "A123",
    quantity_received: "10.000", current_quantity: "10.000", unit: "packet", pack_size: null, pack_unit: null,
    manufacturing_date: null, expiry_date: "2026-11-01", expiry_type: "expiry", expiry_date_status: "confirmed",
    expiry_confirmed_at: null, purchase_price: "30.0000", received_date: "2026-10-29", notes: null,
    created_at: "2026-10-29T10:00:00Z", updated_at: "2026-10-29T10:00:00Z", status: "expiring_soon",
    days_until_expiry: 3, image_scan_id: null, created_by_name: "Asha Rao",
    ...overrides,
  };
}
