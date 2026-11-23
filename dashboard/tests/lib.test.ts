import { describe, expect, it } from "vitest";
import { ago, leadName, seconds } from "@/lib/format";
import { NAV, can, homeFor } from "@/lib/roles";

describe("format", () => {
  it("shows seconds, minutes and hours", () => {
    expect(seconds(null)).toBe("–");
    expect(seconds(42)).toBe("42s");
    expect(seconds(600)).toBe("10 min");
    expect(seconds(7200)).toBe("2.0 h");
  });
  it("treats timestamps without a zone as UTC", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(ago("2026-01-01T11:00:00", now)).toBe("1 h ago");
    expect(ago("2026-01-01T11:59:50Z", now)).toBe("just now");
    expect(ago(null)).toBe("–");
  });
  it("falls back through name, phone, email", () => {
    expect(leadName({ name: "A" })).toBe("A");
    expect(leadName({ phone: "+91" })).toBe("+91");
    expect(leadName({})).toBe("Unknown");
  });
});

describe("roles", () => {
  const visible = (r: any) => NAV.filter((n) => n.roles.includes(r)).map((n) => n.href);
  it("keeps admin away from lead screens", () => {
    expect(visible("admin")).not.toContain("/leads");
    expect(visible("admin")).toContain("/settings");
  });
  it("sales sees only leads, approvals and notifications", () => {
    expect(visible("sales")).toEqual(["/leads", "/approvals", "/notifications"]);
  });
  it("only owner and manager approve quotes and assign", () => {
    expect(can.approveQuotes("sales")).toBe(false);
    expect(can.approveQuotes("manager")).toBe(true);
    expect(can.assign("admin")).toBe(false);
  });
  it("sends each role to a page it can open", () => {
    for (const r of ["owner", "admin", "manager", "sales"] as const) expect(visible(r)).toContain(homeFor(r));
  });
});
