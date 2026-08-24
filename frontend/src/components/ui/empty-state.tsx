import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * The default state of most screens in this milestone, so it is a first-class
 * component rather than an afterthought: a title, an honest explanation of
 * what will fill the space, and at most one action.
 */
export function EmptyState({
  title,
  description,
  children,
  align = "center",
  className,
}: {
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  align?: "center" | "start";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col px-6 py-12",
        align === "center" ? "items-center text-center" : "items-start text-left",
        className,
      )}
    >
      <h3 className="text-ink text-sm font-medium">{title}</h3>
      {description ? (
        <p className="text-ink-muted mt-1.5 max-w-md text-sm leading-relaxed">{description}</p>
      ) : null}
      {children ? <div className="mt-5">{children}</div> : null}
    </div>
  );
}
