import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkflowNav } from "@/components/layout/workflow-nav";
import { deriveWorkflow } from "@/lib/workflow";

const pathname = vi.hoisted(() => ({ current: "/repositories/acme/widgets" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

function renderNav(
  conversationCount: number,
  changeStatuses: Parameters<typeof deriveWorkflow>[0]["changeStatuses"] = [],
) {
  const { stages } = deriveWorkflow({
    repositoryName: "widgets",
    indexingStatus: "indexed",
    conversationCount,
    changeStatuses,
  });
  return render(<WorkflowNav owner="acme" repo="widgets" stages={stages} />);
}

describe("WorkflowNav", () => {
  it("shows every step as a link with its real state", () => {
    pathname.current = "/repositories/acme/widgets";
    renderNav(0);

    expect(screen.getByRole("link", { name: /Repository.*Indexed.*\(done\)/ })).toHaveAttribute(
      "href",
      "/repositories/acme/widgets",
    );
    expect(
      screen.getByRole("link", { name: /Understand.*Ask a question.*\(next step\)/ }),
    ).toHaveAttribute("href", "/repositories/acme/widgets/ask");
    // Locked steps stay navigable, and say why they are not ready.
    expect(
      screen.getByRole("link", { name: /Review.*No proposal yet.*\(not available yet\)/ }),
    ).toHaveAttribute("href", "/repositories/acme/widgets/review");
  });

  it("groups the steps into exploring and building", () => {
    pathname.current = "/repositories/acme/widgets";
    const { container } = renderNav(0);

    expect(container).toHaveTextContent(
      /Explore.*Repository.*Understand.*Build.*Investigate.*Review.*Ship/,
    );
  });

  it("marks the page you are on", () => {
    pathname.current = "/repositories/acme/widgets/review";
    renderNav(1, ["proposed"]);

    expect(screen.getByRole("link", { name: /^Review/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /^Understand/ })).not.toHaveAttribute("aria-current");
    expect(
      screen.getByRole("link", { name: /Review.*1 proposal to review.*\(next step\)/ }),
    ).toBeVisible();
  });
});
