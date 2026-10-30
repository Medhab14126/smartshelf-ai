import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { ApiError, api, setUnauthorizedHandler } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<unknown>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);
export const ME_KEY = ["auth", "me"];

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const meQuery = useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await api.get<Me>("/api/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });

  useEffect(() => {
    setUnauthorizedHandler(() => client.setQueryData<Me | null>(ME_KEY, null));
  }, [client]);

  const value: AuthState = {
    me: meQuery.data ?? null,
    loading: meQuery.isLoading,
    refresh: () => meQuery.refetch(),
    logout: async () => {
      try {
        await api.post("/api/auth/logout");
      } catch (error) {
        // An expired session is already signed out on the server; anything else must not look like success.
        if (!(error instanceof ApiError && error.status === 401)) throw error;
      }
      // Mark the user signed out *before* dropping other data. `client.clear()` would also remove the
      // "me" query that this provider observes, leaving the old user on screen after sign-out.
      client.setQueryData<Me | null>(ME_KEY, null);
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
