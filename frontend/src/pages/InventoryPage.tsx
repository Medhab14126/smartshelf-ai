import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Package, Plus, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { ExpiryBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Spinner } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, query } from "@/lib/api";
import type { Batch, Page, Supplier } from "@/lib/types";
import { formatPack } from "@/lib/units";
import { daysLabel, formatDate, formatQty } from "@/lib/utils";

const STATUS_OPTIONS = [
  ["active", "All in stock"],
  ["expired", "Expired"],
  ["expiring_today", "Expiring today"],
  ["expiring_soon", "Expiring soon"],
  ["requires_confirmation", "Date needs confirmation"],
  ["no_expiry_date", "No expiry date"],
  ["fresh", "Fresh"],
  ["depleted", "Depleted"],
  ["all", "Everything (incl. depleted)"],
] as const;

const PAGE_SIZE = 25;

export function InventoryPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const status = params.get("status") ?? "active";
  const within = params.get("within");
  const category = params.get("category") ?? "";
  const supplierId = params.get("supplier") ?? "";
  const sort = params.get("sort") ?? "expiry_asc";
  const page = Number(params.get("page") ?? "1");

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next, { replace: true });
  };

  // Debounce the search box into the URL.
  useEffect(() => {
    const handle = setTimeout(() => {
      if ((params.get("q") ?? "") !== search) setParam("q", search || null);
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const batches = useQuery({
    queryKey: ["batches", { q: params.get("q"), status, within, category, supplierId, sort, page }],
    queryFn: () =>
      api.get<Page<Batch>>(
        `/api/inventory/batches${query({
          q: params.get("q"),
          status: within ? "active" : status,
          expires_within_days: within,
          category,
          supplier_id: supplierId,
          sort,
          page,
          page_size: PAGE_SIZE,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
  const categories = useQuery({ queryKey: ["categories"], queryFn: () => api.get<string[]>("/api/categories") });
  const suppliers = useQuery({ queryKey: ["suppliers"], queryFn: () => api.get<Supplier[]>("/api/suppliers") });

  const totalPages = batches.data ? Math.max(1, Math.ceil(batches.data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <PageHeader
        title="Inventory"
        description="Each row is one batch. The same product can have several batches with different expiry dates."
        actions={
          <Button asChild>
            <Link to="/inventory/new">
              <Plus /> Add stock
            </Link>
          </Button>
        }
      />

      <Card className="mb-4 p-3 sm:p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <Label htmlFor="search" className="sr-only">
              Search
            </Label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="search" className="pl-9" placeholder="Search product, brand or batch…" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>
          <div>
            <Label htmlFor="status" className="sr-only">
              Expiry status
            </Label>
            <Select
              id="status"
              value={within ? `within:${within}` : status}
              onChange={(e) => {
                const next = new URLSearchParams(params);
                next.delete("within");
                next.delete("page");
                const value = e.target.value;
                if (value.startsWith("within:")) next.set("within", value.slice(7));
                else next.set("status", value);
                setParams(next, { replace: true });
              }}
            >
              {within && <option value={`within:${within}`}>Confirmed, expiring within {within} days</option>}
              {STATUS_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3 lg:col-span-2">
            <div>
              <Label htmlFor="category" className="sr-only">
                Category
              </Label>
              <Select id="category" value={category} onChange={(e) => setParam("category", e.target.value || null)}>
                <option value="">All categories</option>
                {categories.data?.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="supplier" className="sr-only">
                Supplier
              </Label>
              <Select id="supplier" value={supplierId} onChange={(e) => setParam("supplier", e.target.value || null)}>
                <option value="">All suppliers</option>
                {suppliers.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 text-sm">
          <Label htmlFor="sort" className="text-muted-foreground">
            Sort by
          </Label>
          <Select id="sort" className="h-9 w-auto" value={sort} onChange={(e) => setParam("sort", e.target.value)}>
            <option value="expiry_asc">Expiry date (soonest first)</option>
            <option value="expiry_desc">Expiry date (latest first)</option>
            <option value="received_desc">Recently received</option>
            <option value="name_asc">Product name</option>
            <option value="quantity_asc">Lowest quantity</option>
          </Select>
        </div>
      </Card>

      {batches.isLoading ? (
        <Spinner label="Loading inventory…" />
      ) : batches.isError ? (
        <ErrorState error={batches.error} onRetry={() => batches.refetch()} />
      ) : !batches.data?.items.length ? (
        <EmptyState
          icon={<Package />}
          title="No batches match"
          description="Try a different filter, upload an invoice, or add stock manually."
          action={
            <Button asChild variant="outline">
              <Link to="/inventory/new">Add stock</Link>
            </Button>
          }
        />
      ) : (
        <>
          {/* Desktop table */}
          <Card className="hidden overflow-hidden md:block">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-3 font-medium">Product</th>
                  <th className="px-4 py-3 font-medium">Batch</th>
                  <th className="px-4 py-3 text-right font-medium">In stock</th>
                  <th className="px-4 py-3 font-medium">Expiry</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Supplier</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {batches.data.items.map((b) => (
                  <tr key={b.id} className="hover:bg-accent/40">
                    <td className="px-4 py-3">
                      <Link to={`/inventory/${b.id}`} className="font-medium hover:underline">
                        {b.product.name}
                      </Link>
                      <p className="text-xs text-muted-foreground">{[b.product.brand, b.product.category].filter(Boolean).join(" · ") || "—"}</p>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{b.batch_number ?? "—"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {formatQty(b.current_quantity)} <span className="text-muted-foreground">{b.unit}</span>
                      {b.pack_size && <span className="block text-xs text-muted-foreground">{formatPack(b.pack_size, b.pack_unit)} each</span>}
                    </td>
                    <td className="px-4 py-3">
                      {formatDate(b.expiry_date)}
                      {b.expiry_date && <p className="text-xs text-muted-foreground">{daysLabel(b.days_until_expiry)}</p>}
                    </td>
                    <td className="px-4 py-3">
                      <ExpiryBadge status={b.status} />
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{b.supplier?.name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {/* Mobile cards */}
          <ul className="space-y-2 md:hidden">
            {batches.data.items.map((b) => (
              <li key={b.id}>
                <Link to={`/inventory/${b.id}`}>
                  <Card className="flex items-center justify-between gap-3 p-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{b.product.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatQty(b.current_quantity)} {b.unit}
                        {b.pack_size ? ` × ${formatPack(b.pack_size, b.pack_unit)}` : ""}
                        {b.batch_number ? ` · Batch ${b.batch_number}` : ""} · {formatDate(b.expiry_date)}
                      </p>
                    </div>
                    <ExpiryBadge status={b.status} />
                  </Card>
                </Link>
              </li>
            ))}
          </ul>

          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {batches.data.total} batch{batches.data.total === 1 ? "" : "es"}
            </span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setParam("page", String(page - 1))}>
                Previous
              </Button>
              <span>
                Page {page} of {totalPages}
              </span>
              <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setParam("page", String(page + 1))}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
