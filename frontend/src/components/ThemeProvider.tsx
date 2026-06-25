"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const STORAGE_KEY = "tt-theme";

interface ThemeCtx {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Always start with "dark" so server and first client render agree.
  // The no-flash script has already set <html data-theme="…"> for paint;
  // we sync React state to match in a useEffect after mount.
  const [theme, setThemeState] = useState<Theme>("dark");

  const apply = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem(STORAGE_KEY, t); } catch {}
    setThemeState(t);
  }, []);

  /* Always enforce dark on mount */
  useEffect(() => { apply("dark"); }, [apply]);

  const value: ThemeCtx = {
    theme,
    setTheme: () => {},
    toggleTheme: () => {},
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme must be used inside <ThemeProvider>");
  return v;
}

/** Inline script — runs before paint to set data-theme so there's no FOUC. */
export const NO_FLASH_SCRIPT = `document.documentElement.setAttribute('data-theme','dark');`;
