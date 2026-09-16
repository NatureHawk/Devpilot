import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JourneySummary, JourneyTrack } from "@/components/ui/journey";
import { deriveJourney, deriveWorkflow } from "@/lib/workflow";

function steps(conversationCount: number) {
  const workflow = deriveWorkflow({
    repositoryName: "widgets",
    indexingStatus: "indexed",
    conversationCount,
    changeStatuses: [],
  });
  return { workflow, steps: deriveJourney(workflow, "indexed") };
}

describe("JourneyTrack", () => {
  it("shows all six steps in order, marking done, current and upcoming", () => {
    render(<JourneyTrack steps={steps(1).steps} />);

    const items = within(screen.getByRole("list", { name: "Repository progress" }))
      .getAllByRole("listitem")
      .filter((item) => item.textContent);
    expect(items.map((item) => item.textContent)).toEqual([
      "Connect(done)",
      "Index(done)",
      "Understand(done)",
      "Investigate(you are here)",
      "Review(not started)",
      "Ship(not started)",
    ]);
  });

  it("links steps without blocking any of them", () => {
    render(<JourneyTrack steps={steps(0).steps} hrefFor={(step) => `/r/${step.segment}`} />);

    expect(screen.getByRole("link", { name: /Understand/ })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("link", { name: /Ship/ })).toHaveAttribute("href", "/r/pull-requests");
  });
});

describe("JourneySummary", () => {
  it("reads as accomplishments followed by where the repository stands", () => {
    const { workflow, steps: journey } = steps(0);
    render(<JourneySummary steps={journey} status={workflow.next.status} />);

    expect(screen.getByRole("list", { name: "Progress" })).toHaveTextContent(
      "ConnectedIndexedReady to explore",
    );
  });
});
