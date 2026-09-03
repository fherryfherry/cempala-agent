"use client";

import { createContext, useContext } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getMe, logout as apiLogout, type MeResponse } from "@/lib/api";

interface AuthValue {
  me: MeResponse | undefined;
  isLoading: boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/** Gates the whole app behind login (ADR-016). The frontend (Next.js, e.g.
 * localhost:3000) and backend (e.g. 127.0.0.1:8000) are different origins, so
 * the session cookie the backend sets is never visible to a Next.js proxy/
 * middleware running on the frontend's own origin — gating has to happen
 * client-side, by actually asking the backend who's logged in. Renders
 * nothing while that check is in flight, avoiding a flash of protected
 * content; real enforcement is always server-side regardless (every API call
 * re-checks the cookie). */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const isLoginPage = pathname === "/login";

  const me = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
    enabled: !isLoginPage,
    retry: false,
  });

  if (isLoginPage) {
    return (
      <AuthContext.Provider value={{ me: undefined, isLoading: false, logout: async () => {} }}>
        {children}
      </AuthContext.Provider>
    );
  }

  if (me.isLoading) {
    return null;
  }

  if (me.isError) {
    if (typeof window !== "undefined") {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
    return null;
  }

  const logout = async () => {
    await apiLogout();
    queryClient.clear();
    router.replace("/login");
  };

  return (
    <AuthContext.Provider value={{ me: me.data, isLoading: false, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
