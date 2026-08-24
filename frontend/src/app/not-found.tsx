import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageBody, PageHeader, PageTitle } from "@/components/layout/page-header";

export default function NotFound() {
  return (
    <>
      <PageHeader>
        <PageTitle>Not found</PageTitle>
      </PageHeader>
      <PageBody>
        <EmptyState
          title="This page doesn't exist"
          description="The address may be out of date, or the repository it referred to is no longer connected."
        >
          <ButtonLink href="/" variant="secondary">
            Back to dashboard
          </ButtonLink>
        </EmptyState>
      </PageBody>
    </>
  );
}
