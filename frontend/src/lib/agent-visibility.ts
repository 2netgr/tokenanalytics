import type { AgentKey } from "./agents";

export const AGENTS_HIDDEN_BY_DEFAULT: AgentKey[] = ["copilot", "antigravity", "gemini"];

export function isAgentVisible(agent: AgentKey): boolean {
  if (typeof window === "undefined") return !AGENTS_HIDDEN_BY_DEFAULT.includes(agent);
  const stored = localStorage.getItem(`tt-show-agent-${agent}`);
  return stored === null ? !AGENTS_HIDDEN_BY_DEFAULT.includes(agent) : stored === "true";
}

export function setAgentVisible(agent: AgentKey, visible: boolean): void {
  localStorage.setItem(`tt-show-agent-${agent}`, String(visible));
  window.dispatchEvent(new Event("storage"));
}

export function isLocalModelsPageVisible(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem("tt-show-local-models") === "true";
}

export function setLocalModelsPageVisible(visible: boolean): void {
  localStorage.setItem("tt-show-local-models", String(visible));
  window.dispatchEvent(new Event("storage"));
}
