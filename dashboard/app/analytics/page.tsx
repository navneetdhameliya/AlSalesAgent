"use client";
import Link from "next/link";
import { useState } from "react";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, post } from "@/lib/api";
import { ago, leadName, seconds } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const GRADE_COLOR: Record<string, string> = { hot: "var(--hot)", warm: "var(--warm)", cold: "var(--cold)", ungraded: "var(--ungraded)" };

function Bars({ data, colors }: { data: Record<string, number>; colors?: Record<string, string> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="muted">No data in this period.</p>;
  return (
    <div className="gap" style={{ gap: 8 }}>
      {entries.map(([k, v]) => (
        <div key={k}>
          <div className="row small" style={{ justifyContent: "space-between" }}><span>{k}</span><b>{v}</b></div>
          <div className="bar" role="img" aria-label={`${k}: ${v}`}><i style={{ width: `${(v / max) * 100}%`, background: colors?.[k] }} /></div>
        </div>
      ))}
    </div>
  );
}

export default function Analytics() {
  const toast = useToast();
  const [days, setDays] = useState(7);
  const s = useApi<any>(`/analytics/summary?days=${days}`, 60000);
  const stuck = useApi<any[]>("/analytics/stuck-leads", 60000);
  const [sending, setSending] = useState(false);

  async function sendReport() {
    setSending(true);
    try {
      await post("/reports/daily/send-now");
      toast({ title: "Report sent", body: "Check the report inbox." });
    } catch (e) {
      toast({ title: "Report not sent", body: e instanceof ApiError ? e.message : undefined });
    } finally {
      setSending(false);
    }
  }
  const d = s.data;
  const kpis: [string, string | number][] = d ? [
    ["New leads", d.new_leads], ["Replied", d.leads_replied], ["Hot leads", d.hot_leads], ["AI replies sent", d.ai_replies_sent],
    ["Avg first reply", seconds(d.avg_first_reply_seconds)], ["Median first reply", seconds(d.median_first_reply_seconds)], ["Messages received", d.inbound_messages], ["Waiting on a reply", d.stuck_leads],
  ] : [];

  return (
    <Shell allow={["owner", "manager"]}>
      <div className="page-head">
        <div><h1>Analytics</h1><p className="muted">How the AI is doing over the last {days} days</p></div>
        <div className="row">
          {[7, 30, 90].map((n) => <button key={n} className={`chip ${days === n ? "on" : ""}`} aria-pressed={days === n} onClick={() => setDays(n)}>{n} days</button>)}
          <button disabled={sending} onClick={sendReport}>{sending ? "Sending…" : "Send today's report now"}</button>
        </div>
      </div>
      <Screen loading={s.loading} error={s.error} onRetry={s.reload} empty={!!d && d.new_leads === 0 && d.inbound_messages === 0} emptyTitle="No activity in this period" emptyHint="Numbers appear once leads start messaging.">
        {d && (
          <div className="gap">
            <div className="grid g4">{kpis.map(([k, v]) => <div key={k} className="card"><div className="muted small">{k}</div><div className="kpi">{v}</div></div>)}</div>
            <div className="grid g3">
              <div className="card"><h3>By grade</h3><Bars data={d.by_grade} colors={GRADE_COLOR} /></div>
              <div className="card"><h3>By stage</h3><Bars data={d.by_stage} /></div>
              <div className="card"><h3>By source</h3><Bars data={d.by_source} /></div>
            </div>
          </div>
        )}
      </Screen>
      <h2 style={{ marginTop: 24 }}>Waiting on a reply</h2>
      <Screen loading={stuck.loading} error={stuck.error} onRetry={stuck.reload} empty={(stuck.data?.length ?? 0) === 0} emptyTitle="No stuck leads" emptyHint="Leads we messaged that have not answered for a while show up here.">
        <div className="card tablewrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Lead</th><th>Grade</th><th>Stage</th><th>Waiting</th></tr></thead>
            <tbody>
              {stuck.data?.map((l: any) => (
                <tr key={l.id}><td><Link href={`/leads/${l.id}`}><b>{leadName(l)}</b></Link></td><td>{l.grade}</td><td><span className="tag">{l.stage}</span></td><td className="muted">{l.hours_waiting} h</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Screen>
    </Shell>
  );
}
