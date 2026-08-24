#!/usr/bin/env node

import { spawn } from "node:child_process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const DEFAULT_TIMEOUT_MS = 15_000;

function parseArgs(argv) {
  const options = {
    command: "status",
    codexBin: process.env.CODEX_BIN || "codex",
    json: false,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  };

  const args = argv.slice(2);
  if (args[0] && !args[0].startsWith("-")) options.command = args.shift();
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--json") options.json = true;
    else if (arg === "--codex-bin") options.codexBin = args[++index];
    else if (arg === "--timeout-ms") options.timeoutMs = Number(args[++index]);
    else if (arg === "--help" || arg === "-h") options.command = "help";
    else throw new Error(`Unknown option: ${arg}`);
  }

  if (!["status", "plan", "usage", "all", "help"].includes(options.command)) {
    throw new Error(`Unknown command: ${options.command}`);
  }
  if (!options.codexBin) throw new Error("--codex-bin requires a path");
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs < 1000) {
    throw new Error("--timeout-ms must be at least 1000");
  }
  return options;
}

export class AppServerClient {
  constructor({ codexBin, timeoutMs }) {
    this.codexBin = codexBin;
    this.timeoutMs = timeoutMs;
    this.nextId = 1;
    this.pending = new Map();
    this.stderr = "";
  }

  async start() {
    this.child = spawn(this.codexBin, ["app-server"], {
      stdio: ["pipe", "pipe", "pipe"],
      env: process.env,
    });
    this.child.stderr.setEncoding("utf8");
    this.child.stderr.on("data", (chunk) => {
      this.stderr = `${this.stderr}${chunk}`.slice(-4000);
    });
    this.child.on("error", (error) => this.rejectAll(error));
    this.child.on("exit", (code, signal) => {
      if (this.pending.size > 0) {
        const detail = this.stderr.trim();
        this.rejectAll(new Error(`Codex App Server exited (${code ?? signal ?? "unknown"})${detail ? `: ${detail}` : ""}`));
      }
    });

    this.lines = readline.createInterface({ input: this.child.stdout });
    this.lines.on("line", (line) => this.handleLine(line));

    await this.request("initialize", {
      clientInfo: {
        name: "codex_quota_insights",
        title: "Codex Quota Insights",
        version: "0.1.0",
      },
    });
    this.send({ method: "initialized", params: {} });
  }

  handleLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    if (message.id === undefined || !this.pending.has(message.id)) return;
    const pending = this.pending.get(message.id);
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (message.error) pending.reject(new Error(message.error.message || JSON.stringify(message.error)));
    else pending.resolve(message.result);
  }

  send(message) {
    this.child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  request(method, params) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ method, id, ...(params === undefined ? {} : { params }) });
    });
  }

  rejectAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  close() {
    this.lines?.close();
    this.child?.stdin.end();
    this.child?.kill();
  }
}

function collectWindows(result) {
  const buckets = result?.rateLimitsByLimitId
    ? Object.values(result.rateLimitsByLimitId)
    : result?.rateLimits
      ? [result.rateLimits]
      : [];
  const windows = [];
  for (const bucket of buckets) {
    for (const kind of ["primary", "secondary"]) {
      const window = bucket?.[kind];
      if (!window) continue;
      const usedPercent = Number(window.usedPercent);
      const windowDurationMins = Number(window.windowDurationMins);
      const resetsAt = Number(window.resetsAt);
      if (![usedPercent, windowDurationMins, resetsAt].every(Number.isFinite)) continue;
      windows.push({
        limitId: bucket.limitId || "codex",
        limitName: bucket.limitName || null,
        kind,
        usedPercent,
        windowDurationMins,
        resetsAt,
        planType: window.planType || bucket.planType || null,
      });
    }
  }
  return windows;
}

export function selectQuotaWindow(result) {
  const windows = collectWindows(result);
  if (windows.length === 0) throw new Error("Codex returned no usable rate-limit window");
  const codexWindows = windows.filter((window) => window.limitId === "codex");
  const candidates = codexWindows.length > 0 ? codexWindows : windows;
  return candidates.sort((left, right) => right.windowDurationMins - left.windowDurationMins)[0];
}

function startOfLocalDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function localDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function buildBudgetPlan(window, now = new Date()) {
  const reset = new Date(window.resetsAt * 1000);
  const cycleStart = new Date(reset.getTime() - window.windowDurationMins * 60_000);
  const weightedDays = [];

  for (let cursor = startOfLocalDay(cycleStart); cursor < reset;) {
    const next = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 1);
    const overlapStart = Math.max(cursor.getTime(), cycleStart.getTime());
    const overlapEnd = Math.min(next.getTime(), reset.getTime());
    const overlapFraction = Math.max(0, overlapEnd - overlapStart) / (next.getTime() - cursor.getTime());
    if (overlapFraction > 0) {
      const weekend = cursor.getDay() === 0 || cursor.getDay() === 6;
      weightedDays.push({
        date: localDateKey(cursor),
        weekday: cursor.getDay(),
        weekend,
        periodStartIso: new Date(overlapStart).toISOString(),
        periodEndIso: new Date(overlapEnd).toISOString(),
        weight: (weekend ? 1 : 2) * overlapFraction,
      });
    }
    cursor = next;
  }

  const totalWeight = weightedDays.reduce((sum, day) => sum + day.weight, 0);
  if (totalWeight <= 0) throw new Error("Cannot build a budget plan for this quota window");
  const raw = weightedDays.map((day) => ({ ...day, rawBudget: 100 * day.weight / totalWeight }));
  let remainder = 100 - raw.reduce((sum, day) => sum + Math.floor(day.rawBudget), 0);
  const remainderOrder = raw
    .map((day, index) => ({ index, fraction: day.rawBudget - Math.floor(day.rawBudget) }))
    .sort((left, right) => right.fraction - left.fraction || left.index - right.index);
  const bonus = new Set(remainderOrder.slice(0, remainder).map((item) => item.index));

  let plannedRemaining = 100;
  const days = raw.map((day, index) => {
    const budgetPercent = Math.floor(day.rawBudget) + (bonus.has(index) ? 1 : 0);
    plannedRemaining -= budgetPercent;
    return {
      date: day.date,
      weekend: day.weekend,
      periodStartIso: day.periodStartIso,
      periodEndIso: day.periodEndIso,
      budgetPercent,
      plannedRemainingAfterDayPercent: plannedRemaining,
    };
  });

  const todayKey = localDateKey(now);
  return {
    baselinePercent: 100,
    ratio: { weekday: 2, weekend: 1 },
    cycleStartIso: cycleStart.toISOString(),
    resetsAtIso: reset.toISOString(),
    days,
    today: days.find((day) => day.date === todayKey) || null,
  };
}

export function buildStatus(rateLimitsResult, now = new Date()) {
  const window = selectQuotaWindow(rateLimitsResult);
  const remainingPercent = Math.max(0, Math.min(100, 100 - window.usedPercent));
  const alert = remainingPercent <= 0
    ? { level: "critical", message: "Quota exhausted" }
    : remainingPercent <= 1
      ? { level: "critical", message: "Only 1% quota remains" }
      : remainingPercent <= 5
        ? { level: "warning", message: "Only 5% quota remains" }
        : { level: "healthy", message: "Quota is healthy" };
  return {
    source: "codex-app-server",
    limitId: window.limitId,
    windowKind: window.kind,
    windowDurationMins: window.windowDurationMins,
    usedPercent: window.usedPercent,
    remainingPercent,
    resetsAtIso: new Date(window.resetsAt * 1000).toISOString(),
    planType: window.planType,
    alert,
    budget: buildBudgetPlan(window, now),
  };
}

function formatStatus(status) {
  const today = status.budget.today;
  const budget = today
    ? `; today's fixed budget ${today.budgetPercent}% (planned remaining ${today.plannedRemainingAfterDayPercent}%)`
    : "";
  return `Actual Codex quota remaining ${status.remainingPercent}% (used ${status.usedPercent}%); resets ${status.resetsAtIso}; ${status.alert.message}${budget}`;
}

function usageSummary(result) {
  return {
    source: "codex-app-server",
    summary: result?.summary || null,
    dailyUsageBuckets: result?.dailyUsageBuckets || null,
  };
}

function printHelp() {
  process.stdout.write(`Codex Quota Insights\n\nUsage:\n  codex-quota.mjs status [--json]\n  codex-quota.mjs plan [--json]\n  codex-quota.mjs usage [--json]\n  codex-quota.mjs all [--json]\n\nOptions:\n  --codex-bin PATH   Codex executable (default: CODEX_BIN or codex)\n  --timeout-ms N     Request timeout (default: 15000)\n  --json             Print machine-readable JSON\n`);
}

async function main() {
  const options = parseArgs(process.argv);
  if (options.command === "help") return printHelp();

  const client = new AppServerClient(options);
  try {
    await client.start();
    let output;
    if (["status", "plan", "all"].includes(options.command)) {
      const result = await client.request("account/rateLimits/read");
      const status = buildStatus(result);
      output = options.command === "plan" ? status.budget : status;
    }
    if (["usage", "all"].includes(options.command)) {
      const usage = usageSummary(await client.request("account/usage/read"));
      output = options.command === "all" ? { quota: output, usage } : usage;
    }

    if (options.json || options.command !== "status") process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
    else process.stdout.write(`${formatStatus(output)}\n`);
  } finally {
    client.close();
  }
}

const isDirectRun = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isDirectRun) {
  main().catch((error) => {
    process.stderr.write(`codex-quota: ${error instanceof Error ? error.message : String(error)}\n`);
    process.stderr.write("Sign in with the Codex CLI first, or pass the correct executable with --codex-bin.\n");
    process.exitCode = 1;
  });
}
