"use client";
import Link from "next/link";
import { useState } from "react";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import { can } from "@/lib/roles";
import { useApi } from "@/lib/useApi";

type Draft = { id: string; lead_id: string; body: string; channel: string; created_at: string };
type Quote = { id: string; lead_id: string; product: string; quantity: string | number; total: string; currency: string; preview: string; cover_message: string | null; created_at: string };

export default function Approvals() {
  const { me } = useAuth();
  const toast = useToast();
  const { data, loading, error, reload } = useApi<{ drafts: Draft[]; quotations: Quote[] }>("/approvals", 10000);
  const [busy, setBusy] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const canQuote = me ? can.approveQuotes(me.role) : false;
  const none = !data || (data.drafts.length === 0 && data.quotations.length === 0);

  async function run(key: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(key);
    try {
      await fn();
      toast({ title: ok });
      await reload();
    } catch (e) {
      toast({ title: "That did not work", body: e instanceof ApiError ? e.message : undefined });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Shell allow={["owner", "manager", "sales"]}>
      <div className="page-head"><div><h1>Approvals</h1><p className="muted">Nothing here goes to a customer until you approve it.</p></div></div>
      <Screen loading={loading} error={error} onRetry={reload} empty={none} emptyTitle="You are all caught up" emptyHint="AI drafts and price quotes waiting for approval will appear here.">
        <div className="gap">
          {data && data.quotations.length > 0 && <h3>Price quotes</h3>}
          {data?.quotations.map((q) => (
            <div key={q.id} className="card">
              <div className="row"><b>{q.product} × {q.quantity}</b><span className="tag">{q.total} {q.currency}</span><span className="grow" /><span className="muted small">{ago(q.created_at)}</span></div>
              <pre className="code" style={{ whiteSpace: "pre-wrap", margin: "10px 0" }}>{q.preview}</pre>
              <div className="row">
                <Link href={`/leads/${q.lead_id}`} className="btn">Open lead</Link><span className="grow" />
                {canQuote ? (
                  <>
                    <button className="danger" disabled={busy === q.id} onClick={() => run(q.id, () => post(`/quotations/${q.id}/reject`, {}), "Quote rejected")}>Reject</button>
                    <button className="primary" disabled={busy === q.id} onClick={() => run(q.id, () => post(`/quotations/${q.id}/approve`, {}), "Quote sent")}>Approve and send</button>
                  </>
                ) : <span className="muted small">Only an owner or manager can approve quotes.</span>}
              </div>
            </div>
          ))}
          {data && data.drafts.length > 0 && <h3>AI drafts</h3>}
          {data?.drafts.map((d) => (
            <div key={d.id} className="card">
              <div className="row"><span className="tag">{d.channel}</span><span className="grow" /><span className="muted small">{ago(d.created_at)}</span></div>
              <textarea aria-label="Draft text" style={{ margin: "10px 0" }} value={edits[d.id] ?? d.body} onChange={(e) => setEdits({ ...edits, [d.id]: e.target.value })} />
              <div className="row">
                <Link href={`/leads/${d.lead_id}`} className="btn">Open lead</Link><span className="grow" />
                <button className="danger" disabled={busy === d.id} onClick={() => run(d.id, () => post(`/messages/${d.id}/reject`), "Draft discarded")}>Discard</button>
                <button className="primary" disabled={busy === d.id} onClick={() => run(d.id, () => post(`/messages/${d.id}/approve`, edits[d.id] && edits[d.id] !== d.body ? { text: edits[d.id] } : {}), "Sent")}>Approve and send</button>
              </div>
            </div>
          ))}
        </div>
      </Screen>
    </Shell>
  );
}
