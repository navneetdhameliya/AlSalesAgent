import Link from "next/link";
import { site } from "@/lib/site.config";

export const metadata = { title: `Privacy | ${site.name}` };

// Starting text only. Have it reviewed and completed before you go live.
export default function Privacy() {
  return (
    <main id="main" className="wrap" style={{ padding: "64px 0", maxWidth: 720 }}>
      <Link href="/" style={{ color: "var(--accent)" }}>← Back</Link>
      <h1 style={{ fontSize: 40 }}>Privacy</h1>
      <p>When you send an enquiry or use the chat, we keep what you tell us (name, phone, email, company, your message) so we can answer you and follow up about that enquiry.</p>
      <p>Replies start with an AI assistant. It will say so if you ask, and you can ask for a team member at any time. Our team can read the conversation.</p>
      <p>We message you only about your enquiry. Reply STOP on WhatsApp or use the unsubscribe link in any email and we stop contacting you.</p>
      <p>We do not sell your details. To see or delete them, write to <a href={`mailto:${site.email}`}>{site.email}</a>.</p>
    </main>
  );
}
