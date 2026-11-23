export type Enquiry = { name?: string; phone?: string; email?: string; company?: string; requirement?: string; consent?: boolean; website?: string };

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** Returns field errors, or null when the enquiry is acceptable. `website` is a honeypot: real people never fill it. */
export function validate(e: Enquiry): Record<string, string> | null {
  const errors: Record<string, string> = {};
  const phone = (e.phone ?? "").replace(/[\s()-]/g, "");
  const email = (e.email ?? "").trim();
  if (!(e.name ?? "").trim()) errors.name = "Please tell us your name.";
  if (!phone && !email) errors.phone = "Give a phone number or an email so we can reply.";
  if (phone && !/^\+?\d{8,15}$/.test(phone)) errors.phone = "That phone number does not look right.";
  if (email && !EMAIL.test(email)) errors.email = "That email does not look right.";
  if ((e.requirement ?? "").length > 2000) errors.requirement = "Please keep this under 2000 characters.";
  if (!e.consent) errors.consent = "Please agree so we can reply to you.";
  return Object.keys(errors).length ? errors : null;
}

export function isBot(e: Enquiry): boolean {
  return !!(e.website ?? "").trim();
}

/** A simple per-address limit so the form cannot be used to flood the sales team. */
export function makeLimiter(max: number, windowMs: number) {
  const hits = new Map<string, number[]>();
  return (key: string, now = Date.now()): boolean => {
    const recent = (hits.get(key) ?? []).filter((t) => now - t < windowMs);
    if (recent.length >= max) {
      hits.set(key, recent);
      return false;
    }
    recent.push(now);
    hits.set(key, recent);
    return true;
  };
}
