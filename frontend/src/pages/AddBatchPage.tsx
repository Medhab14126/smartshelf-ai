import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { PageHeader } from "@/components/PageHeader";
import { PackSizeInput, emptyPack, packInvalid, packPayload, type PackSize } from "@/components/PackSizeInput";
import { ProductPicker, packFromProduct, productPayload, type ProductChoice } from "@/components/ProductPicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DateInput } from "@/components/ui/date-input";
import { Alert } from "@/components/ui/feedback";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Batch } from "@/lib/types";
import { isIsoDate, todayIso } from "@/lib/utils";

const DATE_FORMAT = "Enter the date as dd/mm/yyyy";
const optionalDate = z
  .string()
  .optional()
  .refine((v) => !v || isIsoDate(v), DATE_FORMAT)
  .transform((v) => v || null);

export const batchSchema = z
  .object({
    brand: z.string().optional(),
    category: z.string().optional(),
    quantity: z.coerce.number({ invalid_type_error: "Enter a quantity" }).positive("Quantity must be more than zero"),
    batch_number: z.string().optional(),
    supplier_name: z.string().optional(),
    manufacturing_date: optionalDate,
    expiry_date: optionalDate,
    expiry_type: z.enum(["expiry", "best_before"]),
    expiry_confirmed: z.boolean(),
    purchase_price: z
      .string()
      .optional()
      .transform((v) => (v ? Number(v) : null))
      .refine((v) => v === null || (Number.isFinite(v) && v >= 0), "Enter a valid price"),
    received_date: z.string().refine(isIsoDate, DATE_FORMAT),
    notes: z.string().optional(),
  })
  .refine((v) => !v.manufacturing_date || !v.expiry_date || v.expiry_date >= v.manufacturing_date, {
    message: "Expiry date cannot be before the manufacturing date",
    path: ["expiry_date"],
  });

type FormInput = z.input<typeof batchSchema>;
type FormValues = z.output<typeof batchSchema>;

export function AddBatchPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [product, setProduct] = useState<ProductChoice | null>(null);
  const [productError, setProductError] = useState<string | null>(null);
  const [pack, setPack] = useState<PackSize>(emptyPack);
  const form = useForm<FormInput, unknown, FormValues>({
    resolver: zodResolver(batchSchema),
    defaultValues: { expiry_type: "expiry", expiry_confirmed: true, received_date: todayIso() },
  });
  const expiryDate = form.watch("expiry_date");

  const create = useMutation({
    mutationFn: (values: FormValues) => {
      const { brand, category, ...rest } = values;
      return api.post<Batch>("/api/inventory/batches", {
        ...productPayload(product!, { brand: brand || null, category: category || null, default_unit: "pcs" }),
        ...rest,
        ...packPayload(pack),
        batch_number: rest.batch_number || null,
        supplier_name: rest.supplier_name || null,
        notes: rest.notes || null,
      });
    },
    onSuccess: (batch) => {
      client.invalidateQueries({ queryKey: ["batches"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success(`${batch.product.name} added to inventory`);
      navigate(`/inventory/${batch.id}`);
    },
    onError: (error) => toast.error(error.message),
  });

  const onSubmit = form.handleSubmit((values) => {
    if (!product) {
      setProductError("Choose or type a product name");
      return;
    }
    if (packInvalid(pack)) return;
    create.mutate(values);
  });
  const { errors } = form.formState;

  return (
    <>
      <PageHeader title="Add stock manually" description="Record a delivery without an invoice. Each entry creates a new batch." />
      <Card className="max-w-3xl">
        <CardContent className="pt-5 sm:pt-5">
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
            <Field label="Product" htmlFor="product" error={productError ?? undefined} className="sm:col-span-2" required>
              <ProductPicker
                inputId="product"
                value={product}
                invalid={!!productError}
                onChange={(choice) => {
                  setProduct(choice);
                  setProductError(null);
                  // Pre-fill the pack size printed on the product ("Milk 500ml"); the user can change it.
                  const found = packFromProduct(choice);
                  if (found && !pack.size) setPack(found);
                }}
              />
            </Field>
            {product?.kind !== "existing" && (
              <>
                <Field label="Brand (optional)" htmlFor="brand">
                  <Input id="brand" {...form.register("brand")} />
                </Field>
                <Field label="Category (optional)" htmlFor="category">
                  <Input id="category" placeholder="Dairy, Bakery…" {...form.register("category")} />
                </Field>
              </>
            )}
            <Field label="Quantity (packs / pieces)" htmlFor="quantity" error={errors.quantity?.message} required>
              <Input id="quantity" type="number" inputMode="decimal" step="any" min="0" aria-invalid={!!errors.quantity} {...form.register("quantity")} />
            </Field>
            <Field label="Size per pack (optional)" htmlFor="pack_size" hint="What one pack holds, e.g. 500 ml or 1 kg">
              <PackSizeInput id="pack_size" value={pack} onChange={setPack} />
            </Field>
            <Field label="Batch / lot number (optional)" htmlFor="batch_number">
              <Input id="batch_number" {...form.register("batch_number")} />
            </Field>
            <Field label="Supplier (optional)" htmlFor="supplier_name">
              <Input id="supplier_name" {...form.register("supplier_name")} />
            </Field>
            <Field label="Manufacturing date (optional)" htmlFor="manufacturing_date" error={errors.manufacturing_date?.message}>
              <Controller
                control={form.control}
                name="manufacturing_date"
                render={({ field }) => <DateInput id="manufacturing_date" value={field.value ?? ""} onChange={field.onChange} aria-invalid={!!errors.manufacturing_date} />}
              />
            </Field>
            <Field label="Expiry / best-before date" htmlFor="expiry_date" error={errors.expiry_date?.message} hint="Leave empty if unknown; you can scan the package later.">
              <Controller
                control={form.control}
                name="expiry_date"
                render={({ field }) => <DateInput id="expiry_date" value={field.value ?? ""} onChange={field.onChange} aria-invalid={!!errors.expiry_date} />}
              />
            </Field>
            {expiryDate && (
              <>
                <Field label="Date type" htmlFor="expiry_type">
                  <Select id="expiry_type" {...form.register("expiry_type")}>
                    <option value="expiry">Expiry / use-by</option>
                    <option value="best_before">Best before</option>
                  </Select>
                </Field>
                <label className="flex items-start gap-2 self-end rounded-md border bg-muted/40 p-3 text-sm">
                  <input type="checkbox" className="mt-0.5 size-4 accent-[hsl(var(--primary))]" {...form.register("expiry_confirmed")} />
                  <span>I checked this date on the package. Reminders are only sent for confirmed dates.</span>
                </label>
              </>
            )}
            <Field label="Purchase price per unit (optional)" htmlFor="purchase_price" error={errors.purchase_price?.message}>
              <Input id="purchase_price" type="number" inputMode="decimal" step="any" min="0" {...form.register("purchase_price")} />
            </Field>
            <Field label="Date received" htmlFor="received_date" error={errors.received_date?.message} required>
              <Controller
                control={form.control}
                name="received_date"
                render={({ field }) => <DateInput id="received_date" value={field.value} onChange={field.onChange} aria-invalid={!!errors.received_date} />}
              />
            </Field>
            <Field label="Notes (optional)" htmlFor="notes" className="sm:col-span-2">
              <Textarea id="notes" {...form.register("notes")} />
            </Field>
            {create.isError && (
              <Alert tone="error" className="sm:col-span-2">
                {create.error.message}
              </Alert>
            )}
            <div className="flex gap-2 sm:col-span-2">
              <Button type="submit" loading={create.isPending}>
                Save batch
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate(-1)}>
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
