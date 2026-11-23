"use client";
import { useState } from "react";
import { Modal } from "@/components/Modal";
import { Screen } from "@/components/Screen";
import { Shell } from "@/components/Shell";
import { useToast } from "@/components/Toasts";
import { ApiError, del, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import { can } from "@/lib/roles";
import { useApi } from "@/lib/useApi";

type Item = { id: string; title: string; kind: string; source: string; chunks: number; updated_at: string };
type Rule = { id: string; rule_type: string; min_qty: number; max_qty: number | null; amount: string | null; tiers: any[]; formula: string | null; note: string | null; active: boolean };
type Product = { id: string; name: string; description: string | null; unit: string; currency: string; attributes: Record<string, string>; active: boolean; rules: Rule[] };

export default function Knowledge() {
  const { me } = useAuth();
  const [tab, setTab] = useState<"items" | "products">("items");
  return (
    <Shell allow={["owner", "admin", "manager"]}>
      <div className="page-head">
        <div><h1>Knowledge</h1><p className="muted">What the AI is allowed to say. It only uses what is here.</p></div>
        <div className="row">
          <button className={`chip ${tab === "items" ? "on" : ""}`} aria-pressed={tab === "items"} onClick={() => setTab("items")}>Documents</button>
          <button className={`chip ${tab === "products" ? "on" : ""}`} aria-pressed={tab === "products"} onClick={() => setTab("products")}>Products and prices</button>
        </div>
      </div>
      {me && (tab === "items" ? <Items edit={can.editKnowledge(me.role)} /> : <Products edit={can.editCatalog(me.role)} />)}
    </Shell>
  );
}

function Items({ edit }: { edit: boolean }) {
  const toast = useToast();
  const { data, loading, error, reload } = useApi<Item[]>("/knowledge");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ title: "", kind: "faq", text: "" });
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const [searchErr, setSearchErr] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await post("/knowledge", f);
      setOpen(false);
      setF({ title: "", kind: "faq", text: "" });
      toast({ title: "Added" });
      reload();
    } catch (x) {
      toast({ title: "Could not add", body: x instanceof ApiError ? x.message : undefined });
    } finally {
      setBusy(false);
    }
  }
  async function archive(id: string) {
    try {
      await del(`/knowledge/${id}`);
      toast({ title: "Archived" });
      reload();
    } catch (x) {
      toast({ title: "Could not archive", body: x instanceof ApiError ? x.message : undefined });
    }
  }
  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setSearchErr(null);
    try {
      setHits((await post<any[]>("/knowledge/test-search", { query: q })).data);
    } catch (x) {
      setSearchErr(x instanceof ApiError ? x.message : "Search failed.");
    }
  }

  return (
    <div className="gap">
      <div className="card">
        <h2>Try a question</h2>
        <form className="row" onSubmit={search}><input className="grow" aria-label="Test question" placeholder="Ask what a customer might ask" value={q} onChange={(e) => setQ(e.target.value)} /><button>Search</button></form>
        {searchErr && <p className="err">{searchErr}</p>}
        {hits && (hits.length === 0 ? <p className="muted" style={{ marginTop: 10 }}>Nothing found. The AI would hand this to a person.</p> : hits.map((h) => (
          <div key={h.chunk_id} style={{ marginTop: 10 }}><b className="small">{h.title}</b> <span className="muted small">match {h.score}</span><p className="muted" style={{ margin: "2px 0 0" }}>{h.content.slice(0, 280)}</p></div>
        )))}
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Documents</h2>{edit && <button className="primary" onClick={() => setOpen(true)}>Add document</button>}</div>
      <Screen loading={loading} error={error} onRetry={reload} empty={(data?.length ?? 0) === 0} emptyTitle="Nothing in the knowledge base" emptyHint={edit ? "Add FAQs, policies and product details so the AI can answer." : "An owner or admin can add documents."}>
        <div className="card tablewrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Title</th><th>Type</th><th>Parts</th><th>Updated</th><th /></tr></thead>
            <tbody>{data?.map((i) => (
              <tr key={i.id}><td><b>{i.title}</b></td><td><span className="tag">{i.kind}</span></td><td className="muted">{i.chunks}</td><td className="muted">{ago(i.updated_at)}</td>
                <td className="right">{edit && <button className="danger ghost" onClick={() => archive(i.id)}>Archive</button>}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </Screen>
      <Modal open={open} title="Add document" onClose={() => setOpen(false)}>
        <form className="gap" onSubmit={add}>
          <label>Title<input required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
          <label>Type<select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{["faq", "product", "policy", "transcript", "other"].map((k) => <option key={k}>{k}</option>)}</select></label>
          <label>Text<textarea required style={{ minHeight: 160 }} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} /></label>
          <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" onClick={() => setOpen(false)}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Adding…" : "Add"}</button></div>
        </form>
      </Modal>
    </div>
  );
}

