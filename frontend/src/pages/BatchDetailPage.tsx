import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Camera, CheckCircle2, Minus, Pencil } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "@/components/PageHeader";
import { ExpiryBadge, ProcessBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DateInput } from "@/components/ui/date-input";
import { ConfirmDialog, Dialog, DialogContent } from "@/components/ui/dialog";
import { Alert, ErrorState, Spinner } from "@/components/ui/feedback";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { PackSizeInput, packInvalid, packPayload, type PackSize } from "@/components/PackSizeInput";
import { api, query } from "@/lib/api";
import type { Batch, Movement, MovementType, NotificationItem, Page } from "@/lib/types";
import { formatPack } from "@/lib/units";
import { daysLabel, formatDate, formatDateTime, formatMoney, formatQty, isIsoDate } from "@/lib/utils";

const MOVEMENT_LABELS: Record<MovementType, string> = {
  received: "Received",
  consumed: "Used / consumed",
  sold: "Sold",
  discarded: "Discarded / wasted",
  returned: "Returned to supplier",
  correction: "Stock count correction",
};

function useBatchInvalidation(batchId: string) {
  const client = useQueryClient();
  return () => {
    client.invalidateQueries({ queryKey: ["batch", batchId] });
    client.invalidateQueries({ queryKey: ["movements", batchId] });
    client.invalidateQueries({ queryKey: ["batches"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
  };
}

export function AdjustStockDialog({ batch, open, onOpenChange }: { batch: Batch; open: boolean; onOpenChange: (o: boolean) => void }) {
  const invalidate = useBatchInvalidation(batch.id);
  const [type, setType] = useState<MovementType>("consumed");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const current = Number(batch.current_quantity);
  const qty = Number(quantity);
  const isCorrection = type === "correction";
  const resulting = isCorrection ? qty : type === "received" ? current + qty : current - qty;
  const invalid =
    quantity === "" || !Number.isFinite(qty) || qty < 0 || (!isCorrection && qty === 0) || resulting < 0 || (isCorrection && !reason.trim());

  const adjust = useMutation({
    mutationFn: () =>
      api.post<Batch>(`/api/inventory/batches/${batch.id}/adjust-stock`, {
        movement_type: type,
        ...(isCorrection ? { new_quantity: qty } : { quantity: qty }),
        reason: reason.trim() || null,
      }),
    onSuccess: (updated) => {
      invalidate();
      toast.success(`Stock updated: ${formatQty(updated.current_quantity)} ${updated.unit} left`);
      setQuantity("");
      setReason("");
      setConfirming(false);
      onOpenChange(false);
    },
    onError: (error) => {
      setConfirming(false);
      toast.error(error.message);
    },
  });

  const destructive = type === "discarded" || type === "returned";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Update stock" description={`${batch.product.name} · ${formatQty(current)} ${batch.unit} in stock`}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (invalid) return;
            if (destructive || isCorrection) setConfirming(true);
            else adjust.mutate();
          }}
        >
          <Field label="What happened?" htmlFor="movement_type" required>
            <Select id="movement_type" value={type} onChange={(e) => setType(e.target.value as MovementType)}>
              {(Object.keys(MOVEMENT_LABELS) as MovementType[]).map((key) => (
                <option key={key} value={key}>
                  {MOVEMENT_LABELS[key]}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label={isCorrection ? `Counted quantity (${batch.unit})` : `Quantity (${batch.unit})`}
            htmlFor="adjust_quantity"
            required
            error={!isCorrection && resulting < 0 ? `Only ${formatQty(current)} ${batch.unit} in stock` : undefined}
            hint={quantity !== "" && resulting >= 0 ? `Stock after this change: ${formatQty(resulting)} ${batch.unit}` : undefined}
          >
            <Input
              id="adjust_quantity"
              type="number"
              inputMode="decimal"
              step="any"
              min="0"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              aria-invalid={resulting < 0}
            />
          </Field>
          <Field label={isCorrection ? "Reason" : "Note (optional)"} htmlFor="reason" required={isCorrection}>
            <Textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. used for morning lattes" />
          </Field>
          <Button type="submit" className="w-full" disabled={invalid} loading={adjust.isPending}>
            Save change
          </Button>
        </form>
        <ConfirmDialog
          open={confirming}
          onOpenChange={setConfirming}
          title={isCorrection ? "Correct the stock count?" : `Record ${MOVEMENT_LABELS[type].toLowerCase()}?`}
          description={`This sets ${batch.product.name} to ${formatQty(resulting)} ${batch.unit}. The change is recorded in the stock history.`}
          confirmLabel="Yes, record it"
          destructive={destructive}
          loading={adjust.isPending}
          onConfirm={() => adjust.mutate()}
        />
      </DialogContent>
    </Dialog>
  );
}

function EditBatchDialog({ batch, open, onOpenChange }: { batch: Batch; open: boolean; onOpenChange: (o: boolean) => void }) {
  const invalidate = useBatchInvalidation(batch.id);
  const [values, setValues] = useState({
    batch_number: batch.batch_number ?? "",
    manufacturing_date: batch.manufacturing_date ?? "",
    expiry_date: batch.expiry_date ?? "",
    expiry_type: batch.expiry_type,
    supplier_name: batch.supplier?.name ?? "",
    purchase_price: batch.purchase_price ?? "",
    received_date: batch.received_date,
    notes: batch.notes ?? "",
  });
  const [pack, setPack] = useState<PackSize>({ size: batch.pack_size ? String(Number(batch.pack_size)) : "", unit: batch.pack_unit ?? "" });
  const set = (key: keyof typeof values) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setValues((v) => ({ ...v, [key]: e.target.value }));
  const setDate = (key: "manufacturing_date" | "expiry_date" | "received_date") => (value: string) => setValues((v) => ({ ...v, [key]: value }));
  const badFormat = [values.manufacturing_date, values.expiry_date].some((d) => d && !isIsoDate(d)) || !isIsoDate(values.received_date);
  const dateError =
    values.manufacturing_date && values.expiry_date && values.expiry_date < values.manufacturing_date
      ? "Expiry date cannot be before the manufacturing date"
      : undefined;

  const save = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = {
        batch_number: values.batch_number || null,
        manufacturing_date: values.manufacturing_date || null,
        expiry_type: values.expiry_type,
        supplier_name: values.supplier_name || null,
        purchase_price: values.purchase_price === "" ? null : Number(values.purchase_price),
        received_date: values.received_date,
        notes: values.notes || null,
        ...packPayload(pack),
      };
      if ((values.expiry_date || null) !== batch.expiry_date) payload.expiry_date = values.expiry_date || null;
      return api.patch<Batch>(`/api/inventory/batches/${batch.id}`, payload);
    },
    onSuccess: () => {
      invalidate();
      toast.success("Batch updated");
      onOpenChange(false);
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Edit batch" description="Typing a new expiry date marks it as confirmed by you.">
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!dateError && !badFormat && !packInvalid(pack)) save.mutate();
          }}
        >
          <Field label="Batch number" htmlFor="e_batch">
            <Input id="e_batch" value={values.batch_number} onChange={set("batch_number")} />
          </Field>
          <Field label="Supplier" htmlFor="e_supplier">
            <Input id="e_supplier" value={values.supplier_name} onChange={set("supplier_name")} />
          </Field>
          <Field label="Manufacturing date" htmlFor="e_mfg">
            <DateInput id="e_mfg" value={values.manufacturing_date} onChange={setDate("manufacturing_date")} />
          </Field>
          <Field label="Expiry / best-before" htmlFor="e_exp" error={dateError}>
            <DateInput id="e_exp" value={values.expiry_date} onChange={setDate("expiry_date")} aria-invalid={!!dateError} />
          </Field>
          <Field label="Date type" htmlFor="e_type">
            <Select id="e_type" value={values.expiry_type} onChange={set("expiry_type")}>
              <option value="expiry">Expiry / use-by</option>
              <option value="best_before">Best before</option>
            </Select>
          </Field>
          <Field label="Size per pack" htmlFor="e_pack">
            <PackSizeInput id="e_pack" value={pack} onChange={setPack} />
          </Field>
          <Field label="Price per unit" htmlFor="e_price">
            <Input id="e_price" type="number" step="any" min="0" value={values.purchase_price} onChange={set("purchase_price")} />
          </Field>
          <Field label="Date received" htmlFor="e_received" required>
            <DateInput id="e_received" value={values.received_date} onChange={setDate("received_date")} />
          </Field>
          <Field label="Notes" htmlFor="e_notes" className="sm:col-span-2">
            <Textarea id="e_notes" value={values.notes} onChange={set("notes")} />
          </Field>
          <Button type="submit" className="sm:col-span-2" loading={save.isPending} disabled={!!dateError || badFormat}>
            Save changes
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium">{children}</dd>
    </div>
  );
}

