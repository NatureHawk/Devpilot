import { Skeleton } from "@/components/ui/skeleton";

/** Shaped like a repository screen: where you are, then the step in front of you. */
export default function Loading() {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-8" aria-busy="true">
        <p role="status" className="sr-only">
          Loading…
        </p>
        <Skeleton className="h-2.5 w-28" />
        <div className="mt-3 flex flex-wrap gap-3">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-4 w-20 rounded-full" />
          ))}
        </div>
        <Skeleton className="mt-10 h-7 w-72" />
        <Skeleton className="mt-3 h-4 w-full max-w-md" />
        <Skeleton className="mt-8 h-32 w-full rounded-lg" />
      </div>
    </div>
  );
}
