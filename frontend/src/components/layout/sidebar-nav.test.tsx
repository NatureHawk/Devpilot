import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SidebarNav } from "@/components/layout/sidebar-nav";
import { PRIMARY_NAV } from "@/lib/navigation";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

describe("SidebarNav", () => {
  it("renders every primary destination", () => {
    pathname.current = "/";
    render(<SidebarNav />);

    for (const item of PRIMARY_NAV) {
      expect(screen.getByRole("link", { name: item.label })).toHaveAttribute("href", item.href);
    }
  });

  it("marks only the current section as the active page", () => {
    pathname.current = "/repositories";
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "Repositories" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });

  it("keeps Repositories active inside a repository workspace", () => {
    pathname.current = "/repositories/acme/widgets/ask";
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "Repositories" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("does not treat every route as the dashboard", () => {
    // "/" is a prefix of everything, so it needs an exact match.
    pathname.current = "/settings";
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });
});
