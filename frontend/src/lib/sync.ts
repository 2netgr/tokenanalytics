import { api } from "@/lib/api";

/**
 * Multi-device sync. A secondary device pushes/pulls its sessions to/from a
 * "hub" device over the LAN/tailnet. The hub is just another TokenAnalytics
 * instance that was started bound to a reachable address with an access token
 * (see GET /remote-access — that endpoint describes THIS device's own pairing
 * info when it can act as a hub).
 */

/** Live sync state — what the background sync loop last did. Polled. */
export interface SyncStatus {
  enabled: boolean;
  hub_url: string;
  last_push_at: string | null;
  last_pull_at: string | null;
  pushed_count: number;
  pulled_count: number;
  last_error: string | null;
}

/** Persisted sync configuration. `has_token` avoids ever echoing the secret. */
export interface SyncConfig {
  enabled: boolean;
  hub_url: string;
  interval: number;
  has_token: boolean;
}

/** Body for POST /sync/config. `interval` is optional (backend keeps its own). */
export interface SyncConfigInput {
  hub_url: string;
  auth_token: string;
  enabled: boolean;
  interval?: number;
}

export function getSyncStatus(): Promise<SyncStatus> {
  return api<SyncStatus>("/sync/status");
}

export function getSyncConfig(): Promise<SyncConfig> {
  return api<SyncConfig>("/sync/config");
}

export function saveSyncConfig(input: SyncConfigInput): Promise<{ ok: true }> {
  return api("/sync/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

/** Trigger one sync cycle now; returns the fresh status it produced. */
export function syncNow(): Promise<SyncStatus> {
  return api<SyncStatus>("/sync/now", { method: "POST" });
}
