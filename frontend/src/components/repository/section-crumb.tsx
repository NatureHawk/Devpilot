"use client";

import { usePathname } from "next/navigation";

import { repositoryPath, SECTION_LABELS } from "@/lib/navigation";

/** The current repository section, named in workflow terms. */
export function SectionCrumb({ owner, repo }: { owner: string; repo: string }) {
  const pathname = usePathname();
  const base = repositoryPath(owner, repo);
  const segment = pathname.startsWith(`${base}/`)
    ? (pathname.slice(base.length + 1).split("/")[0] ?? "")
    : "";
  const label = SECTION_LABELS[segment];

  if (!label) return null;

  return (
    <>
      <span aria-hidden="true" className="text-ink-faint">
        /
      </span>
      <span aria-current="page" className="text-ink-muted shrink-0">
        {label}
      </span>
    </>
  );
}
