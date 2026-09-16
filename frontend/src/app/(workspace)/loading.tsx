import { PageHeader } from "@/components/layout/page-header";
import { Skeleton } from "@/components/ui/skeleton";

/** Shown while a server component's data request is in flight. */
export default function Loading() {
  return (
    <>
      <PageHeader>
        <Skeleton className="h-3 w-24" />
      </PageHeader>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-6 py-8">
          <Skeleton className="h-7 w-56" />
          <Skeleton className="mt-3 h-4 w-80" />
          <Skeleton className="mt-8 h-40 w-full rounded-lg" />
        </div>
      </main>
    </>
  );
}
