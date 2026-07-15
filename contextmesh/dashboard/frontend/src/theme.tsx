/**
 * Theme context: light/dark mode plus the concrete chart palette for the
 * active mode. Recharts needs literal color values (SVG presentation
 * attributes cannot read CSS variables reliably), so chart series colors are
 * exposed here per mode; UI chrome uses the CSS variables in index.css.
 *
 * Palette values come from the validated reference palette (dataviz skill):
 * both modes were run through the six-check validator.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type ThemeMode = "light" | "dark";

export interface ChartPalette {
  surface: string;
  page: string;
  inkPrimary: string;
  inkSecondary: string;
  inkMuted: string;
  grid: string;
  axisLine: string;
  /** Categorical slot 1 (blue) — the default series / accent hue. */
  series1: string;
  /** Categorical slot 2 (aqua) — second distinct series where needed. */
  series2: string;
  /** One-hue two-shade ramp for before/after pairs: "before" (original). */
  tokenBefore: string;
  /** One-hue two-shade ramp for before/after pairs: "after" (compressed). */
  tokenAfter: string;
  /** Unfilled meter track — a lighter step of the accent's own ramp. */
  meterTrack: string;
  /** Neutral wash used as the bar-hover cursor fill. */
  hoverWash: string;
  /* Status palette — fixed, reserved meaning, always paired with icon+label. */
  good: string;
  warning: string;
  serious: string;
  critical: string;
  deltaGood: string;
}

const palettes: Record<ThemeMode, ChartPalette> = {
  light: {
    surface: "#fcfcfb",
    page: "#f9f9f7",
    inkPrimary: "#0b0b0b",
    inkSecondary: "#52514e",
    inkMuted: "#898781",
    grid: "#e1e0d9",
    axisLine: "#c3c2b7",
    series1: "#2a78d6",
    series2: "#1baf7a",
    tokenBefore: "#86b6ef", // blue-250 (validated ordinal light end, 2.06:1)
    tokenAfter: "#2a78d6", // blue-450
    meterTrack: "#b7d3f6", // blue-150
    hoverWash: "rgba(11, 11, 11, 0.05)",
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
    deltaGood: "#006300",
  },
  dark: {
    surface: "#1a1a19",
    page: "#0d0d0d",
    inkPrimary: "#ffffff",
    inkSecondary: "#c3c2b7",
    inkMuted: "#898781",
    grid: "#2c2c2a",
    axisLine: "#383835",
    series1: "#3987e5",
    series2: "#199e70",
    tokenBefore: "#184f95", // blue-600 (validated ordinal dark end, 2.15:1)
    tokenAfter: "#3987e5", // blue-400
    meterTrack: "#104281", // blue-650 — recessive track on the dark surface
    hoverWash: "rgba(255, 255, 255, 0.06)",
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
    deltaGood: "#0ca30c",
  },
};

interface ThemeContextValue {
  mode: ThemeMode;
  palette: ChartPalette;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: "light",
  palette: palettes.light,
  toggle: () => undefined,
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );

  useEffect(() => {
    document.documentElement.classList.toggle("dark", mode === "dark");
    try {
      localStorage.setItem("cm-theme", mode);
    } catch {
      /* localStorage unavailable — theme just won't persist */
    }
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((m) => (m === "light" ? "dark" : "light"));
  }, []);

  return (
    <ThemeContext.Provider value={{ mode, palette: palettes[mode], toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
