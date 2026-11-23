import { NextResponse } from "next/server";
import { isBot, makeLimiter, validate, type Enquiry } from "@/lib/enquiry";

const allow = makeLimiter(5, 10 * 60 * 1000); // 5 enquiries per address per 10 minutes

export async function POST(req: Request) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  if (!allow(ip)) return NextResponse.json({ error: "Too many enquiries. Please try again in a few minutes." }, { status: 429 });

  let body: Enquiry;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }
  if (isBot(body)) return NextResponse.json({ ok: true }); // pretend success, send nothing
  const errors = validate(body);
  if (errors) return NextResponse.json({ error: "Please check the form.", fields: errors }, { status: 422 });

  const api = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
  const key = process.env.LEAD_API_KEY;
  if (!api || !key) return NextResponse.json({ error: "The form is not connected yet." }, { status: 503 });

  const payload = {
    name: body.name?.trim(),
    phone: body.phone?.trim() || undefined,
    email: body.email?.trim() || undefined,
    company: body.company?.trim() || undefined,
    requirement: body.requirement?.trim() || undefined,
    source_detail: req.headers.get("referer")?.slice(0, 300) || "website enquiry form",
  };
  try {
    const res = await fetch(`${api}/api/v1/public/leads`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": key },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => null);
      return NextResponse.json({ error: j?.error?.message || "We could not save your enquiry. Please try again." }, { status: res.status === 422 ? 422 : 502 });
    }
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "We could not reach our system. Please try again in a moment." }, { status: 502 });
  }
}
