import Link from "next/link";
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-ink hover:bg-accent-hover disabled:bg-accent/40 disabled:text-accent-ink/70",
  secondary:
    "border border-line-strong bg-surface text-ink hover:bg-surface-hover disabled:text-ink-faint",
  ghost: "text-ink-muted hover:bg-surface-hover hover:text-ink disabled:text-ink-faint",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 gap-1.5 px-2.5 text-xs",
  md: "h-8 gap-2 px-3 text-sm",
};

const BASE =
  "inline-flex select-none items-center justify-center rounded-md font-medium transition-colors duration-100 disabled:cursor-not-allowed";

export function buttonStyles(variant: Variant = "secondary", size: Size = "md"): string {
  return cn(BASE, VARIANTS[variant], SIZES[size]);
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
};

export function Button({ variant, size, className, children, ...props }: ButtonProps) {
  return (
    <button className={cn(buttonStyles(variant, size), className)} {...props}>
      {children}
    </button>
  );
}

type ButtonLinkProps = ComponentProps<typeof Link> & {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
};

export function ButtonLink({
  href,
  variant,
  size,
  className,
  children,
  ...props
}: ButtonLinkProps) {
  return (
    <Link href={href} className={cn(buttonStyles(variant, size), className)} {...props}>
      {children}
    </Link>
  );
}
