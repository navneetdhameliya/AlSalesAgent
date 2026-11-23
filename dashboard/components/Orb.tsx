"use client";
export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "paused" | "human";

/** The AI's presence. One state value drives it; loops are CSS-only so they stop under reduced motion. */
export function Orb({ state = "idle", label }: { state?: OrbState; label?: string }) {
  return (
    <span className={`orb ${state}`} role="img" aria-label={label ?? `AI ${state}`}>
      <span className="core" />
      {state === "thinking" && (
        <>
          <span className="dot" />
          <span className="dot" />
          <span className="dot" />
        </>
      )}
    </span>
  );
}
