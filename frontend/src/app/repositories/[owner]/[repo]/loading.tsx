import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <Skeleton className="h-5 w-64" />
        <Skeleton className="mt-3 h-4 w-96" />
        <Skeleton className="mt-8 h-48 w-full rounded-lg" />
      </div>
    </div>
  );
}
