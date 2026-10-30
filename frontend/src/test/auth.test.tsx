import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { AuthProvider } from "@/lib/auth";

import { ME, mockApi } from "./utils";

afterEach(() => vi.unstubAllGlobals());

describe("sign out", () => {
  it("returns to the sign-in page and forgets the user", async () => {
    let signedIn = true;
    const api = mockApi({
      "GET /api/auth/me": () => (signedIn ? ME : { status: 401, body: { detail: "Not authenticated" } }),
      "POST /api/auth/logout": () => {
        signedIn = false;
        return null;
      },
      "GET /api/inventory/batches": () => ({ items: [], total: 0, page: 1, page_size: 25 }),
      "GET /api/suppliers": () => [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/inventory"]}>
          <AuthProvider>
            <App />
            <Toaster />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();

    expect((await screen.findAllByText("Asha Rao")).length).toBeGreaterThan(0);
    await user.click(screen.getAllByRole("button", { name: /Sign out/ })[0]);

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Asha Rao")).toBeNull();
    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/auth/logout")).toBe(true));
  });
});
