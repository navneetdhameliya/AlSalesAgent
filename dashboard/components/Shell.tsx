"use client";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { useAuth } from "@/lib/auth";
import { NAV, type Role } from "@/lib/roles";
import { useApi } from "@/lib/useApi";
import { Orb } from "./Orb";
import { useToast } from "./Toasts";

type Notif = { id: string; kind: string; title: string; body: string | null; lead_id: string | null; read: boolean };

/** Sidebar and page frame. Polls unread notifications so a Hot lead or handoff shows up as a moment, not a silent counter. */
export function Shell({ children, allow }: { children: React.ReactNode; allow?: Role[] }) {
  const { me, loading, signOut } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const toast = useToast();
  const seen = useRef<Set<string> | null>(null);
  const unread = useApi<Notif[]>(me ? "/notifications?unread_only=true&limit=50" : null, 15000);

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
  }, [loading, me, router]);

  useEffect(() => {
    const items = unread.data;
    if (!items) return;
    if (seen.current === null) {
      seen.current = new Set(items.map((n) => n.id));
      return;
    }
    for (const n of items) {
      if (seen.current.has(n.id)) continue;
      seen.current.add(n.id);
      toast({ title: n.title, body: n.body ?? undefined, hot: /hot|handoff/.test(n.kind) });
    }
  }, [unread.data, toast]);

  if (loading || !me) {
    return <div className="state" aria-busy="true"><Orb state="thinking" /></div>;
  }
  const items = NAV.filter((n) => n.roles.includes(me.role));
  const blocked = allow && !allow.includes(me.role);
  const count = unread.data?.length ?? 0;

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand"><Orb state="idle" label="AI Sales Executive" /> AI Sales</div>
        <nav className="nav" aria-label="Main">
          {items.map((n) => (
            <Link key={n.href} href={n.href} className={path.startsWith(n.href) ? "on" : ""}>
              {n.label}
              {n.href === "/notifications" && count > 0 && <span className="badge-n">{count}</span>}
            </Link>
          ))}
        </nav>
        <div className="side-foot">
          <div className="small">{me.name || me.email}</div>
          <div className="muted small">{me.role}</div>
          <button className="ghost" style={{ padding: "4px 0" }} onClick={signOut}>Sign out</button>
        </div>
      </aside>
      <AnimatePresence mode="wait">
        <motion.main key={path} className="main" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.24, ease: [0.2, 0, 0, 1] }}>
          {blocked ? (
            <div className="card state" role="alert"><b>You do not have access to this page</b>Ask an owner if you need it.</div>
          ) : (
            children
          )}
        </motion.main>
      </AnimatePresence>
    </div>
  );
}
