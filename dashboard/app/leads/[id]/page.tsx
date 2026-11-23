"use client";
import { AnimatePresence, motion } from "framer-motion";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { GradeBadge } from "@/components/GradeBadge";
import { Orb, type OrbState } from "@/components/Orb";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, clock, leadName, seconds } from "@/lib/format";
import { can } from "@/lib/roles";
import { useApi } from "@/lib/useApi";

type Msg = { id: string; direction: "inbound" | "outbound"; sender: string; channel: string; body: string; status: string; error: string | null; is_quote: boolean; created_at: string };
const STAGES = ["new", "qualifying", "qualified", "handoff", "booked", "won", "lost"];
const GRADES = ["ungraded", "hot", "warm", "cold"];

export default function LeadDetail() {
  const { id } = useParams<{ id: string }>();
  const { me } = useAuth();
  const toast = useToast();
  const lead = useApi<any>(`/leads/${id}`, 10000);
  const msgs = useApi<Msg[]>(`/leads/${id}/messages`, 4000);
  const team = useApi<{ id: string; name: string; role: string }[]>(me && can.assign(me.role) ? "/team" : null);
  const [busy, setBusy] = useState<string | null>(null);
  const l = lead.data;

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    try {
      await fn();
      if (ok) toast({ title: ok });
      await Promise.all([lead.reload(), msgs.reload()]);
    } catch (e) {
      toast({ title: "That did not work", body: e instanceof ApiError ? e.message : "Try again." });
    } finally {
      setBusy(null);
    }
  }

  const list = msgs.data ?? [];
  const last = list.filter((m) => m.status !== "pending_approval").at(-1);
  let orb: OrbState = "idle";
  if (l?.ai_paused) orb = "paused";
  else if (l?.human_takeover) orb = "human";
  else if (last?.direction === "inbound" && Date.now() - new Date(last.created_at.endsWith("Z") ? last.created_at : last.created_at + "Z").getTime() < 120000) orb = "thinking";
  const orbText = { idle: "AI is handling this chat", thinking: "AI is replying", paused: "AI is paused", human: "A person has taken over", listening: "", speaking: "" }[orb];

  return (
    <Shell allow={["owner", "manager", "sales"]}>
      <Screen loading={lead.loading} error={lead.error} onRetry={lead.reload}>
        {l && (
          <>
            <div className="page-head">
              <div>
                <div className="row"><Orb state={orb} /><h1 style={{ margin: 0 }}>{leadName(l)}</h1><GradeBadge grade={l.grade} /><span className="tag">{l.stage}</span></div>
                <p className="muted small" style={{ marginTop: 6 }}>{orbText} · {[l.company, l.phone, l.email].filter(Boolean).join(" · ")}</p>
              </div>
              <div className="row">
                {l.human_takeover
                  ? <button disabled={busy === "rel"} onClick={() => act("rel", () => post(`/leads/${id}/release`), "AI is back in charge")}>Hand back to AI</button>
                  : <button disabled={busy === "take"} onClick={() => act("take", () => post(`/leads/${id}/takeover`), "You have taken over")}>Take over</button>}
                <button disabled={busy === "pause"} onClick={() => act("pause", () => post(`/leads/${id}/ai-pause`, { paused: !l.ai_paused }), l.ai_paused ? "AI resumed" : "AI paused")}>{l.ai_paused ? "Resume AI" : "Pause AI"}</button>
              </div>
            </div>

            <AnimatePresence>
              {l.handoff && (
                <motion.div className="card hot" style={{ marginBottom: 14 }} initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                  <div className="row"><b>Needs a person</b><span className="tag">{l.handoff.reason}</span><span className="grow" />
                    <button onClick={() => act("resolve", () => post(`/handoffs/${l.handoff.id}/resolve`), "Marked as handled")}>Mark handled</button></div>
                  <p className="muted" style={{ whiteSpace: "pre-wrap", margin: "8px 0 0" }}>{l.handoff.summary}</p>
                </motion.div>
              )}
            </AnimatePresence>
            {l.opted_out && <div className="card" style={{ marginBottom: 14 }}>This lead has opted out. Nothing can be sent to them.</div>}

            <div className="split">
              <Chat id={id} msgs={msgs} l={l} orb={orb} act={act} busy={busy} />
              <div className="gap">
                <Details l={l} onSaved={() => lead.reload()} />
                {me && can.assign(me.role) && (
                  <div className="card">
                    <h3>Assigned to</h3>
                    <select aria-label="Assign to" value={l.assigned_to ?? ""} onChange={(e) => e.target.value && act("assign", () => post(`/leads/${id}/assign`, { profile_id: e.target.value }), "Assigned")}>
                      <option value="">Unassigned</option>
                      {team.data?.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.role})</option>)}
                    </select>
                    {team.error && <p className="err">Could not load the team.</p>}
                  </div>
                )}
                <div className="card">
                  <h3>Why this grade</h3>
                  <p>{l.grade_reason || <span className="muted">Not graded yet.</span>}</p>
                  {(l.score_signals as any[]).length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }}>{(l.score_signals as any[]).map((s, i) => <li key={i} className="small muted">{typeof s === "string" ? s : s.signal ?? JSON.stringify(s)}</li>)}</ul>}
                  <p className="muted small" style={{ marginTop: 10 }}>First reply: {seconds(l.first_response_seconds)} · Next follow-up: {l.next_followup_at ? clock(l.next_followup_at) : "none scheduled"}</p>
                </div>
              </div>
            </div>
          </>
        )}
      </Screen>
    </Shell>
  );
}

