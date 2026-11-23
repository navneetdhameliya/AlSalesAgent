"use client";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { configureApi, get } from "./api";
import type { Role } from "./roles";

export type Me = { id: string; name: string; email: string; role: Role };

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const DEV_KEY = "ase_dev_token";

let supabase: SupabaseClient | null = null;
export function supabaseClient(): SupabaseClient | null {
  if (!SUPABASE_URL || !SUPABASE_KEY) return null;
  supabase ??= createClient(SUPABASE_URL, SUPABASE_KEY);
  return supabase;
}

export const devLoginAvailable = !SUPABASE_URL;

type AuthState = { me: Me | null; loading: boolean; signOut: () => Promise<void>; refresh: () => Promise<void> };
const Ctx = createContext<AuthState>({ me: null, loading: true, signOut: async () => {}, refresh: async () => {} });
export const useAuth = () => useContext(Ctx);

export function setDevToken(token: string) {
  localStorage.setItem(DEV_KEY, token);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const signOut = useCallback(async () => {
    await supabaseClient()?.auth.signOut();
    localStorage.removeItem(DEV_KEY);
    setMe(null);
    router.replace("/login");
  }, [router]);

  useEffect(() => {
    configureApi(
      async () => {
        const sb = supabaseClient();
        if (sb) return (await sb.auth.getSession()).data.session?.access_token ?? null;
        return localStorage.getItem(DEV_KEY);
      },
      () => {
        setMe(null);
        if (location.pathname !== "/login") router.replace("/login");
      },
    );
  }, [router]);

  const refresh = useCallback(async () => {
    try {
      const { data } = await get<Me>("/me");
      setMe(data);
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (path === "/login") {
      setLoading(false);
      return;
    }
    refresh();
  }, [path, refresh]);

  const value = useMemo(() => ({ me, loading, signOut, refresh }), [me, loading, signOut, refresh]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
