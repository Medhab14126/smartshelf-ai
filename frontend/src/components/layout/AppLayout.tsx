import { Bell, Camera, FileUp, LayoutDashboard, LogOut, Package, Settings } from "lucide-react";
import { Suspense } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/feedback";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", short: "Home", icon: LayoutDashboard, end: true },
  { to: "/invoices", label: "Upload Invoice", short: "Invoices", icon: FileUp },
  { to: "/inventory", label: "Inventory", short: "Stock", icon: Package },
  { to: "/scan", label: "Scan Product", short: "Scan", icon: Camera },
  { to: "/notifications", label: "Notifications", short: "Alerts", icon: Bell },
  { to: "/settings", label: "Settings", short: "Settings", icon: Settings },
];

function Logo() {
  return (
    <div className="flex items-center gap-2">
      <img src="/favicon.svg" alt="" className="size-8" />
      <div className="leading-tight">
        <p className="font-semibold">SmartShelf AI</p>
      </div>
    </div>
  );
}

export function AppLayout() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    try {
      await logout();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not sign out; please try again");
      return;
    }
    toast.success("Signed out");
    navigate("/login", { replace: true });
  };

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen flex-col border-r bg-card lg:flex">
        <div className="border-b px-5 py-4">
          <Logo />
          <p className="mt-2 truncate text-xs text-muted-foreground">{me?.business.name}</p>
        </div>
        <nav aria-label="Main" className="flex-1 space-y-1 p-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                  isActive && "bg-accent text-accent-foreground",
                )
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t p-3">
          <p className="truncate px-3 text-sm font-medium">{me?.user.full_name}</p>
          <p className="truncate px-3 text-xs text-muted-foreground">{me?.user.email}</p>
          <Button variant="ghost" className="mt-2 w-full justify-start" onClick={handleLogout}>
            <LogOut /> Sign out
          </Button>
        </div>
      </aside>

      <div className="flex min-h-screen flex-col">
        {/* Mobile top bar */}
        <header className="sticky top-0 z-30 flex items-center justify-between border-b bg-card/95 px-4 py-2.5 backdrop-blur lg:hidden">
          <Logo />
          <Button variant="ghost" size="icon" aria-label="Sign out" onClick={handleLogout}>
            <LogOut />
          </Button>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-28 pt-5 sm:px-6 lg:pb-10 lg:pt-8">
          <Suspense fallback={<Spinner />}>
            <Outlet />
          </Suspense>
        </main>

        {/* Mobile bottom navigation */}
        <nav
          aria-label="Main"
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-6 border-t bg-card pb-[env(safe-area-inset-bottom)] lg:hidden"
        >
          {NAV.map(({ to, short, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium text-muted-foreground",
                  isActive && "text-primary",
                )
              }
            >
              <Icon className="size-5" aria-hidden />
              {short}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}
