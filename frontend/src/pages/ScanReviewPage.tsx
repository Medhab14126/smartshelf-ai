import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "@/components/PageHeader";
import { ProductPicker, packFromProduct, productPayload, type ProductChoice } from "@/components/ProductPicker";
import { DeleteUploadDialog } from "@/components/DeleteUploadDialog";
import { PackSizeInput, emptyPack, packInvalid, packPayload, type PackSize } from "@/components/PackSizeInput";
import { ProcessBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DateInput } from "@/components/ui/date-input";
import { Alert, ErrorState, Spinner } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Batch, ExtractedField, FieldStatus, Product, Scan } from "@/lib/types";
import { formatPack, parsePackSize } from "@/lib/units";
import { formatDate, formatDateLong, isIsoDate } from "@/lib/utils";

const FIELD_STATUS: Record<FieldStatus, { label: string; tone: "green" | "amber" | "red" | "neutral" }> = {
  detected: { label: "Read clearly", tone: "green" },
  uncertain: { label: "Please check", tone: "amber" },
  ambiguous: { label: "Ambiguous", tone: "red" },
  not_found: { label: "Not found", tone: "neutral" },
};

function FieldStatusBadge({ field }: { field?: ExtractedField }) {
  const config = FIELD_STATUS[field?.status ?? "not_found"];
  return <Badge tone={config.tone}>{config.label}</Badge>;
}

function FieldNotes({ field }: { field?: ExtractedField }) {
  if (!field || (!field.source && !field.reasons.length)) return null;
  return (
    <div className="text-xs text-muted-foreground">
      {field.source && (
        <p>
          Read from: <span className="font-mono">“{field.source}”</span>
        </p>
      )}
      {field.reasons.map((r, i) => (
        <p key={i}>• {r}</p>
      ))}
    </div>
  );
}

/** Date input with one-tap choices for every interpretation the parser found. */
function DateChoice({
  id,
  label,
  field,
  value,
  onChange,
}: {
  id: string;
  label: string;
  field?: ExtractedField;
  value: string;
  onChange: (v: string) => void;
}) {
  const options = field?.options ?? [];
  return (
    <div className="space-y-1.5 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <FieldStatusBadge field={field} />
      </div>
      {options.length > 1 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={`Possible ${label.toLowerCase()} readings`}>
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => onChange(opt)}
              aria-pressed={value === opt}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${value === opt ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-accent"}`}
            >
              {formatDateLong(opt)}
            </button>
          ))}
        </div>
      )}
      <DateInput id={id} value={value} onChange={onChange} />
      <FieldNotes field={field} />
    </div>
  );
}

