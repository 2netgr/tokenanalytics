"use client";

import { useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/ui";
import {
  isAgentVisible, setAgentVisible,
  isLocalModelsPageVisible, setLocalModelsPageVisible,
  AGENTS_HIDDEN_BY_DEFAULT,
} from "@/lib/agent-visibility";
import { AGENTS, type AgentKey } from "@/lib/agents";

const CONFIGURABLE_AGENTS: AgentKey[] = ["copilot", "antigravity", "gemini"];

function Toggle({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      role="switch"
      aria-checked={enabled}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors mt-0.5 border-[var(--tt-border)] cursor-pointer ${enabled ? "tt-tint-1" : ""}`}
    >
      <span
        className={`absolute h-3.5 w-3.5 rounded-full transition-transform ${enabled ? "translate-x-[18px] bg-[var(--tt-brand)]" : "translate-x-0.5 bg-[var(--tt-fg-muted)]"}`}
      />
    </button>
  );
}

export default function AgentVisibilitySettings() {
  const [agentVisible, setAgentVisibleState] = useState<Record<AgentKey, boolean>>(
    () => Object.fromEntries(CONFIGURABLE_AGENTS.map(k => [k, !AGENTS_HIDDEN_BY_DEFAULT.includes(k)])) as Record<AgentKey, boolean>
  );
  const [showLocalModels, setShowLocalModelsState] = useState(false);

  useEffect(() => {
    const sync = () => {
      setAgentVisibleState(
        Object.fromEntries(CONFIGURABLE_AGENTS.map(k => [k, isAgentVisible(k)])) as Record<AgentKey, boolean>
      );
      setShowLocalModelsState(isLocalModelsPageVisible());
    };
    sync();
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  return (
    <Card padding="none">
      {CONFIGURABLE_AGENTS.map((key) => {
        const meta = AGENTS[key];
        return (
          <div
            key={key}
            className="flex items-center justify-between gap-4 px-5 py-4 border-b border-[var(--tt-border)] last:border-0"
          >
            <div className="flex items-center gap-3">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: meta.hex }}
              />
              <div>
                <CardTitle className="mb-0.5 text-[13px]">{meta.label}</CardTitle>
                <p className="text-[12px] text-[var(--tt-fg-dim)]">
                  Show {meta.label} sessions and agent card on the dashboard.
                </p>
              </div>
            </div>
            <Toggle
              enabled={agentVisible[key] ?? false}
              onToggle={() => {
                const next = !agentVisible[key];
                setAgentVisible(key, next);
                setAgentVisibleState(prev => ({ ...prev, [key]: next }));
              }}
            />
          </div>
        );
      })}

      <div className="flex items-center justify-between gap-4 px-5 py-4">
        <div>
          <CardTitle className="mb-0.5 text-[13px]">Local Models page</CardTitle>
          <p className="text-[12px] text-[var(--tt-fg-dim)]">
            Show the Local Models page in the sidebar navigation.
          </p>
        </div>
        <Toggle
          enabled={showLocalModels}
          onToggle={() => {
            const next = !showLocalModels;
            setLocalModelsPageVisible(next);
            setShowLocalModelsState(next);
          }}
        />
      </div>
    </Card>
  );
}
