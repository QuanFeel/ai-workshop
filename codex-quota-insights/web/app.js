(() => {
  const $ = (id) => document.getElementById(id),
    PLAN_KEY = "codex-quota-open-plan",
    TIPS_KEY = "codex-quota-open-tips",
    defaultTips = {
      rules: [
        {
          title: "模型与速度",
          body: "默认使用 GPT-5.6 Sol；推荐使用 High，尽量不用 xhigh（Extra High）；Speed 使用 Standard。\n确定任务或者额度较低时，推荐使用 GPT-5.6 Terra / Luna，计费便宜很多，耐用。",
          link: "",
        },
        {
          title: "少开 subagent",
          body: "默认少开 subagent；确实需要开启时，必须先询问并获得明确确认。",
          link: "",
        },
        {
          title: "RTK 命令输出压缩工具",
          body: "Rust Token Killer：在命令输出进入模型上下文前进行过滤与压缩。",
          link: "https://github.com/rtk-ai/rtk",
          effectNote:
            "以下均为 Bash 命令输出减少比例，不等于 Codex 总 Token 或周额度同比减少。",
          effects: [
            { ratio: "60–90%", label: "常见开发命令 · 官方整体口径" },
            { ratio: "约 90%", label: "pytest / Go / Cargo / 通用测试" },
            { ratio: "约 80%", label: "Cargo build / clippy / Ruff" },
            { ratio: "约 85%", label: "golangci-lint" },
            { ratio: "约 75%", label: "SBT compile" },
            { ratio: "60%+", label: "RSpec / RuboCop" },
          ],
          examples:
            "官方示例：ls 45 行 → 12 行（约少 73%） · git push 15 行 → 1 行（约少 93%） · cargo test 200+ 行 → 约 20 行（约少 90%）",
        },
      ],
      prompt:
        "## Subagent Usage\n\n- 默认少开 subagent。\n- 开启任何 subagent 前，必须先询问并获得明确确认。",
    };
  const read = (key, fallback) => {
      try {
        return JSON.parse(localStorage.getItem(key) || "");
      } catch {
        return fallback;
      }
    },
    put = (key, value) => localStorage.setItem(key, JSON.stringify(value)),
    esc = (value) =>
      String(value ?? "").replace(
        /[&<>"']/g,
        (ch) =>
          ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          })[ch],
      ),
    httpUrl = (value) => {
      try {
        const url = new URL(value);
        return /^https?:$/.test(url.protocol) ? url.href : "";
      } catch {
        return "";
      }
    },
    effectsHtml = (rule) => {
      const effects = Array.isArray(rule.effects) ? rule.effects : [];
      if (!rule.effectNote && !effects.length && !rule.examples) return "";
      return (
        (rule.effectNote ? '<div class="effect-note">' + esc(rule.effectNote) + "</div>" : "") +
        (effects.length
          ? '<div class="effects">' +
            effects
              .map((effect) => '<div class="effect"><b>' + esc(effect.ratio) + "</b><span>" + esc(effect.label) + "</span></div>")
              .join("") +
            "</div>"
          : "") +
        (rule.examples ? '<div class="examples">' + esc(rule.examples) + "</div>" : "")
      );
    },
    addDays = (date, count) => {
      const copy = new Date(date);
      copy.setDate(copy.getDate() + count);
      return copy;
    },
    dayStart = (date) =>
      new Date(date.getFullYear(), date.getMonth(), date.getDate()),
    key = (date) =>
      date.getFullYear() +
      "-" +
      String(date.getMonth() + 1).padStart(2, "0") +
      "-" +
      String(date.getDate()).padStart(2, "0"),
    time = (date) =>
      String(date.getHours()).padStart(2, "0") +
      ":" +
      String(date.getMinutes()).padStart(2, "0"),
    label = (date) => date.getMonth() + 1 + "/" + date.getDate(),
    weekday = (date) =>
      new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(date),
    weekend = (date) => date.getDay() === 0 || date.getDay() === 6;
  const weightedMinutes = (from, to) => {
    const sleepEnd = new Date(
        from.getFullYear(),
        from.getMonth(),
        from.getDate(),
        7,
      ),
      early = Math.max(0, Math.min(to, sleepEnd) - from) / 60_000,
      normal = Math.max(0, to - Math.max(from, sleepEnd)) / 60_000;
    return early * 0.25 + normal;
  };
  const savedTips = read(TIPS_KEY, null),
    savedTipTitles = Array.isArray(savedTips?.rules)
      ? savedTips.rules.map((rule) => rule.title).join("|")
      : "",
    isManagedDefaultTips =
      Array.isArray(savedTips?.rules) &&
      (savedTipTitles === "模型与速度|少开 subagent|限制命令输出|RTK 命令输出压缩工具" ||
        savedTipTitles === "模型与速度|少开 subagent|省约 50% Token 的 AGENTS.md 方法|RTK 命令输出压缩工具" ||
        savedTipTitles === "模型与速度|少开 subagent|RTK 命令输出压缩工具");
  if (isManagedDefaultTips) put(TIPS_KEY, defaultTips);
  let quota = null,
    plan = read(PLAN_KEY, {}),
    tips = isManagedDefaultTips ? defaultTips : savedTips || defaultTips,
    editing = null;
  function segments(start, end) {
    const days = [];
    for (let day = dayStart(start); day <= dayStart(end); day = addDays(day, 1))
      days.push(day);
    return {
      days,
      parts: days.map((day) => {
        const next = addDays(day, 1),
          from = new Date(Math.max(day, start)),
          to = new Date(Math.min(next, end));
        return {
          from,
          to,
          active: Math.max(0, Math.round((to - from) / 60000)),
          weight: 0,
        };
      }),
    };
  }
  function rounded(weights, total) {
    const sum = weights.reduce((a, b) => a + b, 0),
      last = weights.reduce(
        (found, value, index) => (value > 0 ? index : found),
        -1,
      );
    if (!sum || last < 0) return weights.map(() => 0);
    let used = 0;
    return weights.map((weight, index) => {
      if (index === last) return total - used;
      const value = Math.round((weight / sum) * total);
      used += value;
      return value;
    });
  }
  function balanceWeekend(rows) {
    for (let index = 0; index < rows.length - 1; index += 1) {
      const first = rows[index],
        second = rows[index + 1];
      if (
        first.day.getDay() !== 6 ||
        second.day.getDay() !== 0 ||
        first.active !== 1440 ||
        second.active !== 1440
      )
        continue;
      const total = Math.round(first.budget + second.budget);
      first.budget = Math.ceil(total / 2);
      second.budget = Math.floor(total / 2);
    }
  }
  function balanceSplitBoundary(rows) {
    const first = rows.findIndex((row) => row.active > 0),
      last = rows.findLastIndex((row) => row.active > 0);
    if (first < 0 || first === last || rows[first].day.getDay() !== rows[last].day.getDay()) return;
    if (rows[first].active === 1440 && rows[last].active === 1440) return;

    const boundaryWeekend = weekend(rows[first].day),
      fullWeekdays = rows.filter((row, index) => index !== first && index !== last && !weekend(row.day) && row.active === 1440),
      fullWeekendPeers = rows.filter((row, index) => index !== first && index !== last && weekend(row.day) && row.active === 1440),
      weekendCandidates = rows
        .filter((row, index) => index !== first && index !== last && weekend(row.day) && row.active > 0)
        .sort((left, right) => (right.day.getDay() === 0) - (left.day.getDay() === 0) || right.budget - left.budget);
    let target, candidates, weekendTotal;
    if (boundaryWeekend) {
      if (!fullWeekdays.length || !fullWeekendPeers.length) return;
      const weekdayTarget = Math.round((100 * 2) / (5 * 2 + 2));
      fullWeekdays.forEach((row) => (row.budget = weekdayTarget));
      weekendTotal = 100 - fullWeekdays.reduce((sum, row) => sum + row.budget, 0);
      target = rows[first].day.getDay() === 6 ? Math.ceil(weekendTotal / 2) : Math.floor(weekendTotal / 2);
      candidates = fullWeekendPeers;
    } else {
      if (!fullWeekdays.length || !weekendCandidates.length) return;
      target = Math.round(fullWeekdays.reduce((sum, row) => sum + row.budget, 0) / fullWeekdays.length);
      candidates = weekendCandidates;
    }
    let delta = target - rows[first].budget - rows[last].budget;
    for (const row of candidates) {
      if (!delta) break;
      if (delta > 0 && row.budget > 0) {
        row.budget -= 1;
        rows[last].budget += 1;
        delta -= 1;
      } else if (delta < 0 && rows[last].budget > 0) {
        row.budget += 1;
        rows[last].budget -= 1;
        delta += 1;
      }
    }
    if (boundaryWeekend && candidates.length === 1) candidates[0].budget = weekendTotal - target;
  }
  function fixedRows(start, end, total = 100) {
    const { days, parts } = segments(start, end),
      rows = days.map((day, index) => ({ day, ...parts[index] }));
    rows.forEach(
      (row) => (row.weight = weightedMinutes(row.from, row.to) * (weekend(row.day) ? 1 : 2)),
    );
    const shares = rounded(
      rows.map((row) => row.weight),
      total,
    );
    rows.forEach((row, index) => (row.budget = shares[index]));
    balanceSplitBoundary(rows);
    balanceWeekend(rows);
    const drift = total - rows.reduce((sum, row) => sum + row.budget, 0),
      tail = [...rows].reverse().find((row) => row.budget > 0);
    if (tail) tail.budget += drift;
    let remain = total;
    return rows.map((row) => {
      remain = Math.max(0, remain - row.budget);
      return { ...row, remain };
    });
  }
  function resetAt(dateValue, timeValue) {
    const [year, month, day] = dateValue.split("-").map(Number),
      [hour, minute] = timeValue.split(":").map(Number);
    return new Date(year, month - 1, day, hour, minute, 0, 0);
  }
  function projection() {
    const officialReset = new Date(quota.resetsAtIso),
      now = new Date(),
      // 精确从上一次官方重置时刻开始，而不是从七天前的 00:00 开始。
      // 例如 8/25 22:50 重置时，首日只计 22:50–24:00。
      officialStart = new Date(
        officialReset.getTime() -
          (Number(quota.windowDurationMins) || 7 * 24 * 60) * 60_000,
      );
    if (plan.mode === "estimate") {
      const selected = new Date(plan.reset);
      if (selected > now && selected <= officialReset) {
        // 预计重置在应用时冻结：起点和可分配总额都不随后续自动刷新变化。
        if (!plan.allocationStart || !Number.isFinite(Number(plan.allocationBudget))) {
          plan = {
            ...plan,
            allocationStart: now.toISOString(),
            allocationBudget: quota.remainingPercent,
          };
          put(PLAN_KEY, plan);
        }
        const allocationStart = new Date(plan.allocationStart),
          allocationBudget = Math.max(0, Math.min(100, Number(plan.allocationBudget))),
          start = Number.isFinite(allocationStart.getTime()) && allocationStart < selected
            ? allocationStart
            : now,
          budget = Number.isFinite(allocationBudget) ? allocationBudget : quota.remainingPercent;
        const old = fixedRows(officialStart, officialReset),
          past = old.filter((row) => row.day < dayStart(start)),
          future = fixedRows(start, selected, budget),
          current = [...past, ...future];
        return {
          mode: "estimate",
          start: officialStart,
          reset: selected,
          current,
          next: fixedRows(selected, addDays(selected, 7)),
          officialReset,
        };
      }
    }
    return {
      mode: "official",
      start: officialStart,
      reset: officialReset,
      current: fixedRows(officialStart, officialReset),
      next: [],
      officialReset,
    };
  }
  function renderCalendar(id, rows) {
    const today = key(new Date());
    $(id).innerHTML = rows
      .map((row, index) => {
        const range =
            row.active >= 1440 ? "全天" : time(row.from) + "—" + time(row.to),
          marker =
            key(row.day) === today
              ? '<span class="tag">今天</span>'
              : index === rows.length - 1
                ? '<span class="reset-tag">重置 ' + time(row.to) + "</span>"
                : '<div class="tag-spacer"></div>';
        return (
          '<article class="day ' +
          (key(row.day) < today ? "past " : "") +
          (key(row.day) === today ? "today" : "") +
          '"><div class="day-top"><span>' +
          label(row.day) +
          "</span><span>" +
          weekday(row.day) +
          "</span></div>" +
          marker +
          '<div class="range">💪 时段：' +
          range +
          '</div><div class="metric"><span>当日预算</span><strong>' +
          row.budget +
          '%</strong></div><div class="metric"><span>预算剩余</span><strong>' +
          row.remain +
          "%</strong></div></article>"
        );
      })
      .join("");
  }
  function renderQuota() {
    if (!quota) return;
    const p = projection(),
      reset = p.reset,
      diff = Math.max(0, reset - Date.now()),
      mins = Math.ceil(diff / 60000);
    $("remaining").textContent = quota.remainingPercent + "%";
    $("usedQuota").textContent = "已用：" + quota.usedPercent + "%";
    $("syncInfo").textContent = "本机 Codex 自动读取";
    $("officialReset").textContent =
      (p.mode === "estimate" ? "预计重置：" : "下次重置：") +
      label(reset).replace("/", ".") +
      " " +
      time(reset) +
      "（" +
      Math.floor(mins / 1440) +
      "天" +
      Math.floor((mins % 1440) / 60) +
      "小时" +
      (mins % 60) +
      "分后）";
    $("resetDate").min = key(new Date());
    $("resetDate").max = key(p.officialReset);
    $("resetDate").value = p.mode === "estimate" ? key(reset) : key(new Date());
    $("resetTime").value =
      p.mode === "estimate" ? time(reset) : time(p.officialReset);
    $("currentLabel").textContent =
      "当前周期 · " +
      label(p.start) +
      " " +
      time(p.start) +
      "—" +
      label(p.reset) +
      " " +
      time(p.reset);
    $("nextLabel").textContent =
      "下一个周期 · " +
      label(p.reset) +
      " " +
      time(p.reset) +
      "—" +
      label(addDays(p.reset, 7)) +
      " " +
      time(p.reset);
    renderCalendar("currentCalendar", p.current);
    renderCalendar("nextCalendar", p.next);
    $("nextCycle").hidden = p.mode !== "estimate";
    $("restoreOfficial").hidden = p.mode === "official";
    $("toggleEstimate").textContent =
      p.mode === "estimate" ? "修改预计重置日" : "预计重置日";
  }
  function tipMessage(message, error = false) {
    $("tipsSaveState").textContent = message;
    $("tipsSaveState").className = "save-result " + (error ? "error" : "hint");
    clearTimeout(tipMessage.timer);
    if (message && !error)
      tipMessage.timer = setTimeout(() => {
        $("tipsSaveState").textContent = "";
      }, 1500);
  }
  function renderTips() {
    const rows = tips.rules || [];
    $("tipCount").textContent = rows.length + " 条 · 浏览器本地";
    $("tips").innerHTML =
      rows
        .map((rule, index) => {
          if (editing === index)
            return (
              '<article class="tip" data-index="' +
              index +
              '"><div class="tip-head"><input class="edit-input" data-field="title" value="' +
              esc(rule.title) +
              '" aria-label="建议标题"><div class="tip-actions"><button class="ghost" type="button" data-action="finish">完成</button><button class="ghost delete" type="button" data-action="delete">删除</button></div></div><textarea class="edit-body" data-field="body" aria-label="建议说明">' +
              esc(rule.body) +
              '</textarea><div class="edit-link"><input class="edit-input" data-field="link" value="' +
              esc(rule.link) +
              '" placeholder="可选链接 https://..."></div></article>'
            );
          const link = httpUrl(rule.link);
          return (
            '<article class="tip" data-index="' +
            index +
            '"><div class="tip-head"><h3>' +
            String(index + 1) +
            ". " +
            esc(rule.title) +
            '</h3><div class="tip-actions"><button class="ghost" type="button" data-action="edit">编辑</button></div></div><p>' +
            esc(rule.body) +
            "</p>" +
            (link
              ? '<a href="' +
                esc(link) +
                '" target="_blank" rel="noopener noreferrer">' +
                esc(link) +
                "</a>"
              : "") +
            effectsHtml(rule) +
            "</article>"
          );
        })
        .join("") || '<div class="tip">暂时没有建议。</div>';
    $("promptInput").value = tips.prompt || "";
  }
  async function loadQuota() {
    try {
      const response = await fetch("/api/quota", { cache: "no-store" }),
        data = await response.json();
      if (!response.ok) throw new Error(data.message || "额度读取失败");
      quota = data.quota;
      $("time").textContent =
        "数据更新：" +
        new Date(data.fetchedAtIso || Date.now()).toLocaleString("zh-CN", {
          hour12: false,
        });
      renderQuota();
    } catch (error) {
      $("time").textContent = "读取失败：" + error.message;
      $("time").className = "error";
    }
  }
  document.querySelectorAll("[data-tab]").forEach((button) =>
    button.addEventListener("click", () => {
      document
        .querySelectorAll("[data-tab]")
        .forEach((item) => item.classList.toggle("active", item === button));
      $("tabQuota").hidden = button.dataset.tab !== "quota";
      $("tabSaving").hidden = button.dataset.tab !== "saving";
    }),
  );
  $("toggleEstimate").addEventListener("click", () => {
    $("planner").hidden = !$("planner").hidden;
  });
  $("planner").addEventListener("submit", (event) => {
    event.preventDefault();
    const selected = resetAt($("resetDate").value, $("resetTime").value);
    if (
      !quota ||
      selected <= new Date() ||
      selected > new Date(quota.resetsAtIso)
    ) {
      tipMessage("请选择当前时刻到官方重置时间之间的日期和时间", true);
      return;
    }
    plan = {
      mode: "estimate",
      reset: selected.toISOString(),
      allocationStart: new Date().toISOString(),
      allocationBudget: quota.remainingPercent,
    };
    put(PLAN_KEY, plan);
    $("planner").hidden = true;
    renderQuota();
    tipMessage("预计重置已保存");
  });
  $("restoreOfficial").addEventListener("click", () => {
    plan = {};
    put(PLAN_KEY, plan);
    renderQuota();
    tipMessage("已恢复官方重置");
  });
  $("addTip").addEventListener("click", () => {
    tips.rules.push({
      title: "新建议",
      body: "在这里填写建议说明。",
      link: "",
    });
    editing = tips.rules.length - 1;
    renderTips();
    $("tips").querySelector('[data-field="title"]')?.focus();
  });
  $("tips").addEventListener("input", (event) => {
    const item = event.target.closest("[data-index]"),
      field = event.target.dataset.field;
    if (!item || !field) return;
    tips.rules[Number(item.dataset.index)][field] = event.target.value;
  });
  $("tips").addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]"),
      item = event.target.closest("[data-index]");
    if (!button || !item) return;
    const index = Number(item.dataset.index);
    if (button.dataset.action === "edit") {
      editing = index;
      renderTips();
      return;
    }
    if (button.dataset.action === "finish") {
      editing = null;
      put(TIPS_KEY, tips);
      renderTips();
      tipMessage("建议已保存");
      return;
    }
    if (button.dataset.action === "delete") {
      tips.rules.splice(index, 1);
      editing = null;
      put(TIPS_KEY, tips);
      renderTips();
      tipMessage("建议已删除");
    }
  });
  $("savePrompt").addEventListener("click", () => {
    tips.prompt = $("promptInput").value;
    put(TIPS_KEY, tips);
    tipMessage("Markdown 已保存");
  });
  $("copyPrompt").addEventListener("click", () =>
    navigator.clipboard
      .writeText($("promptInput").value)
      .then(() => tipMessage("Markdown 已复制"))
      .catch(() => tipMessage("复制失败", true)),
  );
  renderTips();
  loadQuota();
})();
