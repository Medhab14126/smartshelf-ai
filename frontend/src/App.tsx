import { lazy, type ComponentType } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppLayout } from "@/components/layout/AppLayout";
import { Spinner } from "@/components/ui/feedback";
import { useAuth } from "@/lib/auth";
import { LoginPage, RegisterPage } from "@/pages/AuthPages";

// Secondary pages are code-split so the first load stays small.
const named = <K extends string>(loader: () => Promise<Record<K, ComponentType>>, name: K) =>
  lazy(() => loader().then((m) => ({ default: m[name] })));
const DashboardPage = named(() => import("@/pages/DashboardPage"), "DashboardPage");
const AddBatchPage = named(() => import("@/pages/AddBatchPage"), "AddBatchPage");
const BatchDetailPage = named(() => import("@/pages/BatchDetailPage"), "BatchDetailPage");
const InventoryPage = named(() => import("@/pages/InventoryPage"), "InventoryPage");
const InvoiceReviewPage = named(() => import("@/pages/InvoiceReviewPage"), "InvoiceReviewPage");
const InvoicesPage = named(() => import("@/pages/InvoicesPage"), "InvoicesPage");
const NotificationsPage = named(() => import("@/pages/NotificationsPage"), "NotificationsPage");
const ScanPage = named(() => import("@/pages/ScanPage"), "ScanPage");
const ScanReviewPage = named(() => import("@/pages/ScanReviewPage"), "ScanReviewPage");
const SettingsPage = named(() => import("@/pages/SettingsPage"), "SettingsPage");

function RequireAuth({ children }: { children: JSX.Element }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Spinner label="Loading SmartShelf…" className="min-h-screen" />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

function PublicOnly({ children }: { children: JSX.Element }) {
  const { me, loading } = useAuth();
  if (loading) return <Spinner className="min-h-screen" />;
  return me ? <Navigate to="/" replace /> : children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<PublicOnly><LoginPage /></PublicOnly>} />
      <Route path="/register" element={<PublicOnly><RegisterPage /></PublicOnly>} />
      <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route index element={<DashboardPage />} />
        <Route path="invoices" element={<InvoicesPage />} />
        <Route path="invoices/:invoiceId" element={<InvoiceReviewPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="inventory/new" element={<AddBatchPage />} />
        <Route path="inventory/:batchId" element={<BatchDetailPage />} />
        <Route path="scan" element={<ScanPage />} />
        <Route path="scans/:scanId" element={<ScanReviewPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
