import { Activity, GitPullRequest, LayoutDashboard, type LucideIcon, Settings } from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
};

export const PRIMARY_NAV: readonly NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Repositories", href: "/repositories", icon: GitPullRequest },
  { label: "Activity", href: "/activity", icon: Activity },
  { label: "Settings", href: "/settings", icon: Settings },
] as const;

export type WorkspaceTab = {
  label: string;
  /** Appended to the repository base path; "" is the overview. */
  segment: string;
};

export const WORKSPACE_TABS: readonly WorkspaceTab[] = [
  { label: "Overview", segment: "" },
  { label: "Code", segment: "code" },
  { label: "Ask", segment: "ask" },
  { label: "Changes", segment: "changes" },
  { label: "Pull Requests", segment: "pull-requests" },
] as const;

export function repositoryPath(owner: string, repo: string, segment = ""): string {
  const base = `/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
  return segment ? `${base}/${segment}` : base;
}