export function BatchDetailPage() {
  const { batchId = "" } = useParams();
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmDateOpen, setConfirmDateOpen] = useState(false);
  const invalidate = useBatchInvalidation(batchId);

  const batch = useQuery({ queryKey: ["batch", batchId], queryFn: () => api.get<Batch>(`/api/inventory/batches/${batchId}`) });
  const movements = useQuery({
    queryKey: ["movements", batchId],
    queryFn: () => api.get<Page<Movement>>(`/api/inventory/batches/${batchId}/movements${query({ page_size: 50 })}`),
  });
  const notifications = useQuery({
    queryKey: ["notifications", "batch", batchId],
    queryFn: () => api.get<Page<NotificationItem>>(`/api/notifications${query({ batch_id: batchId, page_size: 10 })}`),
  });
  const confirmDate = useMutation({
    mutationFn: () => api.patch<Batch>(`/api/inventory/batches/${batchId}`, { expiry_confirmed: true }),
    onSuccess: () => {
      invalidate();
      setConfirmDateOpen(false);
      toast.success("Expiry date confirmed — reminders are now active");
    },
    onError: (error) => toast.error(error.message),
  });

  if (batch.isLoading) return <Spinner />;
  if (batch.isError || !batch.data) return <ErrorState error={batch.error} onRetry={() => batch.refetch()} />;
  const b = batch.data;

  return (
    <>
      <Link to="/inventory" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Inventory
      </Link>
      <PageHeader
        title={b.product.name}
        description={[b.product.brand, b.product.category, b.batch_number && `Batch ${b.batch_number}`].filter(Boolean).join(" · ") || undefined}
        actions={
          <>
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              <Pencil /> Edit
            </Button>
            <Button variant="outline" asChild>
              <Link to={`/scan?batch=${b.id}`}>
                <Camera /> Scan package
              </Link>
            </Button>
            <Button onClick={() => setAdjustOpen(true)}>
              <Minus /> Update stock
            </Button>
          </>
        }
      />

      {b.status === "requires_confirmation" && (
        <Alert tone="warning" className="mb-4" title="Expiry date not confirmed">
          <p>
            The date {formatDate(b.expiry_date)} was read automatically or entered as unverified. No reminders are sent until someone checks the
            package and confirms it.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setConfirmDateOpen(true)}>
              <CheckCircle2 /> I checked — date is correct
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
              Enter a different date
            </Button>
          </div>
        </Alert>
      )}
      {b.status === "no_expiry_date" && (
        <Alert tone="info" className="mb-4" title="No expiry date recorded">
          Scan the package or edit the batch to add its expiry date so SmartShelf can remind you.
        </Alert>
      )}
      {b.status === "expired" && (
        <Alert tone="error" className="mb-4" title="This batch has passed its recorded date">
          It is still counted as stock. Record it as discarded or returned once it has been dealt with.
        </Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Batch details</CardTitle>
            <ExpiryBadge status={b.status} />
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Detail label="In stock">
                {formatQty(b.current_quantity)} {b.unit}
              </Detail>
              <Detail label="Received">
                {formatQty(b.quantity_received)} {b.unit}
              </Detail>
              <Detail label="Size per pack">{formatPack(b.pack_size, b.pack_unit) || "—"}</Detail>
              <Detail label={b.expiry_type === "best_before" ? "Best before" : "Expiry date"}>
                {formatDate(b.expiry_date)}
                {b.expiry_date && <span className="block text-xs font-normal text-muted-foreground">{daysLabel(b.days_until_expiry)}</span>}
              </Detail>
              <Detail label="Date verification">
                {b.expiry_date_status === "confirmed" ? "Confirmed" : b.expiry_date_status === "unconfirmed" ? "Needs confirmation" : "No date"}
              </Detail>
              <Detail label="Manufactured">{formatDate(b.manufacturing_date)}</Detail>
              <Detail label="Date received">{formatDate(b.received_date)}</Detail>
              <Detail label="Supplier">{b.supplier?.name ?? "—"}</Detail>
              <Detail label="Price per unit">{formatMoney(b.purchase_price)}</Detail>
              <Detail label="Invoice">
                {b.invoice_id ? (
                  <Link className="text-primary hover:underline" to={`/invoices/${b.invoice_id}`}>
                    {b.invoice_number ?? "View invoice"}
                  </Link>
                ) : (
                  "—"
                )}
              </Detail>
              <Detail label="Added">
                {formatDateTime(b.created_at)}
                {b.created_by_name && <span className="block text-xs font-normal text-muted-foreground">by {b.created_by_name}</span>}
              </Detail>
            </dl>
            {b.notes && <p className="mt-4 rounded-md bg-muted/50 p-3 text-sm">{b.notes}</p>}
          </CardContent>
        </Card>

        <div className="space-y-4">
          {b.image_scan_id && (
            <Card className="overflow-hidden">
              <img src={`/api/scans/${b.image_scan_id}/image`} alt={`Packaging photo of ${b.product.name}`} className="max-h-64 w-full object-contain bg-muted" />
            </Card>
          )}
          <Card>
            <CardHeader>
              <CardTitle>Reminders for this batch</CardTitle>
            </CardHeader>
            <CardContent>
              {notifications.data?.items.length ? (
                <ul className="space-y-2 text-sm">
                  {notifications.data.items.map((n) => (
                    <li key={n.id} className="flex items-center justify-between gap-2">
                      <span>
                        {n.kind === "expired" ? "Expired alert" : n.offset_days === 0 ? "On expiry day" : `${n.offset_days} day(s) before`}
                        <span className="block text-xs text-muted-foreground">{n.recipient_email}</span>
                      </span>
                      <ProcessBadge status={n.status} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No reminders yet.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Stock history</CardTitle>
        </CardHeader>
        <CardContent>
          {movements.isLoading ? (
            <Spinner />
          ) : (
            <ol className="divide-y text-sm">
              {movements.data?.items.map((m) => (
                <li key={m.id} className="flex items-start justify-between gap-3 py-2.5">
                  <div>
                    <p className="font-medium">{MOVEMENT_LABELS[m.movement_type]}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatDateTime(m.created_at)}
                      {m.created_by_name ? ` · ${m.created_by_name}` : ""}
                      {m.reason ? ` · ${m.reason}` : ""}
                    </p>
                  </div>
                  <div className="text-right tabular-nums">
                    <p className={Number(m.quantity_delta) < 0 ? "text-red-700" : "text-emerald-700"}>
                      {Number(m.quantity_delta) > 0 ? "+" : ""}
                      {formatQty(m.quantity_delta)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      → {formatQty(m.quantity_after)} {b.unit}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      <AdjustStockDialog batch={b} open={adjustOpen} onOpenChange={setAdjustOpen} />
      {editOpen && <EditBatchDialog batch={b} open={editOpen} onOpenChange={setEditOpen} />}
      <ConfirmDialog
        open={confirmDateOpen}
        onOpenChange={setConfirmDateOpen}
        title="Confirm expiry date?"
        description={`Confirm that the package shows ${formatDate(b.expiry_date)}. Expiry reminders will then be scheduled for this batch.`}
        confirmLabel="Confirm date"
        loading={confirmDate.isPending}
        onConfirm={() => confirmDate.mutate()}
      />
    </>
  );
}
