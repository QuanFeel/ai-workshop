#!/usr/bin/env node

import { spawn } from "node:child_process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const DEFAULT_TIMEOUT_MS = 15_000;
const SLEEP_END_HOUR = 7;
const EARLY_HOUR_WEIGHT = 0.25;

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

function weightedLocalMinutes(start, end) {
  const sleepEnd = new Date(start.getFullYear(), start.getMonth(), start.getDate(), SLEEP_END_HOUR);
  const earlyMinutes = Math.max(0, Math.min(end.getTime(), sleepEnd.getTime()) - start.getTime()) / 60_000;
  const normalMinutes = Math.max(0, end.getTime() - Math.max(start.getTime(), sleepEnd.getTime())) / 60_000;
  return earlyMinutes * EARLY_HOUR_WEIGHT + normalMinutes;
}

function balanceSplitBoundaryDay(days) {
  const first = days.findIndex((day) => day.activeMinutes > 0);
  const last = days.findLastIndex((day) => day.activeMinutes > 0);
  if (first < 0 || first === last || days[first].weekday !== days[last].weekday) return;
  if (days[first].activeMinutes === 1440 && days[last].activeMinutes === 1440) return;

  const boundaryWeekend = days[first].weekend;
  const fullWeekdays = days.filter((day, index) => index !== first && index !== last && !day.weekend && day.activeMinutes === 1440);
  const fullWeekendPeers = days.filter((day, index) => index !== first && index !== last && day.weekend && day.activeMinutes === 1440);
  const weekendCandidates = days
    .map((day, index) => ({ day, index }))
    .filter(({ day, index }) => index !== first && index !== last && day.weekend && day.activeMinutes > 0)
    // 差额优先放在周日，保证周六最多比周日多 1%。
    .sort((left, right) => (right.day.weekday === 0) - (left.day.weekday === 0)
      || right.day.budgetPercent - left.day.budgetPercent);

  let target;
  let candidates;
  let weekendTotal;
  if (boundaryWeekend) {
    if (!fullWeekdays.length || !fullWeekendPeers.length) return;
    // 一个完整周固定为 5 个工作日 + 2 个周末日：工作日 17%，周六 8%、周日 7%。
    const weekdayTarget = Math.round((100 * 2) / (5 * 2 + 2));
    fullWeekdays.forEach((day) => { day.budgetPercent = weekdayTarget; });
    weekendTotal = 100 - fullWeekdays.reduce((sum, day) => sum + day.budgetPercent, 0);
    target = days[first].weekday === 6 ? Math.ceil(weekendTotal / 2) : Math.floor(weekendTotal / 2);
    candidates = fullWeekendPeers.map((day) => ({ day, index: days.indexOf(day) }));
  } else {
    if (!fullWeekdays.length || !weekendCandidates.length) return;
    target = Math.round(fullWeekdays.reduce((sum, day) => sum + day.budgetPercent, 0) / fullWeekdays.length);
    candidates = weekendCandidates;
  }

  let delta = target - (days[first].budgetPercent + days[last].budgetPercent);
  for (const { day } of candidates) {
    if (!delta) break;
    if (delta > 0 && day.budgetPercent > 0) {
      day.budgetPercent -= 1;
      days[last].budgetPercent += 1;
      delta -= 1;
    } else if (delta < 0 && days[last].budgetPercent > 0) {
      day.budgetPercent += 1;
      days[last].budgetPercent -= 1;
      delta += 1;
    }
  }
  if (boundaryWeekend && candidates.length === 1) candidates[0].day.budgetPercent = weekendTotal - target;
}

export function buildBudgetPlan(window, now = new Date()) {
  const reset = new Date(window.resetsAt * 1000);
  const cycleStart = new Date(reset.getTime() - window.windowDurationMins * 60_000);
  const weightedDays = [];

  for (let cursor = startOfLocalDay(cycleStart); cursor < reset;) {
    const next = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 1);
    const overlapStart = Math.max(cursor.getTime(), cycleStart.getTime());
    const overlapEnd = Math.min(next.getTime(), reset.getTime());
    const activeMinutes = Math.max(0, overlapEnd - overlapStart) / 60_000;
    if (activeMinutes > 0) {
      const weekend = cursor.getDay() === 0 || cursor.getDay() === 6;
      weightedDays.push({
        date: localDateKey(cursor),
        weekday: cursor.getDay(),
        weekend,
        periodStartIso: new Date(overlapStart).toISOString(),
        periodEndIso: new Date(overlapEnd).toISOString(),
        activeMinutes,
        // 0:00–7:00 仍计入，但按正常时段 25% 权重折算。
        weight: (weekend ? 1 : 2) * weightedLocalMinutes(new Date(overlapStart), new Date(overlapEnd)),
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

  const days = raw.map((day, index) => ({
      date: day.date,
      weekend: day.weekend,
      weekday: day.weekday,
      periodStartIso: day.periodStartIso,
      periodEndIso: day.periodEndIso,
      activeMinutes: day.activeMinutes,
      budgetPercent: Math.floor(day.rawBudget) + (bonus.has(index) ? 1 : 0),
    }));
  balanceSplitBoundaryDay(days);
  let plannedRemaining = 100;
  days.forEach((day) => {
    plannedRemaining -= day.budgetPercent;
    day.plannedRemainingAfterDayPercent = plannedRemaining;
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
