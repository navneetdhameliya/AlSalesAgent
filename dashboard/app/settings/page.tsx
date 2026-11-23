"use client";
import { useEffect, useState } from "react";
import { Modal } from "@/components/Modal";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";

export default function Settings() {
  return (
    <Shell allow={["owner", "admin"]}>
      <div className="page-head"><div><h1>Settings</h1><p className="muted">Business, follow-ups, alerts, integrations and team</p></div></div>
      <div className="gap">
        <General />
        <Widget />
        <Team />
      </div>
    </Shell>
  );
}

const csv = (v: string) => v.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);

function General() {
  const toast = useToast();
  const { data, loading, error, reload } = useApi<any>("/settings");
  const [f, setF] = useState<any>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (!data) return;
    setF({
      business_name: data.business_name, persona_name: data.persona_name, followup_days: (data.followup_days ?? []).join(", "),
      send_window_start: data.send_window_start, send_window_end: data.send_window_end, stuck_lead_hours: data.stuck_lead_hours,
      daily_report_time: data.daily_report_time, report_recipients: (data.report_recipients ?? []).join(", "),
      webhook_export_url: data.webhook_export_url ?? "", webhook_export_secret: "",
    });
  }, [data]);
  const set = (k: string) => (e: React.ChangeEvent<any>) => setF({ ...f, [k]: e.target.value });

  async function save() {
    setBusy(true);
    setFields({});
    const body: any = {
      business_name: f.business_name, persona_name: f.persona_name, followup_days: csv(f.followup_days).map(Number),
      send_window_start: Number(f.send_window_start), send_window_end: Number(f.send_window_end), stuck_lead_hours: Number(f.stuck_lead_hours),
      daily_report_time: f.daily_report_time, report_recipients: csv(f.report_recipients), webhook_export_url: f.webhook_export_url,
    };
    if (f.webhook_export_secret) body.webhook_export_secret = f.webhook_export_secret;
    try {
      await patch("/settings", body);
      toast({ title: "Settings saved" });
      reload2();
    } catch (e) {
      if (e instanceof ApiError && e.fields) setFields(e.fields);
      toast({ title: "Not saved", body: e instanceof ApiError ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }
  const reload2 = () => reload();
  async function testHook() {
    setTesting(true);
    try {
      const { data: r } = await post<any>("/export/webhook-test");
      toast({ title: r?.ok === false ? "Test failed" : "Test sent", body: r?.error ?? (r?.status ? `Your server answered ${r.status}` : undefined) });
    } catch (e) {
      toast({ title: "Test failed", body: e instanceof ApiError ? e.message : undefined });
    } finally {
      setTesting(false);
    }
  }
  const fe = (k: string) => fields[k] && <span className="err">{fields[k]}</span>;

  return (
    <Screen loading={loading} error={error} onRetry={reload}>
      {f && (
        <div className="card gap">
          <h2 style={{ margin: 0 }}>General</h2>
          <div className="grid g2">
            <label>Business name<input value={f.business_name} onChange={set("business_name")} />{fe("business_name")}</label>
            <label>AI display name<input value={f.persona_name} onChange={set("persona_name")} />{fe("persona_name")}</label>
          </div>
          <h3 style={{ margin: "8px 0 0" }}>Follow-ups</h3>
          <div className="grid g3">
            <label>Days after last message<input value={f.followup_days} onChange={set("followup_days")} placeholder="1, 3, 7" />{fe("followup_days")}</label>
            <label>Send from hour (0-23)<input type="number" min="0" max="23" value={f.send_window_start} onChange={set("send_window_start")} />{fe("send_window_start")}</label>
            <label>Send until hour (1-24)<input type="number" min="1" max="24" value={f.send_window_end} onChange={set("send_window_end")} />{fe("send_window_end")}</label>
          </div>
          <h3 style={{ margin: "8px 0 0" }}>Alerts and daily report</h3>
          <div className="grid g3">
            <label>Flag silent leads after (hours)<input type="number" min="1" value={f.stuck_lead_hours} onChange={set("stuck_lead_hours")} />{fe("stuck_lead_hours")}</label>
            <label>Daily report time (India time)<input type="time" value={f.daily_report_time} onChange={set("daily_report_time")} />{fe("daily_report_time")}</label>
            <label>Report recipients<input value={f.report_recipients} onChange={set("report_recipients")} placeholder="a@x.com, b@x.com" />{fe("report_recipients")}</label>
          </div>
          <h3 style={{ margin: "8px 0 0" }}>Send leads to another system</h3>
          <div className="grid g2">
            <label>Webhook URL (https)<input value={f.webhook_export_url} onChange={set("webhook_export_url")} placeholder="https://example.com/hooks/leads" />{fe("webhook_export_url")}</label>
            <label>Signing secret {data.webhook_export_secret_set && <span className="tag">set</span>}<input type="password" autoComplete="new-password" value={f.webhook_export_secret} onChange={set("webhook_export_secret")} placeholder={data.webhook_export_secret_set ? "Leave blank to keep the current one" : "Optional"} />{fe("webhook_export_secret")}</label>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button disabled={testing || !data.webhook_export_url} onClick={testHook}>{testing ? "Sending…" : "Send test event"}</button>
            <button className="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save settings"}</button>
          </div>
        </div>
      )}
    </Screen>
  );
}

function Widget() {
  const toast = useToast();
  const embed = useApi<{ snippet: string; allowed_origins: string[]; warning: string | null }>("/settings/widget-embed");
  const [origins, setOrigins] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (embed.data) setOrigins(embed.data.allowed_origins.join("\n")); }, [embed.data]);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await patch("/settings", { allowed_origins: csv(origins) });
      toast({ title: "Allowed websites saved" });
      embed.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(embed.data!.snippet);
      toast({ title: "Copied" });
    } catch {
      toast({ title: "Could not copy", body: "Select the code and copy it by hand." });
    }
  }
  return (
    <Screen loading={embed.loading} error={embed.error} onRetry={embed.reload}>
      {embed.data && (
        <div className="card gap">
          <h2 style={{ margin: 0 }}>Website chat</h2>
          <p className="muted" style={{ margin: 0 }}>Paste this just before the closing &lt;/body&gt; tag on your website.</p>
          <div className="code">{embed.data.snippet}</div>
          <div><button onClick={copy}>Copy code</button></div>
          <label>Websites allowed to show the chat (one per line, like https://www.example.com)
            <textarea value={origins} onChange={(e) => setOrigins(e.target.value)} />
          </label>
          {embed.data.warning && <p className="err">{embed.data.warning}</p>}
          {err && <p className="err" role="alert">{err}</p>}
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save allowed websites"}</button></div>
        </div>
      )}
    </Screen>
  );
}

