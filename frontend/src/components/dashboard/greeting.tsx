"use client";

import { useSyncExternalStore } from "react";

function greetingFor(hour: number): string {
  if (hour < 5) return "Good evening";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** The clock is an external source, and it never notifies us. */
const noSubscription = () => () => {};
const clientGreeting = () => greetingFor(new Date().getHours());
const serverGreeting = () => "Good evening";

/**
 * The greeting depends on the viewer's clock, which the server cannot know. The
 * server output is already a valid greeting, so a slow hydration shows a
 * correct page rather than a placeholder.
 */
export function Greeting() {
  const greeting = useSyncExternalStore(noSubscription, clientGreeting, serverGreeting);

  return (
    <h1 suppressHydrationWarning className="text-ink text-3xl font-semibold tracking-tight">
      {greeting}.
    </h1>
  );
}
