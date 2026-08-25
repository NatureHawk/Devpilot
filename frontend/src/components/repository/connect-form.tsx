"use client";

import { useState, useTransition } from "react";

import { connectRepositoryAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/**
 * Connects a repository by address.
 *
 * On success the action redirects, so the only state this holds is the failure
 * message and whether a request is in flight.
 */
export function ConnectForm({ className }: { className?: string }) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [value, setValue] = useState("");

  const submit = (formData: FormData) => {
    setError(null);
    startTransition(async () => {
      const result = await connectRepositoryAction(formData);
      // A successful action redirects and never returns; anything here failed.
      if (result && !result.ok) setError(result.error.message);
    });
  };

  return (
    <form action={submit} className={cn("space-y-3", className)}>
      <div className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="repository" className="sr-only">
          Repository
        </label>
        <input
          id="repository"
          name="repository"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="owner/name"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "connect-error" : undefined}
          className="border-line-strong bg-canvas text-ink placeholder:text-ink-faint focus:border-accent h-8 min-w-0 flex-1 rounded-md border px-2.5 font-mono text-sm transition-colors focus:outline-none"
        />
        <Button type="submit" variant="primary" size="md" disabled={pending || !value.trim()}>
          {pending ? "Connecting…" : "Connect"}
        </Button>
      </div>

      {error ? (
        <p id="connect-error" role="alert" className="text-danger text-xs">
          {error}
        </p>
      ) : null}
    </form>
  );
}
