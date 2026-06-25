"use client";

import { useEffect, useRef, useState } from "react";
import { Laptop, Layers, Monitor, Check, RefreshCw, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { useResource } from "@/lib/api";

/* The device a view is scoped to. "local" is the default and keeps the page
   byte-for-byte identical to single-device behaviour; "all" merges every known
   device; anything else is a concrete device_id. */
export type DeviceSelection = "local" | "all" | (string & {});

interface LocalDevice {
  device_id: string;
  device_name: string;
  device_role: string;
  source_origin: string;
  last_seen_at: string | null;
  is_local: true;
}

interface RemoteDevice {
  device_id: string;
  device_name: string;
  device_role: string;
  source_origin: string;
  last_seen_at: string | null;
  session_count: number;
}

interface DevicesResponse {
  local: LocalDevice;
  devices: RemoteDevice[];
}

interface DeviceSelectorProps {
  value: DeviceSelection;
  onChange: (value: DeviceSelection) => void;
  className?: string;
}

/* Compact device scope picker for the Analytics page and Dashboard. Fetches
   /devices, offers Local / All devices / each concrete device by name, and
   surfaces a subtle "synced" marker whenever a non-local scope is active so the
   operator can tell at a glance the numbers aren't from this box alone. The
   /devices endpoint is loopback-friendly; if it errors (e.g. an older backend)
   the control collapses to a static "Local" chip and never blocks the page. */
export default function DeviceSelector({ value, onChange, className }: DeviceSelectorProps) {
  const { data, error } = useResource<DevicesResponse>("/devices", { pollMs: 60_000 });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape — standard menu dismissal.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const remote = data?.devices ?? [];
  // A backend without device data (or an error) leaves nothing to switch to;
  // render an inert "Local" label so the toolbar stays visually consistent.
  const hasChoices = !error && remote.length > 0;
  const synced = value !== "local";

  const activeLabel = (() => {
    if (value === "local") return data?.local?.device_name || "This device";
    if (value === "all") return "All devices";
    const d = remote.find((r) => r.device_id === value);
    return d?.device_name || "Unknown device";
  })();

  if (!hasChoices) {
    return (
      <div className={cn("flex items-center gap-2", className)}>
        <span className="text-[10px] uppercase tracking-[0.16em] text-[var(--tt-fg-dim)]">Device</span>
        <span className="flex items-center gap-1.5 px-2 py-1 text-[11px] text-[var(--tt-fg-muted)] bg-[var(--tt-sunken)] border border-[var(--tt-border)] rounded-[var(--tt-radius)]">
          <Laptop size={12} className="text-[var(--tt-fg-dim)]" />
          {data?.local?.device_name || "Local"}
        </span>
      </div>
    );
  }

  const pick = (next: DeviceSelection) => { onChange(next); setOpen(false); };

  return (
    <div ref={ref} className={cn("relative flex items-center gap-2", className)}>
      <span className="text-[10px] uppercase tracking-[0.16em] text-[var(--tt-fg-dim)]">Device</span>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex items-center gap-1.5 px-2 py-1 text-[11px] rounded-[var(--tt-radius)] border transition-colors",
          synced
            ? "border-[var(--tt-brand)] text-[var(--tt-fg)] bg-[var(--tt-brand)]/10"
            : "border-[var(--tt-border)] text-[var(--tt-fg-muted)] bg-[var(--tt-sunken)] hover:text-[var(--tt-fg)]"
        )}
      >
        {value === "all" ? (
          <Layers size={12} className={synced ? "text-[var(--tt-brand)]" : "text-[var(--tt-fg-dim)]"} />
        ) : value === "local" ? (
          <Laptop size={12} className="text-[var(--tt-fg-dim)]" />
        ) : (
          <Monitor size={12} className="text-[var(--tt-brand)]" />
        )}
        <span className="max-w-[140px] truncate">{activeLabel}</span>
        {synced && <RefreshCw size={10} className="text-[var(--tt-brand)]" aria-label="synced" />}
        <ChevronDown size={12} className={cn("text-[var(--tt-fg-dim)] transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute top-full left-0 mt-1.5 z-30 min-w-[200px] max-h-[300px] overflow-y-auto rounded-[var(--tt-radius)] border border-[var(--tt-border-strong)] bg-[var(--tt-overlay)] shadow-lg py-1"
        >
          <DeviceOption
            icon={<Laptop size={13} />}
            label={data?.local?.device_name || "This device"}
            hint="local"
            selected={value === "local"}
            onClick={() => pick("local")}
          />
          <DeviceOption
            icon={<Layers size={13} />}
            label="All devices"
            hint={`${remote.length + 1} total`}
            selected={value === "all"}
            onClick={() => pick("all")}
          />
          {remote.length > 0 && (
            <div className="my-1 mx-2 border-t border-[var(--tt-border)]" />
          )}
          {remote.map((d) => (
            <DeviceOption
              key={d.device_id}
              icon={<Monitor size={13} />}
              label={d.device_name}
              hint={`${d.session_count} session${d.session_count === 1 ? "" : "s"}`}
              selected={value === d.device_id}
              onClick={() => pick(d.device_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DeviceOption({
  icon, label, hint, selected, onClick,
}: {
  icon: React.ReactNode;
  label: string;
  hint?: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors",
        selected ? "text-[var(--tt-fg)] bg-[var(--tt-brand)]/10" : "text-[var(--tt-fg-muted)] hover:bg-[var(--tt-sunken)] hover:text-[var(--tt-fg)]"
      )}
    >
      <span className={cn("shrink-0", selected ? "text-[var(--tt-brand)]" : "text-[var(--tt-fg-dim)]")}>{icon}</span>
      <span className="flex-1 min-w-0 truncate">{label}</span>
      {hint && <span className="shrink-0 text-[10px] tabular text-[var(--tt-fg-dim)]">{hint}</span>}
      {selected && <Check size={13} className="shrink-0 text-[var(--tt-brand)]" />}
    </button>
  );
}
