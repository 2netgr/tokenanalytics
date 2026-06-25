"use client";

import { useEffect, useRef, useState } from "react";
import { X, RefreshCw, ArrowRight, Download, AlertTriangle } from "lucide-react";
import WhatsChangedDrawer from "./WhatsChangedDrawer";
import { getVersion, applyUpdate, type VersionInfo } from "@/lib/version";

/**
 * Top-of-app update banner.
 *
 * Renders only when the user's local checkout is behind the remote main. Pulls
 * the diff state + curated highlights from `GET /version`.
 *
 * "Update now" calls `POST /update/apply` — the backend runs `git pull` and
 * restarts itself (the LaunchAgent's KeepAlive brings it back with the new code).
 * The banner then polls `/version` until the commit changes and reloads the page,
 * so the user never has to touch a terminal.
 *
 * "What's changed" opens an in-app slide-over with the highlights (the popup of
 * what's new). Dismissal is keyed on the newest curated release id.
 */

const STORAGE_KEY = "tt-update-dismissed-release";
type UpdateState = "idle" | "updating" | "error";

export default function WhatsNewBanner() {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [dismissedRelease, setDismissedRelease] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [state, setState] = useState<UpdateState>("idle");
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    try { setDismissedRelease(window.localStorage.getItem(STORAGE_KEY)); }
    catch { setDismissedRelease(null); }

    let cancelled = false;
    getVersion()
      .then((d) => { if (!cancelled) setInfo(d); })
      .catch(() => { /* backend down or endpoint missing — silent */ });

    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setDismissedRelease(e.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => {
      cancelled = true;
      window.removeEventListener("storage", onStorage);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const isVisible =
    !!info && info.behind && !!info.latest_release && dismissedRelease !== info.latest_release;

  function dismiss() {
    if (!info?.latest_release) return;
    try { window.localStorage.setItem(STORAGE_KEY, info.latest_release); }
    catch { /* private mode etc. */ }
    setDismissedRelease(info.latest_release);
    setDrawerOpen(false);
  }

  // Poll /version until the backend comes back on a NEW commit, then reload so the
  // freshly-pulled frontend is served. Gives up after ~3 min (deps reinstall etc.).
  function waitForRestartThenReload(fromSha: string | null) {
    const startedAt = Date.now();
    pollRef.current = setInterval(async () => {
      if (Date.now() - startedAt > 180_000) {
        if (pollRef.current) clearInterval(pollRef.current);
        setState("error");
        setError("Update is taking longer than expected — reload manually in a moment.");
        return;
      }
      try {
        const v = await getVersion();
        if (v.current && v.current !== fromSha) {
          if (pollRef.current) clearInterval(pollRef.current);
          window.location.reload();
        }
      } catch { /* backend restarting — keep polling */ }
    }, 3000);
  }

  async function updateNow() {
    setState("updating");
    setError(null);
    const fromSha = info?.current ?? null;
    try {
      const res = await applyUpdate();
      if (res.restarting) {
        waitForRestartThenReload(fromSha);
      } else if (!res.updated) {
        // Already current (rare — banner shows when behind). Just refresh state.
        window.location.reload();
      }
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : "Update failed.");
    }
  }

  if (!isVisible || !info) return null;

  const updating = state === "updating";

  return (
    <>
      <div
        role="status"
        aria-label="TokenAnalytics update available"
        className="relative overflow-hidden border-b border-[var(--tt-brand)]/20 bg-[linear-gradient(90deg,rgba(96,165,250,0.18)_0%,rgba(96,165,250,0.08)_45%,rgba(96,165,250,0.02)_100%)]"
      >
        <div aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-[var(--tt-brand)]" />
        <div
          aria-hidden
          className="absolute inset-y-0 left-0 w-1/3 pointer-events-none opacity-30"
          style={{
            background: "linear-gradient(90deg, transparent 0%, rgba(96,165,250,0.25) 50%, transparent 100%)",
            animation: "tt-update-shimmer 6s linear infinite",
          }}
        />

        <div className="relative flex items-center gap-3 px-5 sm:px-7 py-2.5 flex-wrap">
          <span className="inline-flex items-center gap-1.5 shrink-0 text-[12.5px] font-semibold text-[var(--tt-fg)]">
            <RefreshCw
              size={13}
              className="text-[var(--tt-brand)]"
              style={{ animation: "tt-update-spin 3s linear infinite" }}
            />
            {updating ? "Updating TokenAnalytics…" : "Update available for TokenAnalytics"}
          </span>

          {state === "error" && error && (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] text-[var(--tt-warn)]">
              <AlertTriangle size={12} />
              {error}
            </span>
          )}

          <div className="flex-1 min-w-0" />

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={updateNow}
              disabled={updating}
              className="inline-flex items-center gap-1.5 rounded-md bg-[var(--tt-brand)] px-3 py-1.5 text-[11.5px] font-semibold text-white hover:bg-[var(--tt-brand-strong)] transition-colors disabled:opacity-60 disabled:cursor-default"
            >
              {updating
                ? <RefreshCw size={12} className="animate-spin" />
                : <Download size={12} />}
              {updating ? "Updating…" : (state === "error" ? "Retry update" : "Update now")}
            </button>

            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="group inline-flex items-center gap-1.5 rounded-md border border-[var(--tt-brand)]/60 bg-[var(--tt-brand)]/10 px-3 py-1.5 text-[11.5px] font-semibold text-[var(--tt-brand)] hover:bg-[var(--tt-brand)] hover:text-white hover:border-[var(--tt-brand)] transition-colors"
            >
              What&apos;s changed
              <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
            </button>

            <button
              type="button"
              onClick={dismiss}
              disabled={updating}
              aria-label="Dismiss update banner"
              className="shrink-0 h-7 w-7 grid place-items-center rounded-md text-[var(--tt-fg-muted)] hover:text-[var(--tt-fg)] hover:bg-[var(--tt-panel)] transition-colors disabled:opacity-40"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        <style>{`
          @keyframes tt-update-shimmer {
            0%   { transform: translateX(-100%); }
            100% { transform: translateX(400%); }
          }
          @keyframes tt-update-spin {
            0%   { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
          }
          @media (prefers-reduced-motion: reduce) {
            [aria-label="TokenAnalytics update available"] *[style*="tt-update"] {
              animation: none !important;
            }
          }
        `}</style>
      </div>

      <WhatsChangedDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        releases={info.releases}
        releaseUrl={info.release_url}
        repo={info.repo}
        currentSha={info.current}
        latestSha={info.latest}
      />
    </>
  );
}
