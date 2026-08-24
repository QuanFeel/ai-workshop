(() => {
  const $ = (id) => document.getElementById(id),
    PLAN_KEY = "codex-quota-open-plan",
    TIPS_KEY = "codex-quota-open-tips",
    defaultTips = {
      rules: [
        {
          title: "模型与速度",
          body: "普通任务优先合适的模型、High 与 Standard，避免不必要的 Extra High。",
          link: "",
        },
        {
          title: "少开 subagent",
          body: "默认少开 subagent；确实需要并行时，先说明分工和成本。",
          link: "",
        },
        {
          title: "限制命令输出",
          body: "为未知或可能很大的命令输出设置字节上限。",
          link: "",
        },
        {
          title: "RTK 命令输出压缩工具",
          body: "Rust Token Killer：在命令输出进入模型上下文前过滤和压缩。",
          link: "https://github.com/rtk-ai/rtk",
        },
      ],
      prompt:
        "默认少开 subagent。\n\n未知或可能很大的命令输出使用：\nCOMMAND 2>&1 | head -c 4000",
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
  let quota = null,
    plan = read(PLAN_KEY, {}),
    tips = read(TIPS_KEY, defaultTips),
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
  function fixedRows(start, end, total = 100) {
    const { days, parts } = segments(start, end),
      rows = days.map((day, index) => ({ day, ...parts[index] }));
    rows.forEach(
      (row) => (row.weight = row.active * (weekend(row.day) ? 1 : 2)),
    );
    const shares = rounded(
      rows.map((row) => row.weight),
      total,
    );
    rows.forEach((row, index) => (row.budget = shares[index]));
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
      officialStart = addDays(dayStart(officialReset), -7);
    if (plan.mode === "estimate") {
      const selected = new Date(plan.reset);
      if (selected > now && selected <= officialReset) {
        const old = fixedRows(officialStart, officialReset),
          today = dayStart(now),
          past = old.filter((row) => row.day < today),
          spent = past.reduce((sum, row) => sum + row.budget, 0),
          future = fixedRows(today, selected, 100 - spent);
        let remain = 100;
        const current = [...past, ...future].map((row) => ({
          ...row,
          remain: (remain = Math.max(0, remain - row.budget)),
        }));
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
    plan = { mode: "estimate", reset: selected.toISOString() };
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