function Chat({ id, msgs, l, orb, act, busy }: { id: string; msgs: ReturnType<typeof useApi<Msg[]>>; l: any; orb: OrbState; act: (k: string, f: () => Promise<unknown>, ok?: string) => Promise<void>; busy: string | null }) {
  const [text, setText] = useState("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const box = useRef<HTMLDivElement>(null);
  const list = msgs.data ?? [];
  const n = list.length;

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [n]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    await act("send", () => post(`/leads/${id}/send`, { text: t }));
    setText("");
  }

  return (
    <div className="card">
      <h2>Conversation</h2>
      <Screen loading={msgs.loading} error={msgs.error} onRetry={msgs.reload} empty={list.length === 0} emptyTitle="No messages yet" emptyHint="Messages from every channel show up here in order.">
        <div className="chat" ref={box} aria-live="polite">
          <AnimatePresence initial={false}>
            {list.map((m) => {
              const draft = m.status === "pending_approval";
              return (
                <motion.div key={m.id} layout="position" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}
                  className={`msg ${m.direction === "outbound" ? "out" : ""} ${draft ? "draft" : ""} ${m.is_quote ? "quote" : ""}`}>
                  {draft ? (
                    <>
                      <b className="small">AI draft, waiting for you</b>
                      <textarea aria-label="Edit draft" value={edits[m.id] ?? m.body} onChange={(e) => setEdits({ ...edits, [m.id]: e.target.value })} style={{ marginTop: 6 }} />
                      <div className="row" style={{ marginTop: 6 }}>
                        <button className="primary" disabled={busy === m.id} onClick={() => act(m.id, () => post(`/messages/${m.id}/approve`, edits[m.id] && edits[m.id] !== m.body ? { text: edits[m.id] } : {}), "Sent")}>Approve and send</button>
                        <button className="danger" disabled={busy === m.id} onClick={() => act(m.id, () => post(`/messages/${m.id}/reject`), "Draft discarded")}>Discard</button>
                      </div>
                    </>
                  ) : (
                    <>
                      {m.body}
                      <span className="meta">{m.direction === "inbound" ? "Lead" : m.sender === "ai" ? "AI" : "Team"} · {m.channel} · {clock(m.created_at)}{m.status === "failed" ? ` · not delivered${m.error ? `: ${m.error}` : ""}` : ""}</span>
                    </>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
          {orb === "thinking" && <div className="msg out" aria-label="AI is typing"><span className="typing"><i /><i /><i /></span></div>}
        </div>
      </Screen>
      <form onSubmit={send} className="row" style={{ marginTop: 12, alignItems: "flex-end" }}>
        <textarea className="grow" aria-label="Message" style={{ minHeight: 44 }} placeholder={l.human_takeover ? "Write to the lead" : "Write to the lead (the AI keeps replying too unless you take over)"} value={text} onChange={(e) => setText(e.target.value)} disabled={l.opted_out} />
        <button className="primary" disabled={busy === "send" || !text.trim() || l.opted_out}>Send</button>
      </form>
    </div>
  );
}

function Details({ l, onSaved }: { l: any; onSaved: () => void }) {
  const toast = useToast();
  const { me } = useAuth();
  const restricted = me?.role === "sales"; // salespeople may only edit notes
  const [f, setF] = useState({ requirement: l.requirement ?? "", budget_text: l.budget ?? "", timeline_text: l.timeline ?? "", location_text: l.location ?? "", notes: l.notes ?? "", stage: l.stage, grade: l.grade });
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);

  useEffect(() => {
    if (dirty.current) return;
    setF({ requirement: l.requirement ?? "", budget_text: l.budget ?? "", timeline_text: l.timeline ?? "", location_text: l.location ?? "", notes: l.notes ?? "", stage: l.stage, grade: l.grade });
  }, [l]);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<any>) => { dirty.current = true; setF({ ...f, [k]: e.target.value }); };

  async function save() {
    setBusy(true);
    setFields({});
    try {
      const orig: Record<string, string> = { requirement: l.requirement ?? "", budget_text: l.budget ?? "", timeline_text: l.timeline ?? "", location_text: l.location ?? "", notes: l.notes ?? "", stage: l.stage, grade: l.grade };
      const changed = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== orig[k] && (!restricted || k === "notes")));
      if (Object.keys(changed).length === 0) {
        toast({ title: "Nothing to save" });
        return;
      }
      await patch(`/leads/${l.id}`, changed);
      dirty.current = false;
      toast({ title: "Saved" });
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.fields) setFields(e.fields);
      toast({ title: "Could not save", body: e instanceof ApiError ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card gap">
      <h3 style={{ margin: 0 }}>Details</h3>
      <div className="grid g2">
        <label>Stage<select disabled={restricted} value={f.stage} onChange={set("stage")}>{[...new Set([...STAGES, f.stage])].map((s) => <option key={s}>{s}</option>)}</select></label>
        <label>Grade<select disabled={restricted} value={f.grade} onChange={set("grade")}>{GRADES.map((s) => <option key={s}>{s}</option>)}</select></label>
      </div>
      <label>Needs<textarea disabled={restricted} value={f.requirement} onChange={set("requirement")} />{fields.requirement && <span className="err">{fields.requirement}</span>}</label>
      <div className="grid g2">
        <label>Budget<input disabled={restricted} value={f.budget_text} onChange={set("budget_text")} /></label>
        <label>Timeline<input disabled={restricted} value={f.timeline_text} onChange={set("timeline_text")} /></label>
      </div>
      <label>Location<input disabled={restricted} value={f.location_text} onChange={set("location_text")} /></label>
      <label>Internal notes<textarea value={f.notes} onChange={set("notes")} /></label>
      <p className="muted small" style={{ margin: 0 }}>Source: {l.source} · Added {ago(l.created_at)}. Setting the grade yourself overrides the AI.{restricted ? " You can edit notes only." : ""}</p>
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save changes"}</button></div>
    </div>
  );
}
