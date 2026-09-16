import { FolderGit2, type LucideIcon, Settings } from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Other exact paths that are this destination (e.g. the old list URL). */
  aliases?: readonly string[];
};

export const PRIMARY_NAV: readonly NavItem[] = [
  {
    label: "Repositories",
    href: "/",
    icon: FolderGit2,
    aliases: ["/repositories", "/repositories/connect"],
  },
] as const;

export const FOOTER_NAV: readonly NavItem[] = [
  { label: "Settings", href: "/settings", icon: Settings },
] as const;

/**
 * Exact matching on purpose: inside a repository the workflow step is the
 * current page, and two links must never both claim `aria-current="page"`.
 */
export function isNavActive(pathname: string, item: NavItem): boolean {
  return pathname === item.href || (item.aliases ?? []).includes(pathname);
}

/** Human names for repository route segments, in workflow terms. */
export const SECTION_LABELS: Record<string, string> = {
  "": "Overview",
  ask: "Understand",
  changes: "Investigate",
  review: "Review",
  "pull-requests": "Ship",
};

export function repositoryPath(owner: string, repo: string, segment = ""): string {
  const base = `/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
  return segment ? `${base}/${segment}` : base;
}
