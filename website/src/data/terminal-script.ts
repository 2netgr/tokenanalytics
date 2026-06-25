export type ScriptLine = {
  delay: number;
  kind: "user" | "tool" | "reasoning" | "result" | "cost" | "header";
  text: string;
};

export const TERMINAL_SCRIPT: ScriptLine[] = [
  { delay: 0,    kind: "header",    text: "session:claude · model:claude-opus-4-7 · cwd:~/projects/api" },
  { delay: 350,  kind: "user",      text: "› refactor the auth middleware to use JWT" },
  { delay: 900,  kind: "reasoning", text: "thinking · scan auth/, find current strategy …" },
  { delay: 1500, kind: "tool",      text: "→ Read auth/middleware.py" },
  { delay: 1750, kind: "result",    text: "  204 lines · session-cookie based" },
  { delay: 2200, kind: "tool",      text: "→ Grep \"session_token\" --include=*.py" },
  { delay: 2500, kind: "result",    text: "  17 matches across 6 files" },
  { delay: 3000, kind: "reasoning", text: "thinking · plan migration · keep cookies as fallback …" },
  { delay: 3700, kind: "tool",      text: "→ Edit auth/middleware.py" },
  { delay: 4100, kind: "result",    text: "  +47 -12 · jwt.encode imported" },
  { delay: 4600, kind: "cost",      text: "tokens 12,440 · cached 8,210 · cost $0.18" },
  { delay: 5300, kind: "user",      text: "› perfect, write tests" },
];

export const SCRIPT_DURATION_MS = 7000;
