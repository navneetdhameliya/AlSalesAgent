"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Orb } from "@/components/Orb";
import { devLoginAvailable, setDevToken, supabaseClient, useAuth } from "@/lib/auth";
import { homeFor } from "@/lib/roles";
import { ApiError, get } from "@/lib/api";

export default function Login() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function finish() {
    try {
      const { data } = await get("/me");
      await refresh();
      router.replace(homeFor(data.role));
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) setError("Your account is not set up yet. Ask an owner to invite you.");
      else if (e instanceof ApiError && e.status === 401) setError("Signed in, but the server did not accept your session token (401). Check the server's SUPABASE_URL.");
      else if (e instanceof ApiError) setError(`Signed in, but the server answered ${e.status}: ${e.message}`);
      else setError(`Signed in, but the server could not be reached. Check that the server's CORS_ORIGINS is exactly ${window.location.origin} and that it has finished restarting (a free server can take a minute to wake).`);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const sb = supabaseClient();
      if (sb) {
        const { error: err } = await sb.auth.signInWithPassword({ email, password });
        if (err) throw new Error("Wrong email or password.");
      } else {
        if (!token.trim()) throw new Error("Paste a developer token.");
        setDevToken(token.trim());
      }
      await finish();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <form className="card gap" style={{ width: "min(400px, 100%)" }} onSubmit={submit}>
        <div className="row"><Orb state="idle" /><h1 style={{ margin: 0 }}>AI Sales Executive</h1></div>
        <p className="muted">Sign in to see your leads and approvals.</p>
        {devLoginAvailable ? (
          <label>Developer token
            <textarea value={token} onChange={(e) => setToken(e.target.value)} placeholder="Paste a signed token from the backend (development only)" />
          </label>
        ) : (
          <>
            <label>Email<input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          </>
        )}
        {error && <div className="err" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
