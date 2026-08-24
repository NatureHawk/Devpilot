"use client";

/**
 * Last-resort boundary for failures in the root layout itself, where the app
 * shell (and its stylesheet) may not have rendered. Styles are inline for that
 * reason.
 */
export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0b0d",
          color: "#e8eaed",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div style={{ maxWidth: "32rem", padding: "2rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "0.9375rem", fontWeight: 600, margin: 0 }}>
            DevPilot failed to start
          </h1>
          <p style={{ margin: "0.5rem 0 1.25rem", fontSize: "0.875rem", color: "#99a1aa" }}>
            An error occurred while rendering the application shell.
          </p>
          <button
            onClick={reset}
            style={{
              background: "#4c8dff",
              color: "#fff",
              border: 0,
              borderRadius: "0.375rem",
              padding: "0.5rem 0.875rem",
              fontSize: "0.8125rem",
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
