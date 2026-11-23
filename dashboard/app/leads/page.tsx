"use client";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { GradeBadge } from "@/components/GradeBadge";
import { Modal } from "@/components/Modal";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, download, post, upload } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, leadName } from "@/lib/format";
import { can } from "@/lib/roles";
import { useApi } from "@/lib/useApi";

const GRADES = ["hot", "warm", "cold", "ungraded"];
const STAGES = ["new", "qualifying", "qualified", "handoff", "booked", "won", "lost", "stopped"];

type Lead = { id: string; name: string | null; phone: string | null; email: string | null; company: string | null; stage: string; grade: string; source: string; last_inbound_at: string | null; human_takeover: boolean; ai_paused: boolean; opted_out: boolean };

export default function LeadsPage() {
  const { me } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [grade, setGrade] = useState("");
  const [stage, setStage] = useState("");
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => { setDq(q); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (grade) params.set("grade", grade);
  if (stage) params.set("stage", stage);
  if (dq) params.set("q", dq);
  const leads = useApi<Lead[]>(`/leads?${params}`, 20000);
  const total = leads.meta?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / 25));
  const manager = me ? can.importLeads(me.role) : false;
  const filtered = !!(grade || stage || dq);

  async function exportCsv() {
    try {
      const p = new URLSearchParams();
      if (grade) p.set("grade", grade);
      if (stage) p.set("stage", stage);
      await download(`/export/leads?${p}`, "leads.csv");
    } catch (e) {
      toast({ title: "Export failed", body: e instanceof ApiError ? e.message : undefined });
    }
  }

  return (
    <Shell allow={["owner", "manager", "sales"]}>
      <div className="page-head">
        <div><h1>Leads</h1><p className="muted">{me?.role === "sales" ? "Leads assigned to you" : "Every lead across WhatsApp, web chat and email"}</p></div>
        <div className="row">
          {manager && <button onClick={() => setImporting(true)}>Import CSV</button>}
          {manager && <button onClick={exportCsv}>Export CSV</button>}
          <button className="primary" onClick={() => setAdding(true)}>New lead</button>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        <input style={{ maxWidth: 280 }} placeholder="Search name, phone, email, company" aria-label="Search leads" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="row" role="group" aria-label="Grade">
          {GRADES.map((g) => <button key={g} className={`chip ${grade === g ? "on" : ""}`} aria-pressed={grade === g} onClick={() => { setGrade(grade === g ? "" : g); setPage(1); }}>{g}</button>)}
        </div>
        <select style={{ width: "auto" }} aria-label="Stage" value={stage} onChange={(e) => { setStage(e.target.value); setPage(1); }}>
          <option value="">All stages</option>
          {STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <Screen loading={leads.loading} error={leads.error} onRetry={leads.reload} empty={(leads.data?.length ?? 0) === 0}
        emptyTitle={filtered ? "No leads match these filters" : "No leads yet"}
        emptyHint={filtered ? "Clear a filter or change the search." : "Leads appear here when someone messages you, fills a form, or you add one."}>
        <div className="card tablewrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Lead</th><th>Grade</th><th>Stage</th><th>Source</th><th>Last message from lead</th><th /></tr></thead>
            <tbody>
              {leads.data?.map((l) => (
                <tr key={l.id} className="link" tabIndex={0} onClick={() => router.push(`/leads/${l.id}`)} onKeyDown={(e) => e.key === "Enter" && router.push(`/leads/${l.id}`)}>
                  <td><b>{leadName(l)}</b><div className="muted small">{[l.company, l.phone, l.email].filter(Boolean).join(" · ")}</div></td>
                  <td><GradeBadge grade={l.grade} /></td>
                  <td><span className="tag">{l.stage}</span></td>
                  <td className="muted">{l.source}</td>
                  <td className="muted">{ago(l.last_inbound_at)}</td>
                  <td className="right">
                    {l.human_takeover && <span className="tag">human</span>} {l.ai_paused && <span className="tag">AI paused</span>} {l.opted_out && <span className="tag">opted out</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="row" style={{ justifyContent: "space-between", marginTop: 12 }}>
          <span className="muted small">{total} lead{total === 1 ? "" : "s"}</span>
          <div className="row">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="small muted">Page {page} of {pages}</span>
            <button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </div>
      </Screen>

      <NewLead open={adding} onClose={() => setAdding(false)} onDone={(id) => router.push(`/leads/${id}`)} />
      <ImportLeads open={importing} onClose={() => setImporting(false)} onDone={() => { setImporting(false); leads.reload(); }} />
    </Shell>
  );
}

function NewLead({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (id: string) => void }) {
  const [f, setF] = useState({ name: "", phone: "", email: "", company: "", requirement: "" });
  const [err, setErr] = useState<{ message: string; fields?: Record<string, string> | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim()));
      const { data } = await post<{ id: string }>("/leads", body);
      setF({ name: "", phone: "", email: "", company: "", requirement: "" });
      onDone(data.id);
    } catch (x) {
      setErr(x instanceof ApiError ? { message: x.message, fields: x.fields } : { message: "Could not save." });
    } finally {
      setBusy(false);
    }
  }
  const fe = (k: string) => err?.fields?.[k] && <span className="err">{err.fields[k]}</span>;
  return (
    <Modal open={open} title="New lead" onClose={onClose}>
      <form className="gap" onSubmit={submit}>
        <label>Name<input value={f.name} onChange={set("name")} />{fe("name")}</label>
        <label>Phone<input value={f.phone} onChange={set("phone")} placeholder="98765 43210 or +91…" />{fe("phone")}</label>
        <label>Email<input type="email" value={f.email} onChange={set("email")} />{fe("email")}</label>
        <label>Company<input value={f.company} onChange={set("company")} /></label>
        <label>What they need<textarea value={f.requirement} onChange={set("requirement")} /></label>
        {err && !err.fields && <div className="err" role="alert">{err.message}</div>}
        <p className="muted small">Adding a lead does not send them a message.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : "Save lead"}</button></div>
      </form>
    </Modal>
  );
}

function ImportLeads({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  async function run() {
    const f = file.current?.files?.[0];
    if (!f) return setErr("Choose a CSV file first.");
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const { data } = await upload("/leads/import", fd);
      setResult(data);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal open={open} title="Import leads from CSV" onClose={() => { setResult(null); onClose(); }}>
      {result ? (
        <div className="gap">
          <p><b>{result.created ?? 0}</b> added, <b>{result.duplicates ?? 0}</b> skipped as duplicates{result.error_count ? <>, <b>{result.error_count}</b> rows had problems</> : null}.</p>
          <p className="muted small">Imported leads are not messaged automatically.</p>
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="primary" onClick={() => { setResult(null); onDone(); }}>Done</button></div>
        </div>
      ) : (
        <div className="gap">
          <p className="muted">Columns such as name, phone, email, company are detected automatically. Max 1 MB. Duplicates (same phone or email) are skipped.</p>
          <input ref={file} type="file" accept=".csv,text/csv" aria-label="CSV file" />
          {err && <div className="err" role="alert">{err}</div>}
          <div className="row" style={{ justifyContent: "flex-end" }}><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={run}>{busy ? "Importing…" : "Import"}</button></div>
        </div>
      )}
    </Modal>
  );
}
