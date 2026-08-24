"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { PRIMARY_NAV } from "@/lib/navigation";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className="flex flex-col gap-0.5 px-2">
      {PRIMARY_NAV.map(({ label, href, icon: Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            title={label}
            className={cn(
              "flex h-7 items-center gap-2.5 rounded-md text-sm transition-colors",
              "justify-center lg:justify-start lg:px-2",
              active
                ? "bg-surface-hover text-ink font-medium"
                : "text-ink-muted hover:bg-surface-hover hover:text-ink",
            )}
          >
            <Icon aria-hidden="true" className="size-4 shrink-0" strokeWidth={1.75} />
            {/* Hidden but announced on the collapsed rail, visible once labels fit. */}
            <span className="sr-only lg:not-sr-only">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
