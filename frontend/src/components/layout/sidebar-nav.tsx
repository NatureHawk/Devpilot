"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { FOOTER_NAV, isNavActive, PRIMARY_NAV, type NavItem } from "@/lib/navigation";

const SETS: Record<"primary" | "footer", readonly NavItem[]> = {
  primary: PRIMARY_NAV,
  footer: FOOTER_NAV,
};

/**
 * Global destinations. Takes a set name rather than items: nav items carry icon
 * components, which cannot cross from the server-rendered shell into a client
 * component.
 */
export function SidebarNav({ set, label }: { set: "primary" | "footer"; label: string }) {
  const pathname = usePathname();

  return (
    <nav aria-label={label} className="px-2">
      <ul className="flex flex-col gap-0.5">
        {SETS[set].map((item) => {
          const active = isNavActive(pathname, item);
          const Icon = item.icon;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                title={item.label}
                className={cn(
                  "flex h-8 items-center gap-2.5 rounded-md text-sm transition-colors",
                  "justify-center lg:justify-start lg:px-2",
                  active
                    ? "bg-surface-hover text-ink font-medium"
                    : "text-ink-muted hover:bg-surface-hover hover:text-ink",
                )}
              >
                <Icon aria-hidden="true" className="size-4 shrink-0" strokeWidth={1.75} />
                {/* Hidden but announced on the collapsed rail, visible once labels fit. */}
                <span className="sr-only lg:not-sr-only">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
