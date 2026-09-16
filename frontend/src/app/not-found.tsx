import { AppShell } from "@/components/layout/app-shell";
import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";

export default function NotFound() {
  return (
    <AppShell>
      <PageHeader>
        <PageTitle>Not found</PageTitle>
      </PageHeader>
      <PageBody>
        <EmptyState
          title="This page doesn't exist"
          description="The address may be out of date, or the repository it pointed to is no longer connected."
        >
          <ButtonLink href="/" variant="primary">
            Go to repositories
          </ButtonLink>
        </EmptyState>
      </PageBody>
    </AppShell>
  );
}
