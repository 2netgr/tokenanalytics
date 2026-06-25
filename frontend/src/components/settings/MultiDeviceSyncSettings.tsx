"use client";

import { useEffect, useState } from "react";
import {
  RefreshCw, Loader2, Check, Copy, Eye, EyeOff, AlertTriangle, Server, Wifi,
} from "lucide-react";
import { Card, CardHeader, CardTitle, Badge, Button } from "@/components/ui";
import { getRemoteAccess, useResource, type RemoteAccess } from "@/lib/api";
import { timeAgo } from "@/lib/notifications";
import {
  getSyncConfig, saveSyncConfig, syncNow,
  type SyncStatus, type SyncConfig,
} from "@/lib/sync";

/**
 * Settings → "Multi-device sync". Two operational panels:
 *
 *  1. Connect THIS device to a hub (the easy path for a secondary device):
 *     paste a hub URL + token, sync runs in the background.
 *  2. Let OTHER devices sync to THIS one (this device as a hub): surfaces this
 *     device's own pairing info from /remote-access so you can type it on the
 *     others. If this device is loopback-only it can't be a hub yet — we say so.
 *
 * The status block polls /sync/status (~10s) via useResource and mirrors the
 * card/badge/input idiom used by the other settings panels.
 */

/** Compact "key: value" line used across both panels. */
function StatLine({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11px] text-[var(--tt-fg-dim)]">{label}</span>
      <span className="text-[12px] text-[var(--tt-fg-muted)] tabular text-right truncate">{children}</span>
    </div>
  );
}

