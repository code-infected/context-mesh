interface ToolBudgetConfig {
  [toolName: string]: number;
}

interface Config {
  defaultBudgetTokens: number;
  maxOverheadMs: number;
  toolBudgets: ToolBudgetConfig;
  sessionTimeoutMinutes: number;
  /** Cumulative compressed-token budget per session; 0 disables adaptive scaling. */
  sessionTokenLimit: number;
  /** Floor for the adaptive budget scale factor (0..1). */
  adaptiveMinFactor: number;
  logLevel: string;
  budgetForTool: (toolName: string) => number;
}

const DEFAULT_CONFIG: Omit<Config, "budgetForTool"> = {
  defaultBudgetTokens: 8000,
  maxOverheadMs: 80,
  toolBudgets: {
    read_file: 6000,
    web_scrape: 4000,
    run_shell: 8000,
    query_database: 5000,
    search_codebase: 6000,
  },
  sessionTimeoutMinutes: 60,
  sessionTokenLimit: 0,
  adaptiveMinFactor: 0.25,
  logLevel: "info",
};

function loadToolBudgetsFromEnv(): ToolBudgetConfig {
  // CONTEXTMESH_TOOL_BUDGETS='{"read_file": 6000, "web_scrape": 4000}'
  const raw = process.env.CONTEXTMESH_TOOL_BUDGETS;
  if (!raw) return { ...DEFAULT_CONFIG.toolBudgets };
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const budgets: ToolBudgetConfig = { ...DEFAULT_CONFIG.toolBudgets };
    for (const [tool, value] of Object.entries(parsed)) {
      const budget = Number(value);
      if (Number.isFinite(budget) && budget > 0) {
        budgets[tool] = budget;
      }
    }
    return budgets;
  } catch (error) {
    console.error("Invalid CONTEXTMESH_TOOL_BUDGETS JSON; using defaults:", error);
    return { ...DEFAULT_CONFIG.toolBudgets };
  }
}

function loadAdaptiveMinFactorFromEnv(): number {
  // CONTEXTMESH_ADAPTIVE_MIN_FACTOR='0.25'
  const raw = process.env.CONTEXTMESH_ADAPTIVE_MIN_FACTOR;
  if (!raw) return DEFAULT_CONFIG.adaptiveMinFactor;
  const parsed = parseFloat(raw);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    console.error("Invalid CONTEXTMESH_ADAPTIVE_MIN_FACTOR (want 0..1); using default:", raw);
    return DEFAULT_CONFIG.adaptiveMinFactor;
  }
  return parsed;
}

function loadConfigFromEnv(): Omit<Config, "budgetForTool"> {
  return {
    defaultBudgetTokens: parseInt(process.env.CONTEXTMESH_DEFAULT_BUDGET_TOKENS || "") || DEFAULT_CONFIG.defaultBudgetTokens,
    maxOverheadMs: parseInt(process.env.CONTEXTMESH_MAX_OVERHEAD_MS || "") || DEFAULT_CONFIG.maxOverheadMs,
    toolBudgets: loadToolBudgetsFromEnv(),
    sessionTimeoutMinutes: parseInt(process.env.CONTEXTMESH_SESSION_TIMEOUT_MINUTES || "") || DEFAULT_CONFIG.sessionTimeoutMinutes,
    sessionTokenLimit: parseInt(process.env.CONTEXTMESH_SESSION_TOKEN_LIMIT || "") || DEFAULT_CONFIG.sessionTokenLimit,
    adaptiveMinFactor: loadAdaptiveMinFactorFromEnv(),
    logLevel: process.env.CONTEXTMESH_LOG_LEVEL || DEFAULT_CONFIG.logLevel,
  };
}

const baseConfig = loadConfigFromEnv();

export const config: Config = {
  ...baseConfig,
  budgetForTool: (toolName: string): number => {
    return baseConfig.toolBudgets[toolName] || baseConfig.defaultBudgetTokens;
  },
};
