import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, CalendarX, ClipboardCheck, FileUp, Package, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { PageHeader } from "@/components/PageHeader";
import { ExpiryBadge, ProcessBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { api, query } from "@/lib/api";
import type { DashboardSummary, ExpiryStatus, UpcomingExpiry } from "@/lib/types";
import { cn, daysLabel, formatDate, formatDateTime, formatQty } from "@/lib/utils";

const WINDOWS = [
  { key: "upcoming", label: "Needs attention" },
  { key: "today", label: "Expiring today" },
  { key: "3d", label: "Next 3 days" },
  { key: "7d", label: "Next 7 days" },
  { key: "expired", label: "Already expired" },
  { key: "missing", label: "Missing dates" },
  { key: "unconfirmed", label: "Verify date" },
] as const;

// Ordered by urgency; validated for lightness band, labelled directly on the chart.
const CHART_STATUSES: { key: ExpiryStatus; label: string; color: string }[] = [
  { key: "expired", label: "Expired", color: "#b91c1c" },
  { key: "expiring_today", label: "Today", color: "#c2410c" },
  { key: "expiring_soon", label: "Soon", color: "#a16207" },
  { key: "requires_confirmation", label: "Confirm date", color: "#7c3aed" },
  { key: "no_expiry_date", label: "No date", color: "#64748b" },
  { key: "fresh", label: "Fresh", color: "#15803d" },
];

function StatCard({
  label,
  value,
  icon: Icon,
  tone,
  to,
}: {
  label: string;
  value: number;
  icon: typeof Package;
  tone: string;
  to: string;
}) {
  return (
    <Link to={to} className="group">
      <Card className="h-full transition-shadow group-hover:shadow-md">
        <CardContent className="flex items-start justify-between gap-3 p-4 sm:p-4">
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
          </div>
          <span className={cn("rounded-lg p-2", tone)}>
            <Icon className="size-5" aria-hidden />
          </span>
        </CardContent>
      </Card>
    </Link>
  );
}

function StatusChart({ counts }: { counts: Record<ExpiryStatus, number> }) {
  const data = CHART_STATUSES.map((s) => ({ ...s, count: counts[s.key] ?? 0 }));
  return (
    <>
      <div className="h-64" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 32, bottom: 4, left: 8 }} barCategoryGap={6}>
            <CartesianGrid horizontal={false} stroke="#e7e9e4" />
            <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12, fill: "#6b746e" }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="label" width={92} tick={{ fontSize: 12, fill: "#3b4540" }} axisLine={false} tickLine={false} />
            <Tooltip
              cursor={{ fill: "rgba(47,107,79,0.06)" }}
              formatter={(value: number) => [`${value} batch${value === 1 ? "" : "es"}`, "Batches"]}
            />
            <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={22}>
              {data.map((d) => (
                <Cell key={d.key} fill={d.color} />
              ))}
              <LabelList dataKey="count" position="right" style={{ fontSize: 12, fill: "#3b4540" }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <caption>Inventory batches by expiry status</caption>
        <tbody>
          {data.map((d) => (
            <tr key={d.key}>
              <th scope="row">{d.label}</th>
              <td>{d.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function UpcomingList() {
  const [windowKey, setWindowKey] = useState<(typeof WINDOWS)[number]["key"]>("upcoming");
  const upcoming = useQuery({
    queryKey: ["dashboard", "upcoming", windowKey],
    queryFn: () => api.get<UpcomingExpiry[]>(`/api/dashboard/upcoming-expiry${query({ window: windowKey, limit: 15 })}`),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Upcoming expiry</CardTitle>
        <div className="-mx-1 mt-2 flex gap-1.5 overflow-x-auto px-1 pb-1" role="tablist" aria-label="Expiry filter">
          {WINDOWS.map((w) => (
            <button
              key={w.key}
              type="button"
              role="tab"
              aria-selected={windowKey === w.key}
              onClick={() => setWindowKey(w.key)}
              className={cn(
                "whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                windowKey === w.key ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-accent",
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {upcoming.isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : upcoming.isError ? (
          <ErrorState error={upcoming.error} onRetry={() => upcoming.refetch()} />
        ) : upcoming.data?.length ? (
          <ul className="divide-y">
            {upcoming.data.map((item) => (
              <li key={item.batch_id}>
                <Link to={`/inventory/${item.batch_id}`} className="flex items-center justify-between gap-3 py-2.5 hover:bg-accent/40">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{item.product_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {item.batch_number ? `Batch ${item.batch_number} · ` : ""}
                      {formatQty(item.current_quantity)} {item.unit} · {formatDate(item.expiry_date)}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <ExpiryBadge status={item.status} />
                    <span className="text-xs text-muted-foreground">{item.expiry_date ? daysLabel(item.days_until_expiry) : ""}</span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">Nothing in this view. 🎉</p>
        )}
      </CardContent>
    </Card>
  );
}

export function DashboardPage() {
  const summary = useQuery({ queryKey: ["dashboard", "summary"], queryFn: () => api.get<DashboardSummary>("/api/dashboard/summary") });

  if (summary.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      </div>
    );
  }
  if (summary.isError || !summary.data) return <ErrorState error={summary.error} onRetry={() => summary.refetch()} />;
  const data = summary.data;

  if (data.is_empty) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <EmptyState
          icon={<Package />}
          title="Welcome to SmartShelf AI"
          description="Start by uploading a supplier invoice — we'll read the items for you — or add a product manually. Expiry reminders start as soon as batches have confirmed dates."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Button asChild>
                <Link to="/invoices">
                  <FileUp /> Upload your first invoice
                </Link>
              </Button>
              <Button variant="outline" asChild>
                <Link to="/inventory/new">
                  <Plus /> Add a product manually
                </Link>
              </Button>
            </div>
          }
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={`Today is ${formatDate(data.today)} · "expiring soon" means within ${data.soon_days} days`}
        actions={
          <>
            <Button variant="outline" asChild>
              <Link to="/scan">Scan product</Link>
            </Button>
            <Button asChild>
              <Link to="/invoices">
                <FileUp /> Upload invoice
              </Link>
            </Button>
          </>
        }
      />

      {(!data.notifications_configured || !data.smtp_configured) && (
        <Alert tone="warning" className="mb-4" title="Email reminders are not fully set up">
          {!data.smtp_configured ? "The server has no SMTP configuration, so reminder emails cannot be sent. " : ""}
          {!data.notifications_configured ? "Add at least one recipient in " : "Check "}
          <Link to="/settings" className="font-medium underline">
            Settings
          </Link>
          .
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="Active batches" value={data.active_batches} icon={Package} tone="bg-emerald-50 text-emerald-700" to="/inventory" />
        <StatCard label="Expiring in 7 days" value={data.expiring_within_7_days} icon={CalendarClock} tone="bg-amber-50 text-amber-700" to="/inventory?within=7" />
        <StatCard label="Expiring today" value={data.expiring_today} icon={AlertTriangle} tone="bg-orange-50 text-orange-700" to="/inventory?status=expiring_today" />
        <StatCard label="Expired in stock" value={data.expired_needing_attention} icon={CalendarX} tone="bg-red-50 text-red-700" to="/inventory?status=expired" />
        <StatCard label="Dates to confirm" value={data.requires_confirmation} icon={ClipboardCheck} tone="bg-violet-50 text-violet-700" to="/inventory?status=requires_confirmation" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <UpcomingList />
        <Card>
          <CardHeader>
            <CardTitle>Batches by expiry status</CardTitle>
            <CardDescription>Includes {data.status_counts.depleted} depleted batch(es) not shown</CardDescription>
          </CardHeader>
          <CardContent>
            <StatusChart counts={data.status_counts} />
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle>Recently added</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {data.recent_batches.map((b) => (
                <li key={b.id} className="flex items-center justify-between gap-2">
                  <Link to={`/inventory/${b.id}`} className="truncate hover:underline">
                    {b.product.name}
                  </Link>
                  <ExpiryBadge status={b.status} />
                </li>
              ))}
              {!data.recent_batches.length && <li className="text-muted-foreground">No batches yet</li>}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Recent invoices</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {data.recent_invoices.map((inv) => (
                <li key={inv.id} className="flex items-center justify-between gap-2">
                  <Link to={`/invoices/${inv.id}`} className="truncate hover:underline">
                    {inv.supplier_name || inv.original_filename}
                  </Link>
                  <ProcessBadge status={inv.status} />
                </li>
              ))}
              {!data.recent_invoices.length && <li className="text-muted-foreground">No invoices yet</li>}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Recent stock changes</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {data.recent_movements.map((m) => (
                <li key={m.id}>
                  <Link to={`/inventory/${m.batch_id}`} className="hover:underline">
                    <span className="font-medium capitalize">{m.movement_type}</span> {formatQty(Math.abs(Number(m.quantity_delta)))} ·{" "}
                    {m.product_name}
                  </Link>
                  <p className="text-xs text-muted-foreground">
                    {formatDateTime(m.created_at)}
                    {m.created_by_name ? ` · ${m.created_by_name}` : ""}
                  </p>
                </li>
              ))}
              {!data.recent_movements.length && <li className="text-muted-foreground">No changes yet</li>}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Recent notifications</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {data.recent_notifications.map((n) => (
                <li key={n.id} className="flex items-center justify-between gap-2">
                  <span className="truncate">{n.subject ?? n.product_name}</span>
                  <ProcessBadge status={n.status} />
                </li>
              ))}
              {!data.recent_notifications.length && <li className="text-muted-foreground">No reminders sent yet</li>}
            </ul>
            <Link to="/notifications" className="mt-3 inline-block text-xs font-medium text-primary hover:underline">
              View all
            </Link>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
