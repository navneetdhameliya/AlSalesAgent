"use client";
import Link from "next/link";
import { useState } from "react";
import { validate, type Enquiry } from "@/lib/enquiry";

export function EnquiryForm() {
  const [f, setF] = useState<Enquiry>({ name: "", phone: "", email: "", company: "", requirement: "", consent: false, website: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");
  const [fail, setFail] = useState<string | null>(null);
  const set = (k: keyof Enquiry) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setFail(null);
    const local = validate(f);
    setErrors(local ?? {});
    if (local) return;
    setState("sending");
    try {
      const res = await fetch("/api/enquiry", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (j.fields) setErrors(j.fields);
        setFail(j.error ?? "Something went wrong. Please try again.");
        setState("idle");
        return;
      }
      setState("done");
    } catch {
      setFail("We could not send that. Check your connection and try again.");
      setState("idle");
    }
  }

  if (state === "done") {
    return (
      <div className="card ok" role="status">
        <h3>Thank you{f.name ? `, ${f.name.split(" ")[0]}` : ""}.</h3>
        <p className="muted" style={{ margin: 0 }}>{f.phone ? "You will get a message on WhatsApp in a moment." : "You will get an email from us in a moment."} If you do not see it, check your spam folder.</p>
      </div>
    );
  }
  const fe = (k: string) => errors[k] && <span className="err" role="alert">{errors[k]}</span>;
  return (
    <form className="enq card" onSubmit={submit} noValidate aria-label="Enquiry form">
      <div className="hp" aria-hidden="true"><label>Website<input tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} /></label></div>
      <div className="row2">
        <label>Your name<input autoComplete="name" value={f.name} onChange={set("name")} aria-invalid={!!errors.name} />{fe("name")}</label>
        <label>Company (optional)<input autoComplete="organization" value={f.company} onChange={set("company")} /></label>
      </div>
      <div className="row2">
        <label>WhatsApp / phone<input type="tel" autoComplete="tel" placeholder="+91 98765 43210" value={f.phone} onChange={set("phone")} aria-invalid={!!errors.phone} />{fe("phone")}</label>
        <label>Email<input type="email" autoComplete="email" value={f.email} onChange={set("email")} aria-invalid={!!errors.email} />{fe("email")}</label>
      </div>
      <label>What do you need?<textarea value={f.requirement} onChange={set("requirement")} />{fe("requirement")}</label>
      <div>
        <label className="check"><input type="checkbox" checked={!!f.consent} onChange={set("consent")} /><span>I agree to be contacted about my enquiry on the details above. <Link href="/privacy" style={{ color: "var(--accent)" }}>Privacy</Link></span></label>
        {fe("consent")}
      </div>
      {fail && <div className="err" role="alert">{fail}</div>}
      <button className="btn primary" disabled={state === "sending"}>{state === "sending" ? "Sending…" : "Send enquiry"}</button>
    </form>
  );
}
