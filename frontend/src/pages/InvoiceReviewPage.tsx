import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Camera, Check, Loader2, PackagePlus, Pencil, Plus, RefreshCw, Trash2, Undo2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { DeleteUploadDialog } from "@/components/DeleteUploadDialog";
import { PageHeader } from "@/components/PageHeader";
import { PackSizeInput, packInvalid, packPayload, type PackSize } from "@/components/PackSizeInput";
import { ProductPicker, packFromProduct, productPayload, type ProductChoice } from "@/components/ProductPicker";
import { ProcessBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DateInput } from "@/components/ui/date-input";
import { ConfirmDialog, Dialog, DialogContent } from "@/components/ui/dialog";
import { Alert, ErrorState, Spinner } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";
import type { Batch, Invoice, InvoiceItem, Product } from "@/lib/types";
import { formatPack } from "@/lib/units";
import { cn, formatDate, formatMoney, formatQty, isIsoDate } from "@/lib/utils";

type Header = {
  supplier_name: string;
  invoice_number: string;
  invoice_date: string;
  currency: string;
  subtotal: string;
  tax: string;
  total: string;
};

const toHeader = (inv: Invoice): Header => ({
  supplier_name: inv.supplier_name ?? "",
  invoice_number: inv.invoice_number ?? "",
  invoice_date: inv.invoice_date ?? "",
  currency: inv.currency ?? "",
  subtotal: inv.subtotal ?? "",
  tax: inv.tax ?? "",
  total: inv.total ?? "",
});

const headerPayload = (h: Header) => ({
  supplier_name: h.supplier_name.trim() || null,
  invoice_number: h.invoice_number.trim() || null,
  invoice_date: h.invoice_date || null,
  currency: h.currency.trim().toUpperCase() || null,
  subtotal: h.subtotal === "" ? null : Number(h.subtotal),
  tax: h.tax === "" ? null : Number(h.tax),
  total: h.total === "" ? null : Number(h.total),
});

function useInvoiceInvalidation(id: string) {
  const client = useQueryClient();
  return () => {
    client.invalidateQueries({ queryKey: ["invoice", id] });
    client.invalidateQueries({ queryKey: ["invoices"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
  };
}

function ItemDialog({
  invoiceId,
  item,
  open,
  onOpenChange,
}: {
  invoiceId: string;
  item: InvoiceItem | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const invalidate = useInvoiceInvalidation(invoiceId);
  const [values, setValues] = useState({
    product_name: item?.product_name ?? "",
    product_code: item?.product_code ?? "",
    quantity: item?.quantity ?? "",
    unit_price: item?.unit_price ?? "",
    line_total: item?.line_total ?? "",
  });
  const set = (key: keyof typeof values) => (e: React.ChangeEvent<HTMLInputElement>) => setValues((v) => ({ ...v, [key]: e.target.value }));
  const [pack, setPack] = useState<PackSize>({ size: item?.pack_size ? String(Number(item.pack_size)) : "", unit: item?.pack_unit ?? "" });
  const num = (v: string) => (v === "" ? null : Number(v));
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        product_name: values.product_name.trim(),
        product_code: values.product_code.trim() || null,
        quantity: num(values.quantity),
        ...packPayload(pack),
        unit_price: num(values.unit_price),
        line_total: num(values.line_total),
      };
      return item
        ? api.patch<InvoiceItem>(`/api/invoices/${invoiceId}/items/${item.id}`, payload)
        : api.post<InvoiceItem>(`/api/invoices/${invoiceId}/items`, payload);
    },
    onSuccess: () => {
      invalidate();
      toast.success(item ? "Line updated" : "Line added");
      onOpenChange(false);
    },
    onError: (error) => toast.error(error.message),
  });
  const original = item?.extracted;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={item ? `Edit line ${item.line_number}` : "Add a line"} description="Correct anything that was read wrongly.">
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (values.product_name.trim() && !packInvalid(pack)) save.mutate();
          }}
        >
          <Field label="Product name" htmlFor="i_name" className="sm:col-span-2" required hint={original ? `Extracted: ${original.product_name}` : undefined}>
            <Input id="i_name" value={values.product_name} onChange={set("product_name")} required />
          </Field>
          <Field label="Quantity (packs / pieces)" htmlFor="i_qty" hint={original ? `Extracted: ${original.quantity ?? "—"}` : undefined}>
            <Input id="i_qty" type="number" step="any" min="0" value={values.quantity} onChange={set("quantity")} />
          </Field>
          <Field label="Size per pack" htmlFor="i_pack" hint="Read from the product name when printed there, e.g. 500 ml">
            <PackSizeInput id="i_pack" value={pack} onChange={setPack} />
          </Field>
          <Field label="Unit price" htmlFor="i_price" hint={original ? `Extracted: ${original.unit_price ?? "—"}` : undefined}>
            <Input id="i_price" type="number" step="any" min="0" value={values.unit_price} onChange={set("unit_price")} />
          </Field>
          <Field label="Line total" htmlFor="i_total" hint={original ? `Extracted: ${original.line_total ?? "—"}` : undefined}>
            <Input id="i_total" type="number" step="any" min="0" value={values.line_total} onChange={set("line_total")} />
          </Field>
          <Field label="Product code (optional)" htmlFor="i_code" className="sm:col-span-2">
            <Input id="i_code" value={values.product_code} onChange={set("product_code")} />
          </Field>
          <Button type="submit" className="sm:col-span-2" loading={save.isPending}>
            Save line
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type Split = { quantity: string; batch_number: string; expiry_date: string; expiry_confirmed: boolean };

