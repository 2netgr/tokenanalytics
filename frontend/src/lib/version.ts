"use client";

import { api } from "./api";

/** One curated bullet inside a release. */
export interface UpdateHighlight {
  title: string;
  description: string | null;
  /** Internal route (`/settings`) or external URL. */
  href: string | null;
}

/** A single release entry — `tag`/`title` are optional for legacy data. */
export interface UpdateRelease {
  tag: string | null;
  title: string | null;
  highlights: UpdateHighlight[];
}

/** Response shape from `GET /version`. */
export interface VersionInfo {
  current: string | null;
  latest: string | null;
  behind: boolean;
  /** Newest first. Empty when UPDATE.json is missing / unreachable. */
  releases: UpdateRelease[];
  /**
   * Stable id of the newest curated release ("tag|title"), or null when there
   * are no curated highlights. The banner keys dismissal on this so it only
   * re-surfaces when a NEW feature release lands — not on every main commit.
   */
  latest_release: string | null;
  release_url: string;
  source: "github" | "cache" | "offline" | "disabled" | "none";
  repo: string;
  /**
   * True for a bundled build (.dmg/.app/installer). These can't self-update via
   * git-pull, so the banner links to the download instead of applying in place.
   */
  packaged: boolean;
  /** Direct download for the newest release (packaged builds only). */
  download_url: string | null;
}

export const getVersion = () => api<VersionInfo>("/version");

/** Result of the one-click self-update (`POST /update/apply`). */
export interface UpdateResult {
  ok: boolean;
  updated: boolean;
  from: string | null;
  to: string | null;
  detail: string;
  /** True when the app pulled new code and is restarting itself. */
  restarting: boolean;
}

/** Pull the latest code and restart. Loopback-only on the backend. */
export const applyUpdate = () =>
  api<UpdateResult>("/update/apply", { method: "POST" });

/** State of the update-check preference (`GET/POST /config/update-check`). */
export interface UpdateCheckState {
  /** The saved preference (what the toggle reflects). */
  enabled: boolean;
  /** True when TT_NO_UPDATE_CHECK is set — toggle is read-only (policy override). */
  env_forced_off: boolean;
  /** What actually happens: enabled && !env_forced_off. */
  effective: boolean;
}

export const getUpdateCheck = () => api<UpdateCheckState>("/config/update-check");

export const setUpdateCheck = (enabled: boolean) =>
  api<UpdateCheckState>("/config/update-check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
