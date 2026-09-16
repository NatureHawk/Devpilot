import type { Metadata, Viewport } from "next";

import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "DevPilot", template: "%s · DevPilot" },
  description:
    "DevPilot reads your repository, answers questions with the exact code behind each answer, and turns what you learn into reviewed pull requests.",
  applicationName: "DevPilot",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0b0d" },
    { media: "(prefers-color-scheme: light)", color: "#fafafb" },
  ],
};

/**
 * Document shell only. The application chrome (sidebar and main column) is
 * rendered by the layouts below, because inside a repository the sidebar also
 * carries that repository's workflow — which only its layout can load.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint. See lib/theme.ts. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
