import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ui/dialog";
import { api } from "@/lib/api";

const COPY = {
  invoice: {
    path: "/api/invoices",
    title: "Delete this invoice?",
    noun: "Invoice",
    stockNote:
      " Stock from this invoice that is still in inventory will be removed too (recorded in each batch's stock history). Stock already used is not affected.",
    listKey: "invoices",
  },
  scan: {
    path: "/api/scans",
    title: "Delete this scan?",
    noun: "Scan",
    stockNote:
      " If this scan added a new batch, the stock still in it is removed too (recorded in its stock history). A date it confirmed on an existing batch is kept.",
    listKey: "scans",
  },
} as const;

/**
 * Confirms and deletes an uploaded invoice or packaging scan with its file.
 * Confirmed uploads also take the stock they added, and that is still on hand, out of inventory.
 */
export function DeleteUploadDialog({
  kind,
  id,
  name,
  confirmed,
  open,
  onOpenChange,
  onDeleted,
}: {
  kind: keyof typeof COPY;
  id: string;
  name: string;
  confirmed: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const copy = COPY[kind];
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.delete<{ batches_cleared: number }>(`${copy.path}/${id}`),
    onSuccess: ({ batches_cleared }) => {
      client.removeQueries({ queryKey: [kind, id] });
      client.invalidateQueries({ queryKey: [copy.listKey] });
      client.invalidateQueries({ queryKey: ["batches"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      onOpenChange(false);
      toast.success(
        batches_cleared
          ? `${copy.noun} deleted. Its remaining stock was removed from ${batches_cleared} batch${batches_cleared === 1 ? "" : "es"}.`
          : `${copy.noun} deleted`,
      );
      onDeleted?.();
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={copy.title}
      description={`${name} and its uploaded file will be deleted permanently.${confirmed ? copy.stockNote : ""}`}
      confirmLabel={`Delete ${kind}`}
      destructive
      loading={remove.isPending}
      onConfirm={() => remove.mutate()}
    />
  );
}
