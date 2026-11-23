"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { homeFor } from "@/lib/roles";

export default function Home() {
  const { me, loading } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (loading) return;
    router.replace(me ? homeFor(me.role) : "/login");
  }, [me, loading, router]);
  return <div className="state" aria-busy="true">Loading…</div>;
}
