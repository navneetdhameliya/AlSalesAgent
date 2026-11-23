import { describe, expect, it } from "vitest";
import { isBot, makeLimiter, validate } from "@/lib/enquiry";

const ok = { name: "Ravi", phone: "+91 98765 43210", consent: true };

describe("validate", () => {
  it("accepts a phone or an email", () => {
    expect(validate(ok)).toBeNull();
    expect(validate({ name: "A", email: "a@b.co", consent: true })).toBeNull();
  });
  it("needs a name, a way to reply and consent", () => {
    expect(validate({ consent: true, phone: "9876543210" })).toHaveProperty("name");
    expect(validate({ name: "A", consent: true })).toHaveProperty("phone");
    expect(validate({ name: "A", phone: "9876543210" })).toHaveProperty("consent");
  });
  it("rejects malformed contact details", () => {
    expect(validate({ ...ok, phone: "abc" })).toHaveProperty("phone");
    expect(validate({ ...ok, email: "nope" })).toHaveProperty("email");
  });
});

describe("spam controls", () => {
  it("treats a filled honeypot as a bot", () => {
    expect(isBot({ website: "http://spam" })).toBe(true);
    expect(isBot({ website: "" })).toBe(false);
  });
  it("limits repeat submissions per address and recovers after the window", () => {
    const allow = makeLimiter(2, 1000);
    expect([allow("ip", 0), allow("ip", 10), allow("ip", 20)]).toEqual([true, true, false]);
    expect(allow("other", 30)).toBe(true);
    expect(allow("ip", 1500)).toBe(true);
  });
});
