"use client";

import { useEffect, useState } from "react";

/**
 * Whole seconds since `running` became true — a real measurement to show during
 * requests whose internal progress cannot be observed. Resets when a new run
 * starts.
 */
export function useElapsedSeconds(running: boolean): number {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    // The reset is an intentional synchronisation with the start of a run.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setElapsed(0);
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  return elapsed;
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}