type User = { id: string; name: string | null; email: string; phone: string | null; role: string; active: boolean; whatsapp_alerts: boolean };

function Team() {
  const { me } = useAuth();
  const toast = useToast();
  const users = useApi<User[]>("/users");
  const [inv, setInv] = useState<{ email: string; full_name: string; role: string; phone: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function update(u: User, body: object, ok: string) {
    try {
      await patch(`/users/${u.id}`, body);
      toast({ title: ok });
      users.reload();
    } catch (e) {
      toast({ title: "Not changed", body: e instanceof ApiError ? e.message : undefined });
    }
  }
  async function invite(e: React.FormEvent) {
    e.preventDefault();
    if (!inv) return;
    setErr(null);
    try {
      const body: any = { email: inv.email, role: inv.role };
      if (inv.full_name) body.full_name = inv.full_name;
      if (inv.phone) body.phone = inv.phone;
      await post("/users/invite", body);
      toast({ title: "Invitation sent", body: inv.email });
      setInv(null);
      users.reload();
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Could not invite.");
    }
  }
  const roles = me?.role === "owner" ? ["sales", "manager", "admin", "owner"] : ["sales", "manager", "admin"];

  return (
    <div className="gap">
      <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Team</h2><button className="primary" onClick={() => setInv({ email: "", full_name: "", role: "sales", phone: "" })}>Invite someone</button></div>
      <Screen loading={users.loading} error={users.error} onRetry={users.reload} empty={(users.data?.length ?? 0) === 0} emptyTitle="No team members">
        <div className="card tablewrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Person</th><th>Role</th><th>WhatsApp alerts</th><th>Status</th><th /></tr></thead>
            <tbody>{users.data?.map((u) => (
              <tr key={u.id}>
                <td><b>{u.name || u.email}</b><div className="muted small">{u.email}{u.phone ? ` · ${u.phone}` : ""}</div></td>
                <td><select style={{ width: "auto" }} aria-label={`Role for ${u.email}`} value={u.role} disabled={u.id === me?.id} onChange={(e) => update(u, { role: e.target.value }, "Role changed")}>{["owner", "admin", "manager", "sales"].map((r) => <option key={r}>{r}</option>)}</select></td>
                <td><input type="checkbox" aria-label={`WhatsApp alerts for ${u.email}`} checked={u.whatsapp_alerts} onChange={(e) => update(u, { whatsapp_alerts: e.target.checked }, "Updated")} /></td>
                <td>{u.active ? <span className="tag">active</span> : <span className="tag">disabled</span>}</td>
                <td className="right">{u.id !== me?.id && <button className="ghost" onClick={() => update(u, { active: !u.active }, u.active ? "Disabled" : "Enabled")}>{u.active ? "Disable" : "Enable"}</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Screen>
      <Modal open={!!inv} title="Invite someone" onClose={() => setInv(null)}>
        {inv && (
          <form className="gap" onSubmit={invite}>
            <label>Email<input type="email" required value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} /></label>
            <label>Name<input value={inv.full_name} onChange={(e) => setInv({ ...inv, full_name: e.target.value })} /></label>
            <label>WhatsApp number (for alerts)<input value={inv.phone} onChange={(e) => setInv({ ...inv, phone: e.target.value })} /></label>
            <label>Role<select value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}>{roles.map((r) => <option key={r}>{r}</option>)}</select></label>
            {err && <div className="err" role="alert">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" onClick={() => setInv(null)}>Cancel</button><button className="primary">Send invitation</button></div>
          </form>
        )}
      </Modal>
    </div>
  );
}
