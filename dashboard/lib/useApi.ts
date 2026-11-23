"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";

export type Loaded<T> = {
  data: T | null;
  meta?: any;
  error: ApiError | null;
  loading: boolean;
  reload: () => Promise<void>;
};

/** Load a GET endpoint. `refreshMs` re-fetches quietly in the background (paused while the tab is hidden). */
export function useApi<T = any>(path: string | null, refreshMs?: number): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [meta, setMeta] = useState<any>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(!!path);
  const alive = useRef(true);

  const load = useCallback(
    async (quiet = false) => {
      if (!path) return;
      if (!quiet) setLoading(true);
      try {
        const res = await get<T>(path);
        if (!alive.current) return;
        setData(res.data);
        setMeta(res.meta);
        setError(null);
      } catch (e) {
        if (alive.current) setError(e instanceof ApiError ? e : new ApiError(0, "error", String(e)));
      } finally {
        if (alive.current && !quiet) setLoading(false);
      }
    },
    [path],
  );

  useEffect(() => {
    alive.current = true;
    setData(null);
    load();
    if (!refreshMs) return () => void (alive.current = false);
    const id = setInterval(() => {
      if (!document.hidden) load(true);
    }, refreshMs);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [load, refreshMs]);

  return { data, meta, error, loading, reload: () => load(true) };
}
