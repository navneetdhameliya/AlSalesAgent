"use client";
import { motion, useReducedMotion, useScroll, useSpring, useTransform } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { site } from "@/lib/site.config";

/** The line fills as the visitor scrolls and each step lights when reached. */
export function Journey() {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 75%", "end 60%"] });
  const smooth = useSpring(scrollYProgress, { stiffness: 120, damping: 24 });
  const [active, setActive] = useState(reduce ? site.journey.length : 0);
  const steps = site.journey.length;

  useEffect(() => scrollYProgress.on("change", (v) => setActive(Math.min(steps, Math.floor(v * steps + 0.999)))), [scrollYProgress, steps]);
  const scaleY = useTransform(smooth, (v) => (reduce ? 1 : v));

  return (
    <div className="journey" ref={ref}>
      <div className="rail" />
      <motion.div className="fill" style={{ scaleY }} />
      {site.journey.map((s, i) => (
        <div key={s.title} className={`step ${reduce || i < active ? "on" : ""}`}>
          <span className="dot" />
          <h3>{s.title}</h3>
          <p className="muted">{s.body}</p>
        </div>
      ))}
    </div>
  );
}
