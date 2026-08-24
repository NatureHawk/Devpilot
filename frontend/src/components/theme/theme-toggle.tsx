"use client";

import { useSyncExternalStore } from "react";

import { cn } from "@/lib/cn";
import {
  getServerThemePreference,
  getThemePreference,
  setThemePreference,
  subscribeToTheme,
  THEME_OPTIONS,
} from "@/lib/theme";

/**
 * Appearance control. The preference is per-browser because there is no account
 * persistence yet — a deliberate limit, not a stub.
 */
export function ThemeToggle() {
  const preference = useSyncExternalStore(
    subscribeToTheme,
    getThemePreference,
    getServerThemePreference,
  );

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className="border-line-strong inline-flex rounded-md border p-0.5"
    >
      {THEME_OPTIONS.map((option) => {
        const active = option.value === preference;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setThemePreference(option.value)}
            className={cn(
              "rounded px-2.5 py-1 text-xs font-medium transition-colors",
              active ? "bg-surface-hover text-ink" : "text-ink-muted hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
