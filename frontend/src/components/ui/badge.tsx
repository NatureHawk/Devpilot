import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "border-line-strong text-ink-muted",
  accent: "border-accent/40 text-accent",
  success: "border-success/40 text-success",
  warning: "border-warning/40 text-warning",
  danger: "border-danger/40 text-danger",
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "text-2xs inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-medium",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
