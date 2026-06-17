interface ToolBudgetConfig {
  [toolName: string]: number;
}

interface Config {
  defaultBudgetTokens: number;
  maxOverheadMs: number;
  toolBudgets: ToolBudgetConfig;
  sessionTimeoutMinutes: number;
  logLevel: string;
}

const DEFAULT_CONFIG: Config = {
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
  logLevel: "info",
};

function loadConfigFromEnv(): Config {
  return {
    defaultBudgetTokens: parseInt(process.env.CONTEXTMESH_DEFAULT_BUDGET_TOKENS || "") || DEFAULT_CONFIG.defaultBudgetTokens,
    maxOverheadMs: parseInt(process.env.CONTEXTMESH_MAX_OVERHEAD_MS || "") || DEFAULT_CONFIG.maxOverheadMs,
    toolBudgets: DEFAULT_CONFIG.toolBudgets,
    sessionTimeoutMinutes: parseInt(process.env.CONTEXTMESH_SESSION_TIMEOUT_MINUTES || "") || DEFAULT_CONFIG.sessionTimeoutMinutes,
    logLevel: process.env.CONTEXTMESH_LOG_LEVEL || DEFAULT_CONFIG.logLevel,
  };
}

export const config: Config = loadConfigFromEnv();

config.budgetForTool = function(toolName: string): number {
  return config.toolBudgets[toolName] || config.defaultBudgetTokens;
};
