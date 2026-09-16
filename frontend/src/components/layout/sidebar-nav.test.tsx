import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SidebarNav } from "@/components/layout/sidebar-nav";
import { FOOTER_NAV, PRIMARY_NAV } from "@/lib/navigation";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

function renderBoth() {
  return render(
    <>
      <SidebarNav set="primary" label="Primary" />
      <SidebarNav set="footer" label="Account" />
    </>,
  );
}

describe("SidebarNav", () => {
  it("renders every global destination", () => {
    pathname.current = "/";
    renderBoth();

    for (const item of [...PRIMARY_NAV, ...FOOTER_NAV]) {
      expect(screen.getByRole("link", { name: item.label })).toHaveAttribute("href", item.href);
    }
  });

  it("treats the old repository list URL as the Repositories page", () => {
    pathname.current = "/repositories";
    renderBoth();

    expect(screen.getByRole("link", { name: "Repositories" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("does not claim the current page inside a repository, where the workflow step does", () => {
    pathname.current = "/repositories/acme/widgets/ask";
    renderBoth();

    expect(screen.getByRole("link", { name: "Repositories" })).not.toHaveAttribute("aria-current");
  });

  it("marks settings only on the settings page", () => {
    pathname.current = "/settings";
    renderBoth();

    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Repositories" })).not.toHaveAttribute("aria-current");
  });
});
