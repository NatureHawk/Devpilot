"use client";

import { useState, useTransition } from "react";

import { connectRepositoryAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/**
 * Connects a repository by address.
 *
 * On success the action redirects to the repository, whose next step is
 * indexing, so the only state held here is the failure and whether a request is
 * in flight.
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
    <form action={submit} className={cn("space-y-2", className)}>
      <label htmlFor="repository" className="text-ink block text-sm font-medium">
        Repository
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id="repository"
          name="repository"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="owner/name or https://github.com/owner/name"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "connect-error connect-hint" : "connect-hint"}
          className="border-line-strong bg-canvas text-ink placeholder:text-ink-faint focus:border-accent h-10 min-w-0 flex-1 rounded-md border px-3 font-mono text-sm transition-colors focus:outline-none"
        />
        <Button type="submit" variant="primary" size="lg" disabled={pending || !value.trim()}>
          {pending ? "Connecting…" : "Connect repository"}
        </Button>
      </div>
      <p id="connect-hint" className="text-ink-faint text-xs">
        For example <span className="font-mono">vercel/next.js</span>. Visibility and the default
        branch are read from GitHub.
      </p>

      {error ? (
        <p id="connect-error" role="alert" className="text-danger text-sm">
          {error}
        </p>
      ) : null}
    </form>
  );
}