export function ScanReviewPage() {
  const { scanId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const scan = useQuery({
    queryKey: ["scan", scanId],
    queryFn: () => api.get<Scan>(`/api/scans/${scanId}`),
    refetchInterval: (q) => (q.state.data && ["uploaded", "processing"].includes(q.state.data.status) ? 2000 : false),
  });
  const linkedBatch = useQuery({
    queryKey: ["batch", scan.data?.batch_id],
    queryFn: () => api.get<Batch>(`/api/inventory/batches/${scan.data!.batch_id}`),
    enabled: !!scan.data?.batch_id,
  });

  const [initialised, setInitialised] = useState(false);
  const [product, setProduct] = useState<ProductChoice | null>(null);
  const [form, setForm] = useState({
    brand: "",
    quantity: "",
    supplier_name: "",
    batch_number: "",
    manufacturing_date: "",
    expiry_date: "",
    expiry_type: "expiry" as "expiry" | "best_before",
  });
  const [pack, setPack] = useState<PackSize>(emptyPack);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [checked, setChecked] = useState(false);

  // Prefill the form once extraction is available. Values remain editable suggestions.
  useEffect(() => {
    const data = scan.data;
    if (!data || initialised || ["uploaded", "processing"].includes(data.status)) return;
    const f = data.extraction?.fields ?? {};
    const expiry = f.expiry_date?.value ? f.expiry_date : f.best_before_date;
    setForm((prev) => ({
      ...prev,
      brand: f.brand?.value ?? "",
      quantity: data.quantity_hint ?? "",
      batch_number: f.batch_number?.value ?? "",
      manufacturing_date: f.manufacturing_date?.value ?? f.packing_date?.value ?? "",
      expiry_date: expiry?.value ?? "",
      expiry_type: f.expiry_date?.value ? "expiry" : f.best_before_date?.value ? "best_before" : "expiry",
    }));
    // "Net Qty: 500 ml" on the label fills the pack size; it stays editable.
    const labelPack = parsePackSize(f.package_size?.value);
    if (labelPack) setPack(labelPack);
    if (data.matched_product_id) {
      api
        .get<Product>(`/api/products/${data.matched_product_id}`)
        .then((p) => {
          setProduct({ kind: "existing", product: p });
          const known = packFromProduct({ kind: "existing", product: p });
          if (known) setPack((prev) => (prev.size ? prev : known));
        })
        .catch(() => undefined);
    } else if (f.product_name?.value) {
      setProduct({ kind: "new", name: f.product_name.value });
    }
    setInitialised(true);
  }, [scan.data, initialised]);

  const save = useMutation({
    mutationFn: (confirmExpiry: boolean) => {
      const base = {
        batch_number: form.batch_number.trim() || null,
        manufacturing_date: form.manufacturing_date || null,
        expiry_date: form.expiry_date || null,
        expiry_type: form.expiry_type,
        confirm_expiry: confirmExpiry,
      };
      const body = scan.data!.batch_id
        ? { ...base, batch_id: scan.data!.batch_id }
        : {
            ...base,
            ...productPayload(product!, {
              brand: form.brand || null,
              barcode: scan.data!.barcode,
              default_unit: "pcs",
              package_size: formatPack(pack.size, pack.unit) || null,
            }),
            quantity: Number(form.quantity),
            ...packPayload(pack),
            supplier_name: form.supplier_name.trim() || null,
            invoice_item_id: scan.data!.invoice_item_id,
          };
      return api.post<Scan>(`/api/scans/${scanId}/confirm`, body);
    },
    onSuccess: (saved, confirmExpiry) => {
      client.invalidateQueries({ queryKey: ["batches"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      client.invalidateQueries({ queryKey: ["scans"] });
      client.invalidateQueries({ queryKey: ["batch", saved.batch_id] });
      client.invalidateQueries({ queryKey: ["invoice"] });
      toast.success(confirmExpiry ? "Saved with a confirmed expiry date" : "Saved — expiry date still needs confirmation");
      navigate(`/inventory/${saved.batch_id}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const reprocess = useMutation({
    mutationFn: () => api.post<Scan>(`/api/scans/${scanId}/reprocess`),
    onSuccess: () => {
      setInitialised(false);
      client.invalidateQueries({ queryKey: ["scan", scanId] });
    },
    onError: (error) => toast.error(error.message),
  });

  if (scan.isLoading) return <Spinner />;
  if (scan.isError || !scan.data) return <ErrorState error={scan.error} onRetry={() => scan.refetch()} />;
  const s = scan.data;
  const fields = s.extraction?.fields ?? {};
  const processing = s.status === "uploaded" || s.status === "processing";
  const editable = s.status === "awaiting_confirmation" || s.status === "failed";
  const updatingBatch = !!s.batch_id;
  const newBatchInvalid = !updatingBatch && (!product || !(Number(form.quantity) > 0) || packInvalid(pack));
  const dateOrderInvalid = !!form.manufacturing_date && !!form.expiry_date && form.expiry_date < form.manufacturing_date;
  // Half-typed dates block saving rather than being dropped.
  const datesInvalid = dateOrderInvalid || [form.manufacturing_date, form.expiry_date].some((d) => d && !isIsoDate(d));
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const unlabelled = (s.extraction?.date_candidates ?? []).filter((c) => c.field === "unknown" && c.parsed.value);

  return (
    <>
      <Link to="/scan" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Scan Product
      </Link>
      <PageHeader
        title="Review scanned label"
        description={<ProcessBadge status={s.status} />}
        actions={
          <>
            {editable && (
              <Button variant="outline" onClick={() => reprocess.mutate()} loading={reprocess.isPending}>
                <RefreshCw /> Read again
              </Button>
            )}
            {s.status !== "processing" && (
              <Button variant="outline" className="text-destructive hover:text-destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 /> Delete
              </Button>
            )}
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Card className="overflow-hidden">
            <img src={`/api/scans/${s.id}/image`} alt="Scanned packaging" className="max-h-[420px] w-full bg-muted object-contain" />
          </Card>
          {s.raw_text && (
            <Card>
              <CardHeader>
                <CardTitle>Text read from the photo</CardTitle>
                <CardDescription>Compare the dates below with this text and the photo.</CardDescription>
              </CardHeader>
              <CardContent>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-xs">{s.raw_text}</pre>
                {s.barcode && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Barcode {s.barcode} ({s.barcode_format}) — identifies the product only.
                  </p>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          {processing && (
            <Card>
              <CardContent className="flex items-center gap-3 p-5 sm:p-5">
                <Loader2 className="size-6 animate-spin text-primary" />
                <div>
                  <p className="font-medium">Reading the label…</p>
                  <p className="text-sm text-muted-foreground">Recognising text, dates and barcode. You can come back to this later.</p>
                </div>
              </CardContent>
            </Card>
          )}
          {s.status === "failed" && (
            <Alert tone="error" title="The label could not be read automatically">
              {s.error_message} You can still enter the details manually.
            </Alert>
          )}
          {s.status === "confirmed" && (
            <Alert title="Already saved">
              This scan was confirmed{s.confirmed_at ? ` on ${formatDate(s.confirmed_at)}` : ""}.{" "}
              {s.batch_id && (
                <Link className="font-medium underline" to={`/inventory/${s.batch_id}`}>
                  View the batch
                </Link>
              )}
            </Alert>
          )}
          {s.warnings.length > 0 && editable && (
            <Alert tone="warning" title="Please check">
              <ul>
                {s.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </Alert>
          )}

          {editable && (
            <form
              onSubmit={(e) => e.preventDefault()}
              className="space-y-4"
              aria-label="Confirm scanned details"
            >
              <Card>
                <CardHeader>
                  <CardTitle>{updatingBatch ? "Batch" : "Product and quantity"}</CardTitle>
                  {updatingBatch && linkedBatch.data && (
                    <CardDescription>
                      Updating {linkedBatch.data.product.name} · {linkedBatch.data.current_quantity} {linkedBatch.data.unit} in stock
                    </CardDescription>
                  )}
                </CardHeader>
                {!updatingBatch && (
                  <CardContent className="grid gap-3 sm:grid-cols-2">
                    <Field
                      label={
                        <span className="flex items-center gap-2">
                          Product name <FieldStatusBadge field={fields.product_name} />
                        </span>
                      }
                      htmlFor="sc_product"
                      className="sm:col-span-2"
                      required
                      hint={s.matched_product_name ? `Matched to “${s.matched_product_name}” in your catalogue` : undefined}
                    >
                      <ProductPicker key={product?.kind === "existing" ? product.product.id : product ? "named" : "empty"} inputId="sc_product" value={product} onChange={setProduct} />
                    </Field>
                    {product?.kind !== "existing" && (
                      <Field label="Brand" htmlFor="sc_brand">
                        <Input id="sc_brand" value={form.brand} onChange={set("brand")} />
                      </Field>
                    )}
                    <Field label="Supplier (optional)" htmlFor="sc_supplier">
                      <Input id="sc_supplier" value={form.supplier_name} onChange={set("supplier_name")} />
                    </Field>
                    <Field label="Quantity (packs / pieces)" htmlFor="sc_qty" required>
                      <Input id="sc_qty" type="number" step="any" min="0" value={form.quantity} onChange={set("quantity")} />
                    </Field>
                    <Field label="Size per pack" htmlFor="sc_pack">
                      <PackSizeInput id="sc_pack" value={pack} onChange={setPack} />
                    </Field>
                  </CardContent>
                )}
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Dates and batch number</CardTitle>
                  <CardDescription>Manufacturing, packing, expiry and best-before dates are different things — check each one against the package.</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 sm:grid-cols-2">
                  <DateChoice
                    id="sc_mfg"
                    label={fields.manufacturing_date?.value || !fields.packing_date?.value ? "Manufacturing date" : "Packing date (used as manufacturing)"}
                    field={fields.manufacturing_date?.value || !fields.packing_date?.value ? fields.manufacturing_date : fields.packing_date}
                    value={form.manufacturing_date}
                    onChange={(v) => setForm((f) => ({ ...f, manufacturing_date: v }))}
                  />
                  <DateChoice
                    id="sc_exp"
                    label={form.expiry_type === "best_before" ? "Best-before date" : "Expiry / use-by date"}
                    field={form.expiry_type === "best_before" ? fields.best_before_date : fields.expiry_date}
                    value={form.expiry_date}
                    onChange={(v) => {
                      setForm((f) => ({ ...f, expiry_date: v }));
                      setChecked(false);
                    }}
                  />
                  <Field label="Date type" htmlFor="sc_type">
                    <Select id="sc_type" value={form.expiry_type} onChange={set("expiry_type")}>
                      <option value="expiry">Expiry / use-by</option>
                      <option value="best_before">Best before</option>
                    </Select>
                  </Field>
                  <Field
                    label={
                      <span className="flex items-center gap-2">
                        Batch / lot number <FieldStatusBadge field={fields.batch_number} />
                      </span>
                    }
                    htmlFor="sc_batch"
                  >
                    <Input id="sc_batch" value={form.batch_number} onChange={set("batch_number")} />
                  </Field>
                  {unlabelled.length > 0 && (
                    <div className="rounded-lg border border-dashed p-3 text-sm sm:col-span-2">
                      <p className="font-medium">Other dates found without a clear label</p>
                      <p className="mb-2 text-xs text-muted-foreground">Only use one if you can see on the package what it means.</p>
                      <div className="flex flex-wrap gap-1.5">
                        {unlabelled.map((c, i) => (
                          <button
                            key={i}
                            type="button"
                            className="rounded-full border px-3 py-1 text-xs hover:bg-accent"
                            onClick={() => setForm((f) => ({ ...f, expiry_date: c.parsed.value! }))}
                            title={c.line}
                          >
                            Use {formatDateLong(c.parsed.value!)} as expiry (“{c.raw}”)
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {dateOrderInvalid && <p className="text-sm text-destructive sm:col-span-2">The expiry date cannot be before the manufacturing date.</p>}
                </CardContent>
              </Card>

              <Card>
                <CardContent className="space-y-3 p-4 sm:p-5">
                  <label className="flex items-start gap-3 rounded-md bg-muted/50 p-3 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4"
                      checked={checked}
                      disabled={!isIsoDate(form.expiry_date)}
                      onChange={(e) => setChecked(e.target.checked)}
                    />
                    <span>
                      I compared the {form.expiry_type === "best_before" ? "best-before" : "expiry"} date{" "}
                      <strong>{isIsoDate(form.expiry_date) ? formatDateLong(form.expiry_date) : "(none entered)"}</strong> with the package and it is correct.
                    </span>
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      disabled={!checked || !form.expiry_date || newBatchInvalid || datesInvalid}
                      loading={save.isPending && save.variables === true}
                      onClick={() => save.mutate(true)}
                    >
                      <CheckCircle2 /> Save and confirm date
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={newBatchInvalid || datesInvalid}
                      loading={save.isPending && save.variables === false}
                      onClick={() => save.mutate(false)}
                    >
                      Save without confirming
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">Unconfirmed dates are kept but never trigger expiry reminders.</p>
                </CardContent>
              </Card>
            </form>
          )}
        </div>
      </div>
      <DeleteUploadDialog
        kind="scan"
        id={s.id}
        name={s.extraction?.fields.product_name?.value ?? s.original_filename}
        confirmed={s.status === "confirmed"}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onDeleted={() => navigate("/scan")}
      />
    </>
  );
}
