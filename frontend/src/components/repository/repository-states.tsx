import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Panel } from "@/components/ui/panel";

/**
 * Shown on every workspace tab when the address does not correspond to a
 * connected repository. The URL is honoured as a valid destination — it is the
 * repository record that is missing.
 */
export function NotConnectedState({ owner, repo }: { owner: string; repo: string }) {
  return (
    <Panel>
      <EmptyState
        title={`${owner}/${repo} is not connected`}
        description="DevPilot only reads repositories that have been connected to this workspace. Connect it to give DevPilot access to the code, then index it to make the code searchable."
      >
        <ButtonLink href="/repositories/connect" variant="primary" size="sm">
          Connect repository
        </ButtonLink>
      </EmptyState>
    </Panel>
  );
}

/**
 * A repository can be connected without being readable: indexing is the step
 * that turns file contents into something DevPilot can search.
 */
export function NotIndexedState() {
  return (
    <Panel>
      <div className="px-6 pt-7 pb-6">
        <h2 className="text-ink text-lg font-medium tracking-tight">
          This repository hasn&apos;t been indexed yet
        </h2>
        <p className="text-ink-muted mt-2 max-w-2xl text-sm leading-relaxed">
          Indexing is what makes a repository answerable. Until it runs, DevPilot has an address but
          no view of the code.
        </p>
      </div>

      <ul className="divide-line border-line grid grid-cols-1 divide-y border-t sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <li className="px-6 py-4">
          <h3 className="text-ink text-sm font-medium">Inspect and parse</h3>
          <p className="text-ink-muted mt-1 text-xs leading-relaxed">
            Walk the default branch, read source files, and record how the project is laid out.
          </p>
        </li>
        <li className="px-6 py-4">
          <h3 className="text-ink text-sm font-medium">Prepare for retrieval</h3>
          <p className="text-ink-muted mt-1 text-xs leading-relaxed">
            Split code into meaningful units and embed them so a question can find the files that
            answer it.
          </p>
        </li>
      </ul>

      <div className="border-line flex flex-wrap items-center gap-3 border-t px-6 py-4">
        {/* Deliberately inert: the ingestion pipeline does not exist yet, and a
            button that appears to start one would misrepresent the product. */}
        <button
          type="button"
          disabled
          aria-describedby="indexing-availability"
          className="bg-accent/40 text-accent-ink/70 inline-flex h-8 cursor-not-allowed items-center rounded-md px-3 text-sm font-medium"
        >
          Index repository
        </button>
        <span id="indexing-availability" className="text-ink-faint text-xs">
          Indexing runs once the ingestion pipeline is in place.
        </span>
      </div>
    </Panel>
  );
}
