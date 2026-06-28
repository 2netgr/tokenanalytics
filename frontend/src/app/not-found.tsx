import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
      <div className="text-[var(--tt-fg-muted)] text-sm uppercase tracking-widest">404</div>
      <h2 className="text-xl font-semibold text-[var(--tt-fg)]">Page not found</h2>
      <p className="text-sm text-[var(--tt-fg-muted)] max-w-sm">
        The page you&apos;re looking for doesn&apos;t exist.
      </p>
      <Link
        href="/"
        className="mt-2 px-4 py-2 rounded-md bg-[var(--tt-brand)] text-white text-sm hover:opacity-90 transition-opacity"
      >
        Back to dashboard
      </Link>
    </div>
  );
}
