import { cn } from "@/lib/cn";

/** Keyboard hint. Rendered as <kbd> so screen readers announce it as a key. */
export function Kbd({
  children,
  className,
  suppressHydrationWarning,
}: {
  children: string;
  className?: string;
  /** For keys whose label is resolved from the client platform. */
  suppressHydrationWarning?: boolean;
}) {
  return (
    <kbd
      suppressHydrationWarning={suppressHydrationWarning}
      className={cn(
        "border-line-strong text-2xs text-ink-faint inline-flex h-5 min-w-5 items-center justify-center rounded border px-1.5 font-sans",
        className,
      )}
    >
      {children}
    </kbd>
  );
}
