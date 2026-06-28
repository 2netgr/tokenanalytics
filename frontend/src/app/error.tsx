"use client";

import { useEffect } from "react";

export default function Error({
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
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
      <div className="text-[var(--tt-fg-muted)] text-sm uppercase tracking-widest">Error</div>
      <h2 className="text-xl font-semibold text-[var(--tt-fg)]">Something went wrong</h2>
      <p className="text-sm text-[var(--tt-fg-muted)] max-w-sm">
        {error.message || "An unexpected error occurred."}
      </p>
      <button
        onClick={reset}
        className="mt-2 px-4 py-2 rounded-md bg-[var(--tt-brand)] text-white text-sm hover:opacity-90 transition-opacity"
      >
        Try again
      </button>
    </div>
  );
}
