import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, RefreshCw, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "@/components/PageHeader";
import { ProcessBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { EmptyState, ErrorState, Spinner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, query } from "@/lib/api";
import type { NotificationItem, Page } from "@/lib/types";
import { formatDate, formatDateTime } from "@/lib/utils";

function describe(n: NotificationItem) {
  if (n.kind === "expired") return "Expired alert";
  if (n.offset_days === 0) return "Expiry-day reminder";
  return `${n.offset_days}-day reminder`;
}

export function NotificationsPage() {
  const client = useQueryClient();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [resend, setResend] = useState<NotificationItem | null>(null);
  const list = useQuery({
    queryKey: ["notifications", status, page],
    queryFn: () => api.get<Page<NotificationItem>>(`/api/notifications${query({ status, page, page_size: 25 })}`),
    refetchInterval: (q) => (q.state.data?.items.some((n) => n.status === "pending") ? 5000 : false),
  });
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["notifications"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const retry = useMutation({
    mutationFn: ({ id, force }: { id: string; force: boolean }) => api.post<NotificationItem>(`/api/notifications/${id}/retry${force ? "?force=true" : ""}`),
    onSuccess: (n) => {
      refresh();
      setResend(null);
      toast.success(n.status === "sent" ? "Email sent" : n.status === "failed" ? `Still failing: ${n.last_error}` : "Queued for sending");
    },
    onError: (error) => toast.error(error.message),
  });
  const runCheck = useMutation({
    mutationFn: () => api.post<{ scheduled: number }>("/api/notifications/run-check"),
    onSuccess: (r) => {
      refresh();
      toast.success(r.scheduled ? `${r.scheduled} new reminder(s) queued` : "No new reminders are due right now");
    },
    onError: (error) => toast.error(error.message),
  });
  const totalPages = list.data ? Math.max(1, Math.ceil(list.data.total / 25)) : 1;

  return (
    <>
      <PageHeader
        title="Notifications"
        description="Expiry reminder emails. Each reminder is sent once per batch, recipient and interval."
        actions={
          <Button variant="outline" onClick={() => runCheck.mutate()} loading={runCheck.isPending}>
            <RefreshCw /> Check for due reminders now
          </Button>
        }
      />
      <div className="mb-4 flex items-center gap-2">
        <Label htmlFor="n_status" className="text-sm text-muted-foreground">
          Show
        </Label>
        <Select
          id="n_status"
          className="w-auto"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All</option>
          <option value="pending">Pending</option>
          <option value="sent">Sent</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </Select>
      </div>
      {list.isLoading ? (
        <Spinner />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data?.items.length ? (
        <EmptyState
          icon={<Bell />}
          title="No notifications"
          description="Reminders appear here when batches with confirmed expiry dates reach your reminder intervals."
        />
      ) : (
        <>
          <Card className="divide-y">
            {list.data.items.map((n) => (
              <div key={n.id} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="font-medium">
                    <Link to={`/inventory/${n.batch_id}`} className="hover:underline">
                      {n.product_name}
                    </Link>
                    {n.batch_number && <span className="text-muted-foreground"> · batch {n.batch_number}</span>}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {describe(n)} · expiry {formatDate(n.expiry_date)} · to {n.recipient_email}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {n.status === "sent"
                      ? `Sent ${formatDateTime(n.sent_at)}`
                      : n.status === "pending" && n.next_attempt_at
                        ? `Next attempt ${formatDateTime(n.next_attempt_at)}`
                        : `Created ${formatDateTime(n.created_at)}`}
                    {n.attempts > 0 && ` · ${n.attempts} attempt(s)`}
                  </p>
                  {n.last_error && <p className="mt-1 text-xs text-red-700">{n.last_error}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <ProcessBadge status={n.status} />
                  {n.status === "failed" && (
                    <Button size="sm" variant="outline" onClick={() => retry.mutate({ id: n.id, force: false })} loading={retry.isPending && retry.variables?.id === n.id}>
                      <RotateCcw /> Retry
                    </Button>
                  )}
                  {n.status === "sent" && (
                    <Button size="sm" variant="ghost" onClick={() => setResend(n)}>
                      Resend
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </Card>
          {totalPages > 1 && (
            <div className="mt-3 flex justify-end gap-2">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </div>
          )}
        </>
      )}
      <ConfirmDialog
        open={!!resend}
        onOpenChange={(o) => !o && setResend(null)}
        title="Send this reminder again?"
        description={resend ? `${resend.recipient_email} already received this reminder. Send it once more?` : ""}
        confirmLabel="Resend email"
        loading={retry.isPending}
        onConfirm={() => resend && retry.mutate({ id: resend.id, force: true })}
      />
    </>
  );
}
