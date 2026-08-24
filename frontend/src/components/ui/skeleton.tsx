import { cn } from "@/lib/cn";

/** Placeholder block for loading states. Pulses only; no shimmer sweep. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("bg-line animate-pulse rounded", className)} />;
}

/** Loading shape for a list of rows, sized to match the real rows. */
export function RowsSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="divide-line divide-y" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-3 px-4 py-3">
          <Skeleton className="size-4 rounded" />
          <Skeleton className="h-3 w-48" />
          <Skeleton className="ml-auto h-3 w-16" />
        </div>
      ))}
    </div>
  );
}
