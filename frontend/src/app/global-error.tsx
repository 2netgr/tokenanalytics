"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html>
      <body style={{ margin: 0, fontFamily: "sans-serif", background: "#0a0a0a", color: "#fafafa" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "16px", textAlign: "center", padding: "0 16px" }}>
          <div style={{ fontSize: "11px", letterSpacing: "0.15em", textTransform: "uppercase", color: "#666" }}>Fatal Error</div>
          <h2 style={{ fontSize: "20px", fontWeight: 600, margin: 0 }}>TokenAnalytics failed to load</h2>
          <p style={{ fontSize: "13px", color: "#888", maxWidth: "360px", margin: 0 }}>
            {error.message || "An unexpected error occurred."}
          </p>
          <button
            onClick={reset}
            style={{ marginTop: "8px", padding: "8px 16px", borderRadius: "6px", background: "#7c3aed", color: "#fff", border: "none", fontSize: "13px", cursor: "pointer" }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