function AddToInventoryDialog({
  invoice,
  item,
  open,
  onOpenChange,
}: {
  invoice: Invoice;
  item: InvoiceItem;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const invalidate = useInvoiceInvalidation(invoice.id);
  const client = useQueryClient();
  const remaining = item.quantity !== null ? Number(item.quantity) - Number(item.quantity_added) : null;
  // With a fuzzy catalogue match, ask the user before picking either option.
  const [product, setProduct] = useState<ProductChoice | null>(
    item.suggested_product_id ? null : { kind: "new", name: item.product_name },
  );
  const [category, setCategory] = useState("");
  // Quantities count packs; the pack size comes from the line (read from its name) and can be changed.
  const [pack, setPack] = useState<PackSize>({ size: item.pack_size ? String(Number(item.pack_size)) : "", unit: item.pack_unit ?? "" });
  const [splits, setSplits] = useState<Split[]>([
    { quantity: remaining !== null ? String(remaining) : "", batch_number: "", expiry_date: "", expiry_confirmed: true },
  ]);
  const total = splits.reduce((sum, s) => sum + (Number(s.quantity) || 0), 0);
  const overLimit = remaining !== null && total > remaining + 1e-9;
  const invalid =
    !product || packInvalid(pack) || splits.some((s) => !(Number(s.quantity) > 0) || (s.expiry_date && !isIsoDate(s.expiry_date))) || overLimit;

  const add = useMutation({
    mutationFn: () =>
      api.post<Batch[]>(`/api/invoices/${invoice.id}/items/${item.id}/add-to-inventory`, {
        ...productPayload(product!, {
          category: category || null,
          default_unit: "pcs",
          package_size: formatPack(pack.size, pack.unit) || null,
          product_code: item.product_code,
        }),
        ...packPayload(pack),
        batches: splits.map((s) => ({
          quantity: Number(s.quantity),
          batch_number: s.batch_number.trim() || null,
          expiry_date: s.expiry_date || null,
          expiry_confirmed: s.expiry_confirmed,
        })),
      }),
    onSuccess: (batches) => {
      invalidate();
      client.invalidateQueries({ queryKey: ["batches"] });
      toast.success(`${batches.length} batch${batches.length === 1 ? "" : "es"} added to inventory`);
      onOpenChange(false);
    },
    onError: (error) => toast.error(error.message),
  });

  const updateSplit = (index: number, patch: Partial<Split>) => setSplits((all) => all.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Add to inventory"
        description={`${item.product_name} · ${remaining !== null ? `${formatQty(remaining)} remaining on this line` : "quantity not on invoice"}`}
        className="sm:max-w-2xl"
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!invalid) add.mutate();
          }}
        >
          {item.suggested_product_id && item.suggested_product_name && !product && (
            <Alert tone="info" title="Similar product already in your catalogue">
              <p>“{item.suggested_product_name}” looks like the same product.</p>
              <div className="mt-2 flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={async () => {
                    const existing = await api.get<Product>(`/api/products/${item.suggested_product_id}`);
                    setProduct({ kind: "existing", product: existing });
                    const known = packFromProduct({ kind: "existing", product: existing });
                    if (known && !pack.size) setPack(known);
                  }}
                >
                  Use “{item.suggested_product_name}”
                </Button>
                <Button type="button" size="sm" variant="outline" onClick={() => setProduct({ kind: "new", name: item.product_name })}>
                  Create a new product
                </Button>
              </div>
            </Alert>
          )}
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Product" htmlFor="p_name" className="sm:col-span-2" required>
              <ProductPicker key={product?.kind === "existing" ? product.product.id : "new"} inputId="p_name" value={product} onChange={setProduct} />
            </Field>
            <Field label="Size per pack" htmlFor="p_pack">
              <PackSizeInput id="p_pack" value={pack} onChange={setPack} />
            </Field>
            {product?.kind === "new" && (
              <Field label="Category (optional)" htmlFor="p_cat">
                <Input id="p_cat" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Dairy" />
              </Field>
            )}
          </div>

          <div>
            <p className="mb-1 text-sm font-medium">Batches</p>
            <p className="mb-2 text-xs text-muted-foreground">
              Split the delivery if packages carry different expiry dates. Leave the date empty if it isn't known yet — you can scan the package later.
            </p>
            <div className="space-y-3">
              {splits.map((split, index) => (
                <div key={index} className="grid gap-2 rounded-lg border bg-muted/30 p-3 sm:grid-cols-[1fr_1fr_1.3fr_auto] sm:items-end">
                  <Field label="Quantity" htmlFor={`s_qty_${index}`} required>
                    <Input id={`s_qty_${index}`} type="number" step="any" min="0" value={split.quantity} onChange={(e) => updateSplit(index, { quantity: e.target.value })} />
                  </Field>
                  <Field label="Batch no." htmlFor={`s_batch_${index}`}>
                    <Input id={`s_batch_${index}`} value={split.batch_number} onChange={(e) => updateSplit(index, { batch_number: e.target.value })} />
                  </Field>
                  <Field label="Expiry date" htmlFor={`s_exp_${index}`}>
                    <DateInput id={`s_exp_${index}`} value={split.expiry_date} onChange={(value) => updateSplit(index, { expiry_date: value })} />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Remove batch ${index + 1}`}
                    disabled={splits.length === 1}
                    onClick={() => setSplits((all) => all.filter((_, i) => i !== index))}
                  >
                    <Trash2 />
                  </Button>
                  {isIsoDate(split.expiry_date) && (
                    <label className="flex items-center gap-2 text-xs sm:col-span-4">
                      <input type="checkbox" checked={split.expiry_confirmed} onChange={(e) => updateSplit(index, { expiry_confirmed: e.target.checked })} />
                      I checked this date on the package
                    </label>
                  )}
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => setSplits((all) => [...all, { quantity: "", batch_number: "", expiry_date: "", expiry_confirmed: true }])}
            >
              <Plus /> Another batch
            </Button>
            {overLimit && <p className="mt-2 text-sm text-destructive">Total {formatQty(total)} is more than the {formatQty(remaining)} remaining on this line.</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={invalid} loading={add.isPending}>
              <PackagePlus /> Add {formatQty(total)} to inventory
            </Button>
            <Button type="button" variant="outline" asChild>
              <Link to={`/scan?invoice_item=${item.id}&quantity=${remaining ?? ""}`}>
                <Camera /> Scan package instead
              </Link>
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function InvoiceReviewPage() {
  const { invoiceId = "" } = useParams();
  const invalidate = useInvoiceInvalidation(invoiceId);
  const client = useQueryClient();
  const invoice = useQuery({
    queryKey: ["invoice", invoiceId],
    queryFn: () => api.get<Invoice>(`/api/invoices/${invoiceId}`),
    refetchInterval: (q) => (q.state.data && ["uploaded", "processing"].includes(q.state.data.status) ? 2000 : false),
  });
  const [header, setHeader] = useState<Header | null>(null);
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<InvoiceItem | null | "new">(null);
  const [adding, setAdding] = useState<InvoiceItem | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [dupConflict, setDupConflict] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const navigate = useNavigate();

  const status = invoice.data?.status;
  useEffect(() => {
    if (invoice.data && (header === null || status === "confirmed")) setHeader(toHeader(invoice.data));
    // Reset the header form when processing finishes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, invoice.data?.processed_at]);

  const confirm = useMutation({
    mutationFn: (allowDuplicate: boolean) =>
      api.post<Invoice>(`/api/invoices/${invoiceId}/confirm`, {
        ...headerPayload(header!),
        rejected_item_ids: [...rejected],
        allow_duplicate: allowDuplicate,
      }),
    onSuccess: (confirmed) => {
      invalidate();
      client.invalidateQueries({ queryKey: ["batches"] });
      setConfirmOpen(false);
      setDupConflict(null);
      const added = confirmed.items.filter((i) => i.status === "added_to_inventory").length;
      const left = confirmed.items.filter((i) => i.status === "accepted").length;
      toast.success(
        `Invoice confirmed. ${added} line${added === 1 ? "" : "s"} added to inventory` +
          (left ? `; ${left} need${left === 1 ? "s" : ""} details before adding.` : "."),
      );
    },
    onError: (error) => {
      setConfirmOpen(false);
      if (error instanceof ApiError && error.status === 409 && error.message.includes("allow_duplicate")) setDupConflict(error.message);
      else toast.error(error.message);
    },
  });
  const saveHeader = useMutation({
    mutationFn: () => api.patch<Invoice>(`/api/invoices/${invoiceId}`, headerPayload(header!)),
    onSuccess: () => {
      invalidate();
      toast.success("Invoice details saved");
    },
    onError: (error) => toast.error(error.message),
  });
  const reprocess = useMutation({
    mutationFn: () => api.post<Invoice>(`/api/invoices/${invoiceId}/reprocess`),
    onSuccess: () => {
      setHeader(null);
      invalidate();
      toast.success("Reading the invoice again…");
    },
    onError: (error) => toast.error(error.message),
  });
  const rejectInvoice = useMutation({
    mutationFn: () => api.post<Invoice>(`/api/invoices/${invoiceId}/reject`),
    onSuccess: () => {
      invalidate();
      setRejectOpen(false);
      toast.success("Invoice marked as rejected");
    },
    onError: (error) => toast.error(error.message),
  });

  if (invoice.isLoading) return <Spinner />;
  if (invoice.isError || !invoice.data) return <ErrorState error={invoice.error} onRetry={() => invoice.refetch()} />;
  const inv = invoice.data;
  const processing = inv.status === "uploaded" || inv.status === "processing";
  const reviewable = inv.status === "awaiting_review" || inv.status === "failed";
  const confirmed = inv.status === "confirmed";
  const fileUrl = `/api/invoices/${inv.id}/file`;
  const h = header ?? toHeader(inv);
  const headerDateInvalid = !!h.invoice_date && !isIsoDate(h.invoice_date);
  // Mirrors the backend: a missing quantity is not guessed, so confirming skips those lines (a missing unit means pcs).
  const incomplete = inv.items.filter((i) => !rejected.has(i.id) && i.status !== "rejected" && !(Number(i.quantity) > 0)).length;
  const setH = (key: keyof Header) => (e: React.ChangeEvent<HTMLInputElement>) => setHeader({ ...h, [key]: e.target.value });

  return (
    <>
      <Link to="/invoices" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Invoices
      </Link>
      <PageHeader
        title={inv.supplier_name || inv.original_filename}
        description={
          <span className="inline-flex flex-wrap items-center gap-2">
            <ProcessBadge status={inv.status} />
            {inv.extraction_method && (
              <span>{inv.extraction_method === "rules" ? "Read automatically" : "Read automatically with AI"}</span>
            )}
            {inv.ocr_engine && <span>· text from {inv.ocr_engine}</span>}
            {inv.confidence !== null && <span>· confidence {Math.round(inv.confidence * 100)}%</span>}
          </span>
        }
        actions={
          <>
            {reviewable && (
              <>
                <Button variant="outline" onClick={() => reprocess.mutate()} loading={reprocess.isPending}>
                  <RefreshCw /> Read again
                </Button>
                <Button variant="outline" onClick={() => setRejectOpen(true)}>
                  <X /> Not an invoice
                </Button>
              </>
            )}
            {inv.status !== "processing" && (
              <Button variant="outline" className="text-destructive hover:text-destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 /> Delete
              </Button>
            )}
          </>
        }
      />

      {processing && (
        <Card className="mb-4">
          <CardContent className="flex items-center gap-3 p-5 sm:p-5">
            <Loader2 className="size-6 animate-spin text-primary" aria-hidden />
            <div>
              <p className="font-medium">Reading your invoice…</p>
              <p className="text-sm text-muted-foreground">
                Text recognition and extraction run in the background. This usually takes a few seconds; you can leave this page and come back.
              </p>
            </div>
          </CardContent>
        </Card>
      )}
      {inv.status === "failed" && (
        <Alert tone="error" className="mb-4" title="We couldn't read this invoice automatically">
          <p>{inv.error_message}</p>
          <p className="mt-1">You can still enter the details and lines manually below, then confirm.</p>
        </Alert>
      )}
      {inv.status === "rejected" && <Alert className="mb-4">This upload was marked as not an invoice. It is kept for your records.</Alert>}
      {inv.warnings.length > 0 && !processing && (
        <Alert tone="warning" className="mb-4" title="Please check">
          <ul>
            {inv.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Alert>
      )}
      {dupConflict && (
        <Alert tone="warning" className="mb-4" title="Possible duplicate invoice">
          <p>{dupConflict.replace(" Confirm again with allow_duplicate to proceed.", "")}</p>
          <Button size="sm" className="mt-2" onClick={() => confirm.mutate(true)} loading={confirm.isPending}>
            It's a different delivery — confirm anyway
          </Button>
        </Alert>
      )}

      {!processing && (
        <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Invoice details</CardTitle>
                {reviewable && <CardDescription>Correct anything that was read wrongly. Empty fields were not found on the invoice.</CardDescription>}
              </CardHeader>
              <CardContent>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Supplier" htmlFor="h_supplier" className="sm:col-span-2">
                    <Input id="h_supplier" value={h.supplier_name} onChange={setH("supplier_name")} disabled={!reviewable} />
                  </Field>
                  <Field label="Invoice number" htmlFor="h_number">
                    <Input id="h_number" value={h.invoice_number} onChange={setH("invoice_number")} disabled={!reviewable} />
                  </Field>
                  <Field label="Invoice date" htmlFor="h_date">
                    <DateInput id="h_date" value={h.invoice_date} onChange={(value) => setHeader({ ...h, invoice_date: value })} disabled={!reviewable} />
                  </Field>
                  <Field label="Currency" htmlFor="h_currency">
                    <Input id="h_currency" maxLength={3} placeholder="INR" value={h.currency} onChange={setH("currency")} disabled={!reviewable} />
                  </Field>
                  <Field label="Subtotal" htmlFor="h_subtotal">
                    <Input id="h_subtotal" type="number" step="any" value={h.subtotal} onChange={setH("subtotal")} disabled={!reviewable} />
                  </Field>
                  <Field label="Tax" htmlFor="h_tax">
                    <Input id="h_tax" type="number" step="any" value={h.tax} onChange={setH("tax")} disabled={!reviewable} />
                  </Field>
                  <Field label="Total" htmlFor="h_total">
                    <Input id="h_total" type="number" step="any" value={h.total} onChange={setH("total")} disabled={!reviewable} />
                  </Field>
                </div>
                {reviewable && (
                  <Button variant="outline" size="sm" className="mt-3" onClick={() => saveHeader.mutate()} loading={saveHeader.isPending} disabled={headerDateInvalid}>
                    Save details
                  </Button>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <div>
                  <CardTitle>Line items ({inv.items.length})</CardTitle>
                  <CardDescription>
                    {!confirmed
                      ? "Edit lines, exclude lines that aren't stock, then confirm to add the rest to inventory."
                      : inv.items.some((i) => i.status === "accepted")
                        ? "Lines with a button still need adding: the invoice didn't give enough detail (e.g. no quantity) to add them automatically."
                        : "Accepted lines were added to inventory when the invoice was confirmed."}
                  </CardDescription>
                </div>
                {(reviewable || confirmed) && (
                  <Button size="sm" variant="outline" onClick={() => setEditing("new")}>
                    <Plus /> Add line
                  </Button>
                )}
              </CardHeader>
              <CardContent>
                {inv.items.length === 0 ? (
                  <p className="py-4 text-center text-sm text-muted-foreground">No lines yet. Add them manually.</p>
                ) : (
                  <ul className="space-y-2">
                    {inv.items.map((item) => {
                      const isRejected = item.status === "rejected" || rejected.has(item.id);
                      const lowConfidence = item.confidence !== null && item.confidence < 0.7;
                      return (
                        <li key={item.id} className={cn("rounded-lg border p-3", isRejected && "bg-muted/50 opacity-60", lowConfidence && !isRejected && "border-amber-300")}>
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div className="min-w-0">
                              <p className={cn("font-medium", isRejected && "line-through")}>
                                {item.line_number}. {item.product_name}
                                {item.edited && <span className="ml-2 text-xs font-normal text-muted-foreground">(edited)</span>}
                              </p>
                              <p className="text-sm text-muted-foreground tabular-nums">
                                {formatQty(item.quantity)} × {formatMoney(item.unit_price, inv.currency)} = {formatMoney(item.line_total, inv.currency)}
                                {item.pack_size ? ` · ${formatPack(item.pack_size, item.pack_unit)} each` : ""}
                                {item.product_code ? ` · ${item.product_code}` : ""}
                              </p>
                            </div>
                            <div className="flex items-center gap-1">
                              {confirmed ? (
                                item.status === "added_to_inventory" ? (
                                  <ProcessBadge status="added_to_inventory" />
                                ) : item.status === "rejected" ? (
                                  <ProcessBadge status="rejected" />
                                ) : (
                                  <Button size="sm" onClick={() => setAdding(item)}>
                                    <PackagePlus /> Add to inventory
                                  </Button>
                                )
                              ) : reviewable ? (
                                <>
                                  <Button size="icon" variant="ghost" aria-label={`Edit line ${item.line_number}`} onClick={() => setEditing(item)}>
                                    <Pencil />
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() =>
                                      setRejected((set) => {
                                        const next = new Set(set);
                                        if (next.has(item.id)) next.delete(item.id);
                                        else next.add(item.id);
                                        return next;
                                      })
                                    }
                                  >
                                    {isRejected ? <><Undo2 /> Keep</> : <><X /> Exclude</>}
                                  </Button>
                                </>
                              ) : null}
                            </div>
                          </div>
                          {Number(item.quantity_added) > 0 && item.status !== "added_to_inventory" && (
                            <p className="mt-1 text-xs text-emerald-700">{formatQty(item.quantity_added)} already in inventory</p>
                          )}
                          {item.warnings.length > 0 && !isRejected && (
                            <ul className="mt-2 space-y-0.5 text-xs text-amber-800">
                              {item.warnings.map((w, i) => (
                                <li key={i}>⚠ {w}</li>
                              ))}
                            </ul>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {reviewable && (
                  <Button className="mt-4 w-full sm:w-auto" onClick={() => setConfirmOpen(true)} disabled={inv.items.length === 0 || headerDateInvalid}>
                    <Check /> Confirm invoice
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="space-y-4">
            <Card className="overflow-hidden">
              <CardHeader>
                <CardTitle>Original document</CardTitle>
                <CardDescription>
                  {inv.original_filename}
                  {inv.page_count ? ` · ${inv.page_count} page${inv.page_count === 1 ? "" : "s"}` : ""}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {inv.content_type.startsWith("image/") ? (
                  <a href={fileUrl} target="_blank" rel="noreferrer">
                    <img src={fileUrl} alt="Uploaded invoice" className="w-full rounded-md border" />
                  </a>
                ) : (
                  <>
                    <iframe title="Invoice PDF" src={fileUrl} className="hidden h-[480px] w-full rounded-md border sm:block" />
                    <Button variant="outline" asChild className="mt-2 w-full">
                      <a href={fileUrl} target="_blank" rel="noreferrer">
                        Open PDF
                      </a>
                    </Button>
                  </>
                )}
              </CardContent>
            </Card>
            {inv.raw_text && (
              <Card>
                <details>
                  <summary className="cursor-pointer p-4 text-sm font-medium">Extracted text (for checking)</summary>
                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap border-t p-4 text-xs text-muted-foreground">{inv.raw_text}</pre>
                </details>
              </Card>
            )}
            {confirmed && <p className="text-sm text-muted-foreground">Confirmed on {formatDate(inv.confirmed_at)}.</p>}
          </div>
        </div>
      )}

      {editing && (
        <ItemDialog invoiceId={inv.id} item={editing === "new" ? null : editing} open onOpenChange={(o) => !o && setEditing(null)} />
      )}
      {adding && <AddToInventoryDialog invoice={inv} item={adding} open onOpenChange={(o) => !o && setAdding(null)} />}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Confirm this invoice?"
        description={
          `${inv.items.length - rejected.size - incomplete} line(s) will be added to inventory${rejected.size ? `, ${rejected.size} excluded` : ""}.` +
          (incomplete
            ? ` ${incomplete} line(s) have no quantity and won't be added until you fill it in; cancel and use the pencil button to do that first.`
            : "") +
          " Expiry dates aren't on invoices, so add them afterwards by scanning the packages or editing each batch."
        }
        confirmLabel="Confirm invoice"
        loading={confirm.isPending}
        onConfirm={() => confirm.mutate(false)}
      />
      <DeleteUploadDialog
        kind="invoice"
        id={inv.id}
        name={`${inv.supplier_name || inv.original_filename}${inv.invoice_number ? ` #${inv.invoice_number}` : ""}`}
        confirmed={confirmed}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onDeleted={() => navigate("/invoices")}
      />
      <ConfirmDialog
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        title="Mark as not an invoice?"
        description="The upload is kept for your records but nothing from it will be added to inventory."
        confirmLabel="Reject upload"
        destructive
        loading={rejectInvoice.isPending}
        onConfirm={() => rejectInvoice.mutate()}
      />
    </>
  );
}
