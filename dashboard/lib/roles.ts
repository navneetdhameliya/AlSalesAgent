export type Role = "owner" | "admin" | "manager" | "sales";

// Mirrors the backend role table. The server is the real gate; this only decides what to show.
export const NAV: { href: string; label: string; roles: Role[] }[] = [
  { href: "/leads", label: "Leads", roles: ["owner", "manager", "sales"] },
  { href: "/approvals", label: "Approvals", roles: ["owner", "manager", "sales"] },
  { href: "/knowledge", label: "Knowledge", roles: ["owner", "admin", "manager"] },
  { href: "/analytics", label: "Analytics", roles: ["owner", "manager"] },
  { href: "/notifications", label: "Notifications", roles: ["owner", "admin", "manager", "sales"] },
  { href: "/settings", label: "Settings", roles: ["owner", "admin"] },
];

export const can = {
  approveQuotes: (r: Role) => r === "owner" || r === "manager",
  assign: (r: Role) => r === "owner" || r === "manager",
  importLeads: (r: Role) => r === "owner" || r === "manager",
  editKnowledge: (r: Role) => r === "owner" || r === "admin",
  editCatalog: (r: Role) => r === "owner" || r === "admin",
};

export function homeFor(role: Role): string {
  return NAV.find((n) => n.roles.includes(role))?.href ?? "/notifications";
}
