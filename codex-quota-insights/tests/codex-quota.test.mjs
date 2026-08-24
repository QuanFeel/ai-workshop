import assert from "node:assert/strict";
import test from "node:test";

import { buildBudgetPlan, selectQuotaWindow } from "../scripts/codex-quota.mjs";

test("selects the longest Codex window as the weekly quota", () => {
  const selected = selectQuotaWindow({
    rateLimitsByLimitId: {
      codex: {
        limitId: "codex",
        primary: { usedPercent: 10, windowDurationMins: 300, resetsAt: 1_800_000_000 },
        secondary: { usedPercent: 32, windowDurationMins: 10_080, resetsAt: 1_800_600_000 },
      },
    },
  });
  assert.equal(selected.kind, "secondary");
  assert.equal(selected.usedPercent, 32);
  assert.equal(selected.windowDurationMins, 10_080);
});

test("fixed budget always totals 100 and keeps weekday 2:weekend 1 weighting", () => {
  const reset = new Date(2026, 7, 27, 11, 35, 0);
  const window = {
    resetsAt: Math.floor(reset.getTime() / 1000),
    windowDurationMins: 10_080,
  };
  const plan = buildBudgetPlan(window, new Date(2026, 7, 22, 12, 0, 0));
  assert.equal(plan.baselinePercent, 100);
  assert.deepEqual(plan.ratio, { weekday: 2, weekend: 1 });
  assert.equal(plan.days.reduce((sum, day) => sum + day.budgetPercent, 0), 100);
  assert.equal(plan.days.at(-1).plannedRemainingAfterDayPercent, 0);
  assert.ok(plan.today);
});

test("budget is deterministic and independent from actual used percentage", () => {
  const window = { resetsAt: 1_800_600_000, windowDurationMins: 10_080 };
  const now = new Date(1_800_100_000 * 1000);
  assert.deepEqual(buildBudgetPlan({ ...window, usedPercent: 5 }, now), buildBudgetPlan({ ...window, usedPercent: 95 }, now));
});
