"use client";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useState } from "react";
import { site } from "@/lib/site.config";
import { Orb } from "./Orb";

type Line = { me: boolean; text: string };
const SCRIPT: Line[] = [
  { me: true, text: "Hi, can you tell me how this works?" },
  { me: false, text: "Hello! I am the assistant. Tell me what you are looking for and I will help right away." },
  { me: true, text: "I need a quote, and I would like it this month." },
  { me: false, text: "Happy to help. A team member checks every quote before it reaches you. What quantity do you have in mind?" },
];

/** A looping, scripted example conversation. It is an illustration, not a live chat; the real chat is the bubble in the corner. */
export function DemoChat() {
  const reduce = useReducedMotion();
  const [n, setN] = useState(reduce ? SCRIPT.length : 0);
  const [typing, setTyping] = useState(false);

  useEffect(() => {
    if (reduce) return setN(SCRIPT.length);
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const later = (fn: () => void, ms: number) => timers.push(setTimeout(() => !cancelled && fn(), ms));
    const play = (i: number) => {
      if (i >= SCRIPT.length) return later(() => { setN(0); play(0); }, 4500);
      if (SCRIPT[i].me) later(() => { setN(i + 1); play(i + 1); }, 1100);
      else { setTyping(true); later(() => { setTyping(false); setN(i + 1); play(i + 1); }, 1300); }
    };
    play(0);
    return () => { cancelled = true; timers.forEach(clearTimeout); };
  }, [reduce]);

  return (
    <div className="card demo" role="img" aria-label="Example conversation with the assistant">
      <div className="demo-head"><Orb /><div><b>{site.assistantName}</b><small>Online</small></div></div>
      <div className="demo-body" aria-hidden="true">
        <AnimatePresence initial={false}>
          {SCRIPT.slice(0, n).map((l, i) => (
            <motion.div key={i} className={`bubble ${l.me ? "me" : ""}`} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 260, damping: 26 }}>{l.text}</motion.div>
          ))}
        </AnimatePresence>
        {typing && <span className="typing"><i /><i /><i /></span>}
      </div>
    </div>
  );
}
