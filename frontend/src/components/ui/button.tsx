import { ArrowRight } from "lucide-react";
import Link from "next/link";
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Visual weight follows importance:
 *
 * - primary: the one action a state is about. At most one per screen region.
 * - secondary: a real alternative, outlined and quieter.
 * - ghost: tertiary text actions.
 * - danger: destructive or dismissive decisions, never filled.
 */
type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-ink shadow-[0_1px_0_0_rgb(255_255_255/0.12)_inset,0_1px_2px_0_rgb(0_0_0/0.3)] hover:bg-accent-hover active:brightness-95 disabled:bg-accent/40 disabled:text-accent-ink/70 disabled:shadow-none",
  secondary:
    "border border-line-strong bg-surface text-ink hover:border-ink-faint hover:bg-surface-hover active:bg-elevated disabled:border-line disabled:text-ink-faint",
  ghost:
    "text-ink-muted hover:bg-surface-hover hover:text-ink active:bg-elevated disabled:text-ink-faint",
  danger:
    "border border-danger/35 text-danger hover:border-danger/60 hover:bg-danger/10 active:bg-danger/15 disabled:opacity-50",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 gap-1.5 px-2.5 text-xs",
  md: "h-8 gap-2 px-3 text-sm",
  lg: "h-10 gap-2 px-4 text-sm",
};

const BASE =
  "inline-flex select-none items-center justify-center whitespace-nowrap rounded-md font-medium transition-[background-color,border-color,color,filter,transform] duration-100 active:translate-y-px disabled:pointer-events-none disabled:cursor-not-allowed";

export function buttonStyles(variant: Variant = "secondary", size: Size = "md"): string {
  return cn(BASE, VARIANTS[variant], SIZES[size]);
}

/** The trailing arrow that marks an action as moving you forward in the journey. */
function Forward() {
  return (
    <ArrowRight
      aria-hidden="true"
      className="size-3.5 transition-transform group-hover/button:translate-x-0.5"
      strokeWidth={2.25}
    />
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  /** Append a forward arrow: this action advances to the next step. */
  forward?: boolean;
  children: ReactNode;
};

export function Button({
  variant,
  size,
  forward = false,
  className,
  children,
  type,
  ...props
}: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={cn(buttonStyles(variant, size), "group/button", className)}
      {...props}
    >
      {children}
      {forward ? <Forward /> : null}
    </button>
  );
}

type ButtonLinkProps = ComponentProps<typeof Link> & {
  variant?: Variant;
  size?: Size;
  forward?: boolean;
  children: ReactNode;
};

export function ButtonLink({
  href,
  variant,
  size,
  forward = false,
  className,
  children,
  ...props
}: ButtonLinkProps) {
  return (
    <Link
      href={href}
      className={cn(buttonStyles(variant, size), "group/button", className)}
      {...props}
    >
      {children}
      {forward ? <Forward /> : null}
    </Link>
  );
}
