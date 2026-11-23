// Thin client for the FastAPI backend. Every call sends the signed-in user's token.
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public fields?: Record<string, string> | null) {
    super(message);
  }
}

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

type TokenGetter = () => Promise<string | null>;
let getToken: TokenGetter = async () => null;
let onUnauthorized: () => void = () => {};

export function configureApi(tokenGetter: TokenGetter, unauthorized: () => void) {
  getToken = tokenGetter;
  onUnauthorized = unauthorized;
}

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/v1${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the server. Check your connection and try again.");
  }
  if (res.status === 401) onUnauthorized();
  if (!res.ok) {
    let body: any = null;
    try {
      body = await res.json();
    } catch {}
    const e = body?.error;
    throw new ApiError(res.status, e?.code || "error", e?.message || `Request failed (${res.status})`, e?.fields);
  }
  return res;
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<{ data: T; meta?: any }> {
  const res = await raw(path, init);
  if (res.status === 204) return { data: null as T };
  return res.json();
}

export const get = <T = any>(path: string) => api<T>(path);
export const post = <T = any>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T = any>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T = any>(path: string) => api<T>(path, { method: "DELETE" });

export async function download(path: string, filename: string) {
  const res = await raw(path);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function upload<T = any>(path: string, form: FormData) {
  return api<T>(path, { method: "POST", body: form });
}
