import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { PackSize } from "@/components/PackSizeInput";
import { parsePackSize } from "@/lib/units";

import { Input } from "@/components/ui/input";
import { api, query } from "@/lib/api";
import type { Page, Product } from "@/lib/types";
import { cn } from "@/lib/utils";

export type ProductChoice = { kind: "existing"; product: Product } | { kind: "new"; name: string };

/**
 * Type a product name: pick an existing catalogue entry or keep the typed
 * name to create a new product.
 */
export function ProductPicker({
  value,
  onChange,
  inputId,
  invalid,
}: {
  value: ProductChoice | null;
  onChange: (choice: ProductChoice | null) => void;
  inputId: string;
  invalid?: boolean;
}) {
  const listId = useId();
  const [text, setText] = useState(value ? (value.kind === "existing" ? value.product.name : value.name) : "");
  const [open, setOpen] = useState(false);
  const search = useQuery({
    queryKey: ["products", "search", text],
    queryFn: () => api.get<Page<Product>>(`/api/products${query({ q: text, page_size: 8 })}`),
    enabled: open && text.trim().length > 0,
  });
  const matches = search.data?.items ?? [];
  const exact = matches.find((p) => p.name.toLowerCase() === text.trim().toLowerCase());

  const update = (next: string) => {
    setText(next);
    setOpen(true);
    onChange(next.trim() ? { kind: "new", name: next.trim() } : null);
  };

  return (
    <div className="relative">
      <Input
        id={inputId}
        value={text}
        role="combobox"
        aria-expanded={open && matches.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={invalid}
        autoComplete="off"
        placeholder="e.g. Milk 500ml"
        onChange={(e) => update(e.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && matches.length > 0 && (
        <ul id={listId} role="listbox" className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border bg-card py-1 shadow-lg">
          {matches.map((product) => (
            <li
              key={product.id}
              role="option"
              aria-selected={value?.kind === "existing" && value.product.id === product.id}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-accent"
              onMouseDown={(e) => {
                e.preventDefault();
                setText(product.name);
                setOpen(false);
                onChange({ kind: "existing", product });
              }}
            >
              <span className="font-medium">{product.name}</span>
              {product.brand && <span className="text-muted-foreground"> · {product.brand}</span>}
              <span className="block text-xs text-muted-foreground">
                {product.active_batches} active batch{product.active_batches === 1 ? "" : "es"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {value && (
        <p className={cn("mt-1 text-xs", value.kind === "existing" ? "text-emerald-700" : "text-muted-foreground")}>
          {value.kind === "existing"
            ? "Existing product selected"
            : exact
              ? "A product with this name exists — select it from the list to reuse it"
              : "A new product will be created"}
        </p>
      )}
    </div>
  );
}

/** Pack size printed in the product's catalogue entry or name ("Milk 500ml"), used to pre-fill forms. */
export function packFromProduct(choice: ProductChoice | null): PackSize | null {
  if (!choice) return null;
  return choice.kind === "existing" ? (parsePackSize(choice.product.package_size) ?? parsePackSize(choice.product.name)) : parsePackSize(choice.name);
}

export function productPayload(choice: ProductChoice, extra: Record<string, unknown> = {}) {
  return choice.kind === "existing" ? { product_id: choice.product.id } : { new_product: { name: choice.name, ...extra } };
}
