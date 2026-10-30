import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FileUp, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { DeleteUploadDialog } from "@/components/DeleteUploadDialog";
import { PageHeader } from "@/components/PageHeader";
import { ProcessBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, Spinner } from "@/components/ui/feedback";
import { api, query } from "@/lib/api";
import type { InvoiceSummary, Page } from "@/lib/types";
import { cn, formatDate, formatDateTime, formatMoney } from "@/lib/utils";

const ACCEPTED = ["application/pdf", "image/jpeg", "image/png"];
const MAX_MB = 15;

export function InvoiceUploader() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return api.post<{ invoice: InvoiceSummary; duplicate: boolean }>("/api/invoices/upload", body);
    },
    onSuccess: ({ invoice, duplicate }) => {
      client.invalidateQueries({ queryKey: ["invoices"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      if (duplicate) toast.info("This exact file was uploaded before — opening the existing invoice");
      else toast.success("Invoice uploaded. Reading it now…");
      navigate(`/invoices/${invoice.id}`);
    },
    onError: (error) => toast.error(error.message),
  });

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    if (!ACCEPTED.includes(file.type)) {
      toast.error("Please choose a PDF, JPEG or PNG file");
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      toast.error(`The file is larger than ${MAX_MB} MB`);
      return;
    }
    upload.mutate(file);
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        handleFile(e.dataTransfer.files[0]);
      }}
      className={cn(
        "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed bg-card px-6 py-10 text-center transition-colors",
        dragging && "border-primary bg-accent",
      )}
    >
      <div className="rounded-full bg-accent p-3 text-accent-foreground">
        <UploadCloud className="size-7" aria-hidden />
      </div>
      <div>
        <p className="font-medium">Drop a supplier invoice here</p>
        <p className="text-sm text-muted-foreground">PDF, JPEG or PNG up to {MAX_MB} MB. Photos from your phone work too.</p>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(",")}
        className="sr-only"
        aria-label="Choose invoice file"
        onChange={(e) => {
          handleFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <Button onClick={() => inputRef.current?.click()} loading={upload.isPending}>
        <FileUp /> {upload.isPending ? "Uploading…" : "Choose file"}
      </Button>
    </div>
  );
}

const invoiceName = (inv: InvoiceSummary) => `${inv.supplier_name || inv.original_filename}${inv.invoice_number ? ` #${inv.invoice_number}` : ""}`;

export function InvoicesPage() {
  const [page, setPage] = useState(1);
  const [deleting, setDeleting] = useState<InvoiceSummary | null>(null);
  const invoices = useQuery({
    queryKey: ["invoices", page],
    queryFn: () => api.get<Page<InvoiceSummary>>(`/api/invoices${query({ page, page_size: 20 })}`),
    // Keep the list fresh while something is still being processed.
    refetchInterval: (q) => (q.state.data?.items.some((i) => i.status === "uploaded" || i.status === "processing") ? 3000 : false),
  });
  const totalPages = invoices.data ? Math.max(1, Math.ceil(invoices.data.total / 20)) : 1;

  return (
    <>
      <PageHeader
        title="Upload Invoice"
        description="SmartShelf reads the supplier, items, quantities and prices. You review everything before it goes into inventory."
      />
      <InvoiceUploader />
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Uploaded invoices</CardTitle>
        </CardHeader>
        <CardContent>
          {invoices.isLoading ? (
            <Spinner />
          ) : invoices.isError ? (
            <ErrorState error={invoices.error} onRetry={() => invoices.refetch()} />
          ) : !invoices.data?.items.length ? (
            <EmptyState icon={<FileText />} title="No invoices yet" description="Uploaded invoices appear here so you can come back to review them later." />
          ) : (
            <>
              <ul className="divide-y">
                {invoices.data.items.map((inv) => (
                  <li key={inv.id} className="flex items-center gap-1">
                    <Link to={`/invoices/${inv.id}`} className="flex min-w-0 flex-1 items-center justify-between gap-3 py-3 hover:bg-accent/40">
                      <div className="min-w-0">
                        <p className="truncate font-medium">{inv.supplier_name || inv.original_filename}</p>
                        <p className="text-xs text-muted-foreground">
                          {inv.invoice_number ? `#${inv.invoice_number} · ` : ""}
                          {inv.invoice_date ? formatDate(inv.invoice_date) : `Uploaded ${formatDateTime(inv.created_at)}`}
                          {inv.item_count ? ` · ${inv.item_count} items` : ""}
                          {inv.total ? ` · ${formatMoney(inv.total, inv.currency)}` : ""}
                        </p>
                      </div>
                      <ProcessBadge status={inv.status} />
                    </Link>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Delete invoice ${inv.invoice_number ?? inv.original_filename}`}
                      disabled={inv.status === "processing"}
                      onClick={() => setDeleting(inv)}
                    >
                      <Trash2 />
                    </Button>
                  </li>
                ))}
              </ul>
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
        </CardContent>
      </Card>
      {deleting && (
        <DeleteUploadDialog
          kind="invoice"
          id={deleting.id}
          name={invoiceName(deleting)}
          confirmed={deleting.status === "confirmed"}
          open
          onOpenChange={(o) => !o && setDeleting(null)}
        />
      )}
    </>
  );
}