function ruleText(r: Rule) {
  if (r.rule_type === "fixed") return `Fixed ${r.amount}`;
  if (r.rule_type === "per_unit") return `${r.amount} per unit`;
  if (r.rule_type === "formula") return `Formula: ${r.formula}`;
  return `Tiers: ${r.tiers.map((t) => `${t.up_to == null ? "above" : `up to ${t.up_to}`} → ${t.unit_price}`).join(", ")}`;
}

function Products({ edit }: { edit: boolean }) {
  const toast = useToast();
  const { data, loading, error, reload } = useApi<Product[]>("/products?include_inactive=true");
  const [np, setNp] = useState<{ name: string; unit: string; currency: string } | null>(null);
  const [rule, setRule] = useState<{ product: Product; rule_type: string; min_qty: string; amount: string; formula: string; tiers: string; note: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState({ product: "", quantity: "1", attrs: "" });
  const [testOut, setTestOut] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, ok: string, after?: () => void) {
    setErr(null);
    try {
      await fn();
      toast({ title: ok });
      after?.();
      reload();
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Failed.");
    }
  }
  function saveRule() {
    if (!rule) return;
    const body: any = { rule_type: rule.rule_type, min_qty: Number(rule.min_qty || 0), note: rule.note || null };
    try {
      if (rule.rule_type === "fixed" || rule.rule_type === "per_unit") body.amount = rule.amount;
      if (rule.rule_type === "formula") body.formula = rule.formula;
      if (rule.rule_type === "tiered") body.tiers = JSON.parse(rule.tiers || "[]");
    } catch {
      return setErr('Tiers must be JSON, for example [{"up_to": 10, "unit_price": "5"}, {"up_to": null, "unit_price": "4"}].');
    }
    run(() => post(`/products/${rule.product.id}/rules`, body), "Price rule added", () => setRule(null));
  }
  async function runTest(e: React.FormEvent) {
    e.preventDefault();
    setTestOut(null);
    let attributes = {};
    try {
      attributes = test.attrs.trim() ? JSON.parse(test.attrs) : {};
    } catch {
      return setTestOut('Details must be JSON, for example {"width": 4}.');
    }
    try {
      const { data: r } = await post<any>("/price/test", { product: test.product, quantity: Number(test.quantity), attributes });
      setTestOut(`${r.quantity} × ${r.unit_price_text} = ${r.total_text}${r.note ? ` (${r.note})` : ""}`);
    } catch (x) {
      setTestOut(x instanceof ApiError ? x.message : "Could not price that.");
    }
  }

  return (
    <div className="gap">
      <div className="card">
        <h2>Price check</h2>
        <form className="row" onSubmit={runTest} style={{ alignItems: "flex-end" }}>
          <label className="grow">Product<select required value={test.product} onChange={(e) => setTest({ ...test, product: e.target.value })}><option value="">Choose…</option>{data?.filter((p) => p.active).map((p) => <option key={p.id}>{p.name}</option>)}</select></label>
          <label style={{ width: 110 }}>Quantity<input type="number" min="0" step="any" value={test.quantity} onChange={(e) => setTest({ ...test, quantity: e.target.value })} /></label>
          <label className="grow">Details (JSON, optional)<input value={test.attrs} onChange={(e) => setTest({ ...test, attrs: e.target.value })} placeholder='{"width": 4, "height": 2}' /></label>
          <button>Calculate</button>
        </form>
        {testOut && <p style={{ marginTop: 10 }}><b>{testOut}</b></p>}
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Products</h2>{edit && <button className="primary" onClick={() => setNp({ name: "", unit: "unit", currency: "INR" })}>Add product</button>}</div>
      {err && <div className="err" role="alert">{err}</div>}
      <Screen loading={loading} error={error} onRetry={reload} empty={(data?.length ?? 0) === 0} emptyTitle="No products yet" emptyHint="The AI cannot quote prices until products and price rules are added.">
        {data?.map((p) => (
          <div key={p.id} className="card" style={{ opacity: p.active ? 1 : 0.6 }}>
            <div className="row"><b>{p.name}</b><span className="tag">per {p.unit} · {p.currency}</span>{!p.active && <span className="tag">inactive</span>}<span className="grow" />
              {edit && <><button onClick={() => setRule({ product: p, rule_type: "per_unit", min_qty: "0", amount: "", formula: "", tiers: "", note: "" })}>Add price rule</button>
                <button className="ghost" onClick={() => run(() => patch(`/products/${p.id}`, { active: !p.active }), p.active ? "Product deactivated" : "Product activated")}>{p.active ? "Deactivate" : "Activate"}</button></>}</div>
            {p.description && <p className="muted" style={{ margin: "6px 0" }}>{p.description}</p>}
            {p.rules.filter((r) => r.active).length === 0 ? <p className="muted small">No price rules. The AI cannot quote this product.</p> : (
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>{p.rules.filter((r) => r.active).map((r) => (
                <li key={r.id} className="small">From {r.min_qty}{r.max_qty ? ` to ${r.max_qty}` : "+"}: {ruleText(r)}{r.note ? ` (${r.note})` : ""}
                  {edit && <button className="ghost danger" style={{ padding: "0 6px" }} onClick={() => run(() => del(`/price-rules/${r.id}`), "Rule removed")}>remove</button>}</li>
              ))}</ul>
            )}
          </div>
        ))}
      </Screen>

      <Modal open={!!np} title="Add product" onClose={() => setNp(null)}>
        {np && (
          <form className="gap" onSubmit={(e) => { e.preventDefault(); run(() => post("/products", np), "Product added", () => setNp(null)); }}>
            <label>Name<input required value={np.name} onChange={(e) => setNp({ ...np, name: e.target.value })} /></label>
            <div className="grid g2"><label>Unit<input value={np.unit} onChange={(e) => setNp({ ...np, unit: e.target.value })} /></label><label>Currency<input value={np.currency} onChange={(e) => setNp({ ...np, currency: e.target.value })} /></label></div>
            {err && <div className="err">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" onClick={() => setNp(null)}>Cancel</button><button className="primary">Add</button></div>
          </form>
        )}
      </Modal>
      <Modal open={!!rule} title={rule ? `Price rule: ${rule.product.name}` : ""} onClose={() => setRule(null)}>
        {rule && (
          <div className="gap">
            <label>Type<select value={rule.rule_type} onChange={(e) => setRule({ ...rule, rule_type: e.target.value })}><option value="fixed">Fixed price</option><option value="per_unit">Price per unit</option><option value="tiered">Tiered</option><option value="formula">Formula</option></select></label>
            <label>Applies from quantity<input type="number" min="0" step="any" value={rule.min_qty} onChange={(e) => setRule({ ...rule, min_qty: e.target.value })} /></label>
            {(rule.rule_type === "fixed" || rule.rule_type === "per_unit") && <label>Amount<input value={rule.amount} onChange={(e) => setRule({ ...rule, amount: e.target.value })} placeholder="e.g. 450.00" /></label>}
            {rule.rule_type === "formula" && <label>Formula<input value={rule.formula} onChange={(e) => setRule({ ...rule, formula: e.target.value })} placeholder="width * height * 120" /></label>}
            {rule.rule_type === "tiered" && <label>Tiers (JSON)<textarea value={rule.tiers} onChange={(e) => setRule({ ...rule, tiers: e.target.value })} placeholder='[{"up_to": 10, "unit_price": "5"}, {"up_to": null, "unit_price": "4"}]' /></label>}
            <label>Note (optional)<input value={rule.note} onChange={(e) => setRule({ ...rule, note: e.target.value })} /></label>
            {err && <div className="err" role="alert">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}><button onClick={() => setRule(null)}>Cancel</button><button className="primary" onClick={saveRule}>Save rule</button></div>
          </div>
        )}
      </Modal>
    </div>
  );
}
