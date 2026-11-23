"use client";
import Link from "next/link";
import { useState } from "react";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";

type N = { id: string; kind: string; title: string; body: string | null; lead_id: string | null; read: boolean; created_at: string };

export default function Notifications() {
  const { me } = useAuth();
  const toast = useToast();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const { data, loading, error, reload } = useApi<N[]>(`/notifications?limit=100${unreadOnly ? "&unread_only=true" : ""}`, 15000);
  const canOpenLead = me?.role !== "admin";

  async function mark(fn: () => Promise<unknown>) {
    try {
      await fn();
      await reload();
    } catch (e) {
      toast({ title: "Could not update", body: e instanceof ApiError ? e.message : undefined });
    }
  }
  return (
    <Shell>
      <div className="page-head">
        <div><h1>Notifications</h1><p className="muted">Hot leads, handoffs, approvals and system alerts</p></div>
        <div className="row">
          <button className={`chip ${unreadOnly ? "on" : ""}`} aria-pressed={unreadOnly} onClick={() => setUnreadOnly(!unreadOnly)}>Unread only</button>
          <button onClick={() => mark(() => post("/notifications/read-all"))}>Mark all read</button>
        </div>
      </div>
      <Screen loading={loading} error={error} onRetry={reload} empty={(data?.length ?? 0) === 0} emptyTitle={unreadOnly ? "No unread notifications" : "No notifications yet"} emptyHint="You will see Hot leads and handoffs here as they happen.">
        <div className="gap">
          {data?.map((n) => (
            <div key={n.id} className="card" style={{ opacity: n.read ? 0.7 : 1 }}>
              <div className="row">
                {!n.read && <span aria-label="Unread" style={{ width: 8, height: 8, borderRadius: 8, background: "var(--accent)" }} />}
                <b>{n.title}</b><span className="tag">{n.kind}</span><span className="grow" /><span className="muted small">{ago(n.created_at)}</span>
              </div>
              {n.body && <p className="muted" style={{ whiteSpace: "pre-wrap", margin: "8px 0 0" }}>{n.body}</p>}
              <div className="row" style={{ marginTop: 10 }}>
                {n.lead_id && canOpenLead && <Link className="btn" href={`/leads/${n.lead_id}`}>Open lead</Link>}
                {!n.read && <button className="ghost" onClick={() => mark(() => post(`/notifications/${n.id}/read`))}>Mark read</button>}
              </div>
            </div>
          ))}
        </div>
      </Screen>
    </Shell>
  );
}
