/**
 * Theme preference store.
 *
 * The preference lives in localStorage and the resolved theme lives on the
 * <html> element — both outside React. Components read it through
 * `useSyncExternalStore` rather than mirroring it into state, so a change made
 * in another tab, or by the operating system, stays in sync.
 */

export const THEME_STORAGE_KEY = "devpilot.theme";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_OPTIONS: readonly { value: ThemePreference; label: string }[] = [
  { value: "system", label: "System" },
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
] as const;

const LIGHT_QUERY = "(prefers-color-scheme: light)";
const DEFAULT_PREFERENCE: ThemePreference = "dark";

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

function resolve(preference: ThemePreference): ResolvedTheme {
  if (preference !== "system") return preference;
  return window.matchMedia(LIGHT_QUERY).matches ? "light" : "dark";
}

function apply(preference: ThemePreference): void {
  document.documentElement.setAttribute("data-theme", resolve(preference));
}

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/**
 * DevPilot is dark-first: with nothing stored the answer is "dark", not
 * "system". Following the OS is an explicit choice, so it is stored explicitly.
 * Reading storage can throw in restricted contexts; the default covers that.
 */
export function getThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : DEFAULT_PREFERENCE;
  } catch {
    return DEFAULT_PREFERENCE;
  }
}

export function getServerThemePreference(): ThemePreference {
  return DEFAULT_PREFERENCE;
}

export function setThemePreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Private browsing can reject writes; the theme still applies for this session.
  }
  apply(preference);
  emit();
}

export function subscribeToTheme(listener: () => void): () => void {
  listeners.add(listener);

  // Another tab changed the preference.
  const onStorage = (event: StorageEvent) => {
    if (event.key === THEME_STORAGE_KEY) {
      apply(getThemePreference());
      emit();
    }
  };
  // The OS switched appearance while "system" is selected.
  const media = window.matchMedia(LIGHT_QUERY);
  const onMedia = () => {
    if (getThemePreference() === "system") apply("system");
  };

  window.addEventListener("storage", onStorage);
  media.addEventListener("change", onMedia);

  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
    media.removeEventListener("change", onMedia);
  };
}

/**
 * Runs before first paint to stamp `data-theme` on <html>.
 *
 * Inlined as a string because it must execute synchronously in <head>; doing it
 * in React would run after the first paint and flash the wrong theme.
 */
export const THEME_INIT_SCRIPT = `(function(){try{var s=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});var t=s==='light'||s==='dark'?s:(s==='system'&&window.matchMedia('${LIGHT_QUERY}').matches?'light':'dark');document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;
