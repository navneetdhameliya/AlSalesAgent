"use client";
import { motion, useReducedMotion } from "framer-motion";

/** Fades and lifts in once when scrolled into view. With reduced motion it is simply there. */
export function Reveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  const reduce = useReducedMotion();
  if (reduce) return <>{children}</>;
  return (
    <motion.div initial={{ opacity: 0, y: 18 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.5, delay, ease: [0.2, 0, 0, 1] }}>
      {children}
    </motion.div>
  );
}

export function Words({ text }: { text: string }) {
  const reduce = useReducedMotion();
  return (
    <>
      {text.split(" ").map((w, i) => (
        <motion.span key={i} className="word" initial={reduce ? false : { opacity: 0, y: 14, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ delay: reduce ? 0 : 0.05 * i, duration: 0.45, ease: [0.2, 0, 0, 1] }}>
          {w}&nbsp;
        </motion.span>
      ))}
    </>
  );
}
