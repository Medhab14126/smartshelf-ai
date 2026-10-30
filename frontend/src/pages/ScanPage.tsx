import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, ImageUp, Link2, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { DeleteUploadDialog } from "@/components/DeleteUploadDialog";
import { PageHeader } from "@/components/PageHeader";
import { ProcessBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Spinner } from "@/components/ui/feedback";
import { api, query } from "@/lib/api";
import type { Batch, Page, Scan } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const MAX_MB = 15;

const scanName = (scan: Scan) => scan.extraction?.fields.product_name?.value ?? scan.original_filename;

export function ScanPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const batchId = params.get("batch");
  const invoiceItemId = params.get("invoice_item");
  const quantity = params.get("quantity");
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const linkedBatch = useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => api.get<Batch>(`/api/inventory/batches/${batchId}`),
    enabled: !!batchId,
  });
  const [deleting, setDeleting] = useState<Scan | null>(null);
  const scans = useQuery({
    queryKey: ["scans"],
    queryFn: () => api.get<Page<Scan>>(`/api/scans${query({ page_size: 10 })}`),
    refetchInterval: (q) => (q.state.data?.items.some((s) => s.status === "uploaded" || s.status === "processing") ? 3000 : false),
  });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      if (batchId) body.append("batch_id", batchId);
      if (invoiceItemId) body.append("invoice_item_id", invoiceItemId);
      if (quantity && Number(quantity) > 0) body.append("quantity", quantity);
      return api.post<Scan>("/api/scans/product", body);
    },
    onSuccess: (scan) => {
      client.invalidateQueries({ queryKey: ["scans"] });
      toast.success("Photo uploaded. Reading the label…");
      navigate(`/scans/${scan.id}`);
    },
    onError: (error) => toast.error(error.message),
  });

  const handle = (file: File | undefined) => {
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      toast.error("Please use a JPEG, PNG or WEBP photo");
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      toast.error(`The photo is larger than ${MAX_MB} MB`);
      return;
    }
    upload.mutate(file);
  };

  return (
    <>
      <PageHeader
        title="Scan Product"
        description="Photograph the part of the package that shows the dates and batch number. You confirm every date before reminders use it."
      />
      {batchId && linkedBatch.data && (
        <Alert className="mb-4" title="Linked to an inventory batch">
          <Link2 className="mr-1 inline size-4" />
          The scan will update{" "}
          <Link to={`/inventory/${batchId}`} className="font-medium underline">
            {linkedBatch.data.product.name}
            {linkedBatch.data.batch_number ? ` (batch ${linkedBatch.data.batch_number})` : ""}
          </Link>
          .
        </Alert>
      )}
      {invoiceItemId && <Alert className="mb-4">The scan will create a batch for the selected invoice line.</Alert>}

      <Card>
        <CardContent className="grid gap-3 p-5 sm:grid-cols-2 sm:p-6">
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="sr-only"
            aria-label="Take a photo"
            onChange={(e) => {
              handle(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="sr-only"
            aria-label="Upload a photo"
            onChange={(e) => {
              handle(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <Button size="lg" className="h-24 flex-col gap-1 text-base" onClick={() => cameraRef.current?.click()} loading={upload.isPending}>
            <Camera className="!size-7" /> Take a photo
          </Button>
          <Button size="lg" variant="outline" className="h-24 flex-col gap-1 text-base" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            <ImageUp className="!size-7" /> Upload a photo
          </Button>
          <ul className="space-y-1 text-sm text-muted-foreground sm:col-span-2">
            <li>• Hold the phone steady and fill the frame with the date area.</li>
            <li>• Avoid glare on shiny packaging; good light helps a lot.</li>
          </ul>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Recent scans</CardTitle>
        </CardHeader>
        <CardContent>
          {scans.isLoading ? (
            <Spinner />
          ) : scans.data?.items.length ? (
            <ul className="divide-y">
              {scans.data.items.map((scan) => (
                <li key={scan.id} className="flex items-center gap-1">
                  <Link to={`/scans/${scan.id}`} className="flex min-w-0 flex-1 items-center gap-3 py-2.5 hover:bg-accent/40">
                    <img src={`/api/scans/${scan.id}/image`} alt="" className="size-12 rounded-md border object-cover" loading="lazy" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{scanName(scan)}</p>
                      <p className="text-xs text-muted-foreground">{formatDateTime(scan.created_at)}</p>
                    </div>
                    <ProcessBadge status={scan.status} />
                  </Link>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Delete scan ${scanName(scan)}`}
                    disabled={scan.status === "processing"}
                    onClick={() => setDeleting(scan)}
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No scans yet.</p>
          )}
        </CardContent>
      </Card>
      {deleting && (
        <DeleteUploadDialog
          kind="scan"
          id={deleting.id}
          name={scanName(deleting)}
          confirmed={deleting.status === "confirmed"}
          open
          onOpenChange={(o) => !o && setDeleting(null)}
        />
      )}
    </>
  );
}
