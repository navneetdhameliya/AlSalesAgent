"use client";
import { motion } from "framer-motion";

const COLORS: Record<string, string> = { hot: "#ff5a3c", warm: "#ffb020", cold: "#4c8dff", ungraded: "#6b7380" };

/** Colour and scale animate when the grade changes. A Hot grade gets a short pulse. */
export function GradeBadge({ grade }: { grade: string }) {
  const g = COLORS[grade] ? grade : "ungraded";
  return (
    <motion.span
      key={g}
      className={`pill ${g}`}
      initial={g === "hot" ? { scale: 0.8 } : false}
      animate={{ backgroundColor: COLORS[g], scale: 1 }}
      transition={{ type: "spring", stiffness: 420, damping: 32 }}
      style={{ color: g === "ungraded" ? "#fff" : "#0b0d10" }}
    >
      {g}
    </motion.span>
  );
}
