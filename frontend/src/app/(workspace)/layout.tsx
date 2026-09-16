import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

/** Screens outside a repository: the repository list and settings. */
export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
