// Telemetry was removed from this build (privacy: nothing is collected or sent).
// This component is intentionally inert — it renders nothing and emits no events.
// It is kept as a no-op so its mount point in LayoutWrapper stays valid without a
// wider refactor; it can be deleted entirely in a future cleanup.
export default function TelemetryNotice() {
  return null;
}