function ConnectToHub() {
  // Live status (polled). Separate from the editable form below so typing never
  // gets clobbered by a poll.
  const { data: status, refetch } = useResource<SyncStatus>("/sync/status", { pollMs: 10_000 });

  const [hubUrl, setHubUrl] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [hasToken, setHasToken] = useState(false);
  const [enabled, setEnabled] = useState(false);

  const [busy, setBusy] = useState<null | "connect" | "now" | "stop">(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Seed the form from persisted config once. The token is never echoed back
  // (has_token only), so we leave the token field empty and show a placeholder
  // when one is already stored.
  useEffect(() => {
    let cancelled = false;
    getSyncConfig()
      .then((c: SyncConfig) => {
        if (cancelled) return;
        setHubUrl(c.hub_url ?? "");
        setHasToken(c.has_token);
        setEnabled(c.enabled);
      })
      .catch(() => { /* empty state — nothing configured yet */ });
    return () => { cancelled = true; };
  }, []);

  const isOn = status?.enabled ?? enabled;

  const connect = async () => {
    if (!hubUrl.trim()) { setError("Enter your hub's URL first."); return; }
    if (!authToken.trim() && !hasToken) { setError("Paste the access token from your hub."); return; }
    setBusy("connect"); setError(null); setSaved(false);
    try {
      await saveSyncConfig({ hub_url: hubUrl.trim(), auth_token: authToken.trim(), enabled: true });
      setEnabled(true);
      if (authToken.trim()) { setHasToken(true); setAuthToken(""); }
      setSaved(true); setTimeout(() => setSaved(false), 2500);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save sync settings.");
    } finally {
      setBusy(null);
    }
  };

  const runNow = async () => {
    setBusy("now"); setError(null);
    try {
      await syncNow();
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed.");
    } finally {
      setBusy(null);
    }
  };

  const stop = async () => {
    setBusy("stop"); setError(null); setSaved(false);
    try {
      await saveSyncConfig({ hub_url: hubUrl.trim(), auth_token: "", enabled: false });
      setEnabled(false);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't stop syncing.");
    } finally {
      setBusy(null);
    }
  };

  const input =
    "w-full h-9 px-3 rounded-md bg-[var(--tt-sunken)] border border-[var(--tt-border-strong)] " +
    "text-[13px] text-[var(--tt-fg)] placeholder:text-[var(--tt-fg-dim)] " +
    "focus:outline-none focus:border-[var(--tt-border-focus)] transition-colors";

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <RefreshCw size={14} className="text-[var(--tt-brand)]" />
          Connect this device to your hub
        </CardTitle>
        <Badge variant={isOn ? "success" : "neutral"} size="sm">
          {isOn ? "Syncing" : "Off"}
        </Badge>
      </CardHeader>

      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-[10.5px] font-medium uppercase tracking-[0.1em] text-[var(--tt-fg-muted)] mb-1.5">
              Hub URL
            </label>
            <input
              value={hubUrl}
              onChange={(e) => setHubUrl(e.target.value)}
              placeholder="http://192.168.1.20:8000"
              spellCheck={false}
              autoCapitalize="none"
              className={input}
            />
          </div>
          <div>
            <label className="block text-[10.5px] font-medium uppercase tracking-[0.1em] text-[var(--tt-fg-muted)] mb-1.5">
              Access token
            </label>
            <input
              value={authToken}
              onChange={(e) => setAuthToken(e.target.value)}
              type="password"
              placeholder={hasToken ? "•••••••• (stored — leave blank to keep)" : "Paste token"}
              spellCheck={false}
              autoCapitalize="none"
              autoComplete="off"
              className={input}
            />
          </div>
        </div>

        <p className="text-[11px] text-[var(--tt-fg-dim)]">
          Open the hub device&apos;s dashboard → <span className="text-[var(--tt-fg-muted)]">&ldquo;Connect a device&rdquo;</span> to
          get the URL and token.
        </p>

        {error && <p className="text-[12px] text-[var(--tt-danger-fg)]">{error}</p>}

        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <Button variant="primary" onClick={connect} disabled={busy !== null}>
            {busy === "connect"
              ? <><Loader2 size={13} className="animate-spin" /> Connecting…</>
              : "Connect & sync"}
          </Button>
          <Button variant="secondary" onClick={runNow} disabled={busy !== null || !isOn}>
            {busy === "now"
              ? <><Loader2 size={13} className="animate-spin" /> Syncing…</>
              : "Sync now"}
          </Button>
          {isOn && (
            <Button variant="ghost" onClick={stop} disabled={busy !== null}>
              {busy === "stop"
                ? <><Loader2 size={13} className="animate-spin" /> Stopping…</>
                : "Stop syncing"}
            </Button>
          )}
          {saved && (
            <span className="flex items-center gap-1.5 text-[12px] text-[var(--tt-success-fg)]">
              <Check size={13} /> Saved
            </span>
          )}
        </div>

        {/* Status readout */}
        {status && (status.enabled || status.hub_url || status.last_push_at || status.last_pull_at) && (
          <div className="rounded-[var(--tt-radius)] border border-[var(--tt-border)] bg-[var(--tt-sunken)] px-4 py-3 space-y-1.5">
            {status.hub_url && <StatLine label="Hub">{status.hub_url}</StatLine>}
            <StatLine label="Last push">
              {status.last_push_at ? timeAgo(status.last_push_at) : "—"}
              {status.pushed_count > 0 && (
                <span className="text-[var(--tt-fg-dim)]"> · {status.pushed_count.toLocaleString()} sent</span>
              )}
            </StatLine>
            <StatLine label="Last pull">
              {status.last_pull_at ? timeAgo(status.last_pull_at) : "—"}
              {status.pulled_count > 0 && (
                <span className="text-[var(--tt-fg-dim)]"> · {status.pulled_count.toLocaleString()} received</span>
              )}
            </StatLine>
            {status.last_error && (
              <div className="flex items-start gap-1.5 pt-1.5 text-[11px] text-[var(--tt-warn-fg)]">
                <AlertTriangle size={12} className="mt-px shrink-0" />
                <span className="min-w-0">{status.last_error}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function ActAsHub() {
  const [info, setInfo] = useState<RemoteAccess | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [copied, setCopied] = useState<"" | "url" | "token">("");

  useEffect(() => {
    let cancelled = false;
    getRemoteAccess()
      .then((d) => { if (!cancelled) { setInfo(d); setLoaded(true); } })
      .catch(() => { if (!cancelled) { setInfo(null); setLoaded(true); } });
    return () => { cancelled = true; };
  }, []);

  // Don't render anything until we know — avoids flashing the "not reachable"
  // hint while the request is still in flight.
  if (!loaded) return null;

  const reachable = !!info?.enabled && !!info.url;

  const copy = (what: "url" | "token", text: string) => {
    navigator.clipboard?.writeText(text)
      .then(() => { setCopied(what); setTimeout(() => setCopied(""), 1500); })
      .catch(() => {});
  };

  const copyBtn =
    "inline-flex items-center gap-1.5 rounded-lg border border-[var(--tt-border)] " +
    "px-3 py-1.5 text-[12px] text-[var(--tt-fg)] hover:border-[var(--tt-border-focus)] " +
    "transition-colors cursor-pointer";

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Server size={14} className="text-[var(--tt-brand)]" />
          Let other devices sync to this one
        </CardTitle>
        <Badge variant={reachable ? "success" : "neutral"} size="sm">
          {reachable ? "Reachable" : "Loopback only"}
        </Badge>
      </CardHeader>

      {reachable ? (
        <div className="space-y-3">
          <p className="text-[12px] text-[var(--tt-fg-dim)]">
            Enter these on your other devices under{" "}
            <span className="text-[var(--tt-fg-muted)]">&ldquo;Connect this device to your hub&rdquo;</span>.
          </p>

          <div className="rounded-[var(--tt-radius)] border border-[var(--tt-border)] bg-[var(--tt-sunken)] px-4 py-3 space-y-3">
            <div>
              <div className="text-[10.5px] font-medium uppercase tracking-[0.1em] text-[var(--tt-fg-muted)] mb-1">
                Hub URL
              </div>
              <code className="block text-[12px] text-[var(--tt-fg)] font-mono break-all">{info!.url}</code>
            </div>
            {info!.token && (
              <div>
                <div className="text-[10.5px] font-medium uppercase tracking-[0.1em] text-[var(--tt-fg-muted)] mb-1">
                  Access token
                </div>
                <code className="block text-[12px] text-[var(--tt-fg)] font-mono break-all">
                  {showToken ? info!.token : "•".repeat(Math.min(info!.token.length, 32))}
                </code>
              </div>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <button type="button" className={copyBtn} onClick={() => copy("url", info!.url!)}>
              {copied === "url" ? <Check size={13} /> : <Copy size={13} />} Copy URL
            </button>
            {info!.token && (
              <>
                <button type="button" className={copyBtn} onClick={() => copy("token", info!.token!)}>
                  {copied === "token" ? <Check size={13} /> : <Copy size={13} />} Copy token
                </button>
                <button type="button" className={copyBtn} onClick={() => setShowToken((v) => !v)}>
                  {showToken ? <EyeOff size={13} /> : <Eye size={13} />} {showToken ? "Hide" : "Reveal"} token
                </button>
              </>
            )}
          </div>
        </div>
      ) : (
        <div className="flex items-start gap-2.5 rounded-[var(--tt-radius)] border border-[var(--tt-border)] bg-[var(--tt-sunken)] px-4 py-3">
          <Wifi size={14} className="mt-0.5 shrink-0 text-[var(--tt-fg-dim)]" />
          <p className="text-[12px] leading-relaxed text-[var(--tt-fg-dim)]">
            This device isn&apos;t reachable by others yet — restart it bound to your LAN/Tailscale
            address with an access token to act as a hub.
          </p>
        </div>
      )}
    </Card>
  );
}

export default function MultiDeviceSyncSettings() {
  return (
    <div className="space-y-4">
      <ConnectToHub />
      <ActAsHub />
    </div>
  );
}
