import Link from "next/link";
import { DemoChat } from "@/components/DemoChat";
import { EnquiryForm } from "@/components/EnquiryForm";
import { Journey } from "@/components/Journey";
import { Orb } from "@/components/Orb";
import { Reveal, Words } from "@/components/Reveal";
import { site } from "@/lib/site.config";

export default function Home() {
  return (
    <>
      <header className="top">
        <div className="wrap">
          <Link href="/" className="logo"><Orb small /> {site.name}</Link>
          <nav className="links" aria-label="Main">
            <a className="hide" href="#offer">What we do</a>
            <a className="hide" href="#how">How it works</a>
            <a className="hide" href="#faq">Questions</a>
            <a className="btn primary" href="#contact">Get in touch</a>
          </nav>
        </div>
      </header>

      <main id="main">
        <section className="wrap hero">
          <div>
            <span className="eyebrow"><Orb small /> {site.hero.eyebrow}</span>
            <h1><Words text={site.hero.headline} /></h1>
            <p className="lead">{site.hero.sub}</p>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 24 }}>
              <a className="btn primary" href="#contact">Send an enquiry</a>
              <a className="btn" href="#how">See how it works</a>
            </div>
            <p className="muted" style={{ marginTop: 18, fontSize: 14 }}>Prefer to chat? Use the chat bubble in the corner.</p>
          </div>
          <DemoChat />
        </section>

        <section id="offer" className="wrap">
          <Reveal><h2>What you get</h2><p className="lead" style={{ marginBottom: 32 }}>{site.tagline}</p></Reveal>
          <div className="grid3">
            {site.offers.map((o, i) => (
              <Reveal key={o.title} delay={i * 0.08}><div className="card" style={{ height: "100%" }}><h3>{o.title}</h3><p className="muted" style={{ margin: 0 }}>{o.body}</p></div></Reveal>
            ))}
          </div>
        </section>

        <section id="how" className="wrap">
          <Reveal><h2>From first message to a real conversation</h2><p className="lead" style={{ marginBottom: 40 }}>No waiting for office hours, and nobody asks you the same thing twice.</p></Reveal>
          <Journey />
        </section>

        <section id="contact" className="wrap">
          <div className="contact">
            <Reveal><h2>Tell us what you need</h2><p className="lead">Leave your details and you will hear from us in moments, on WhatsApp if you gave a phone number, otherwise by email.</p></Reveal>
            <EnquiryForm />
          </div>
        </section>

        <section id="faq" className="wrap tight">
          <Reveal><h2>Questions</h2></Reveal>
          <div style={{ maxWidth: 760 }}>
            {site.faqs.map((f) => (<details key={f.q}><summary>{f.q}</summary><p>{f.a}</p></details>))}
          </div>
        </section>
      </main>

      <footer>
        <div className="wrap" style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <span>© {new Date().getFullYear()} {site.name}</span>
          <span><a href={`mailto:${site.email}`}>{site.email}</a> · <a href={`https://wa.me/${site.whatsapp}`}>{site.phone}</a> · <Link href="/privacy">Privacy</Link></span>
        </div>
      </footer>
    </>
  );
}
