import { useState, type ReactNode } from "react";
import { api } from "./api";
import {
  AlertTriangleIcon,
  BarsIcon,
  GridIcon,
  HistoryIcon,
  MoonIcon,
  SunIcon,
  ZapIcon,
} from "./components/icons";
import { compactNumber } from "./format";
import { useApi, useInterval } from "./hooks";
import { useTheme } from "./theme";
import { FailureAnalysis } from "./views/FailureAnalysis";
import { GuidelineHistory } from "./views/GuidelineHistory";
import { Playground } from "./views/Playground";
import { SessionOverview } from "./views/SessionOverview";
import { ToolBreakdown } from "./views/ToolBreakdown";

type ViewId = "overview" | "tools" | "failures" | "guidelines" | "playground";

interface NavItem {
  id: ViewId;
  label: string;
  description: string;
  icon: ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  {
    id: "overview",
    label: "Session overview",
    description: "Sessions, token savings, and compression totals",
    icon: <GridIcon size={15} />,
  },
  {
    id: "tools",
    label: "Tool breakdown",
    description: "Per-tool compression ratios and chunk selection",
    icon: <BarsIcon size={15} />,
  },
  {
    id: "failures",
    label: "Failure analysis",
    description: "Tasks where compression was implicated, with diffs",
    icon: <AlertTriangleIcon size={15} />,
  },
  {
    id: "guidelines",
    label: "Guideline history",
    description: "ACON multiplier updates and current guidelines",
    icon: <HistoryIcon size={15} />,
  },
  {
    id: "playground",
    label: "Playground",
    description: "Try compression interactively on any tool output",
    icon: <ZapIcon size={15} />,
  },
];

/** Health pill: status dot + label (never color alone). Polls every 30s. */
function HealthPill() {
  const { palette } = useTheme();
  const { data, error, reload } = useApi(api.health);
  useInterval(reload, 30_000);

  const healthy = !error && data !== null;
  return (
    <span className="flex items-center gap-1.5 rounded-full border border-hairline bg-surface px-2.5 py-1 text-xs text-ink-2">
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: healthy ? palette.good : palette.critical }}
      />
      {healthy
        ? `API ${data.status} · ${compactNumber(data.traces_stored ?? 0)} traces`
        : "API offline"}
    </span>
  );
}

export default function App() {
  const [view, setView] = useState<ViewId>("overview");
  const { mode, toggle } = useTheme();
  const active = NAV_ITEMS.find((n) => n.id === view) ?? NAV_ITEMS[0];

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-hairline bg-surface">
        <div className="border-b border-hairline px-4 py-4">
          <div className="text-sm font-semibold tracking-tight text-ink">ContextMesh</div>
          <div className="mt-0.5 text-[11px] text-ink-3">Compression observability</div>
        </div>
        <nav className="flex-1 space-y-1 p-2">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setView(item.id)}
              className={
                view === item.id
                  ? "flex w-full items-center gap-2.5 rounded-md bg-accent-soft px-3 py-2 text-left text-xs font-semibold text-ink"
                  : "flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-xs font-medium text-ink-2 hover:bg-accent-soft hover:text-ink"
              }
            >
              <span className={view === item.id ? "text-accent" : "text-ink-3"}>
                {item.icon}
              </span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="border-t border-hairline p-3 text-[11px] leading-relaxed text-ink-3">
          MCP-native context compression middleware
        </div>
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-hairline bg-surface px-6 py-3">
          <div>
            <h1 className="text-base font-semibold text-ink">{active.label}</h1>
            <p className="text-xs text-ink-2">{active.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <HealthPill />
            <button
              type="button"
              onClick={toggle}
              aria-label={mode === "light" ? "Switch to dark mode" : "Switch to light mode"}
              className="rounded-md border border-hairline bg-surface p-1.5 text-ink-2 hover:bg-accent-soft hover:text-ink"
            >
              {mode === "light" ? <MoonIcon size={15} /> : <SunIcon size={15} />}
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto bg-page p-6">
          {view === "overview" && <SessionOverview />}
          {view === "tools" && <ToolBreakdown />}
          {view === "failures" && <FailureAnalysis />}
          {view === "guidelines" && <GuidelineHistory />}
          {view === "playground" && <Playground />}
        </main>
      </div>
    </div>
  );
}
