/* Strategy execution page — step wizard with param sliders and run state machine */

let strategyMetas = [];
let constraintsCache = null;

function renderStrategies(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">策略运行</h1>
        </div>

        <div class="card" style="padding:16px 20px;">
            <div class="card-title card-title-sm" style="margin-bottom:10px;">策略选择</div>
            <div class="tab-bar-pill" id="strategy-tabs"></div>
        </div>

        <div class="step-wizard" id="step-wizard">
            <div class="step active" data-step="1">
                <span class="step-num">1</span> 参数配置
            </div>
            <div class="step-connector"></div>
            <div class="step" data-step="2">
                <span class="step-num">2</span> 约束检查
            </div>
            <div class="step-connector"></div>
            <div class="step" data-step="3">
                <span class="step-num">3</span> 运行结果
            </div>
        </div>

        <div class="grid-12" style="align-items:start;">
            <div class="col-6">
                <div class="card">
                    <div class="card-title">参数配置</div>
                    <div id="strategy-params">${Utils.skeleton(4)}</div>
                    <div class="btn-group mt-16">
                        <button class="btn btn-primary" id="btn-run-strategy" disabled>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>
                            运行策略
                        </button>
                        <button class="btn btn-sm" id="btn-reset-params">重置</button>
                    </div>
                </div>
            </div>

            <div class="col-6">
                <div class="card">
                    <div class="card-title">约束检查</div>
                    <div id="constraint-panel">
                        <div class="empty-state">
                            <div class="empty-title">配置参数后点击"运行策略"</div>
                            <div class="empty-desc">运行时将校验组合权重与分类约束</div>
                        </div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">运行结果</div>
                    <div id="strategy-result">
                        <div class="empty-state">
                            <div class="empty-title">暂无结果</div>
                            <div class="empty-desc">运行完成后展示收益指标与净值曲线</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-title" style="cursor:pointer;" id="history-toggle">
                <span>历史运行记录</span>
                <span class="text-muted" id="history-arrow" style="font-size:12px;">▸ 展开</span>
            </div>
            <div id="history-panel" style="display:none;">
                <div class="btn-group mb-16">
                    <button class="btn btn-sm" id="btn-refresh-runs">🔄 刷新</button>
                </div>
                <div class="table-wrap">
                    <table class="table" id="runs-table">
                        <thead><tr><th>ID</th><th>策略</th><th>状态</th><th>参数</th><th>创建时间</th><th>操作</th></tr></thead>
                        <tbody><tr><td colspan="6" class="text-muted">加载中...</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    loadStrategies();
    loadRuns();
    loadConstraints();

    document.getElementById("btn-refresh-runs").addEventListener("click", loadRuns);
    document.getElementById("btn-reset-params").addEventListener("click", () => {
        const sel = document.getElementById("strategy-type");
        if (sel) sel.dispatchEvent(new Event("change"));
        Components.toast("参数已重置", "info", 1200);
    });
    document.getElementById("history-toggle").addEventListener("click", toggleHistory);
    document.getElementById("btn-run-strategy").addEventListener("click", runStrategy);
}

function toggleHistory() {
    const panel = document.getElementById("history-panel");
    const arrow = document.getElementById("history-arrow");
    const open = panel.style.display !== "none";
    panel.style.display = open ? "none" : "block";
    arrow.textContent = open ? "▸ 展开" : "▾ 收起";
}

async function loadStrategies() {
    try {
        strategyMetas = await API.listStrategies();
        const tabsEl = document.getElementById("strategy-tabs");
        tabsEl.innerHTML = strategyMetas
            .map(
                (s, i) =>
                    `<button class="tab-pill ${i === 0 ? "active" : ""}" data-strategy="${s.name}">${Utils.escapeHtml(s.display_name || s.name)}</button>`
            )
            .join("");

        tabsEl.querySelectorAll(".tab-pill").forEach((tab) => {
            tab.addEventListener("click", () => {
                tabsEl.querySelectorAll(".tab-pill").forEach((t) => t.classList.remove("active"));
                tab.classList.add("active");
                renderParamForm(tab.dataset.strategy);
            });
        });
        renderParamForm(strategyMetas[0]?.name);
    } catch (e) {
        tabsEl.innerHTML = `<span class="text-muted">加载失败: ${Utils.escapeHtml(e.message)}</span>`;
    }
}

async function loadConstraints() {
    try {
        constraintsCache = await API.getConstraints();
        renderConstraintPanel();
    } catch (e) {
        constraintsCache = null;
    }
}

function renderConstraintPanel() {
    const panel = document.getElementById("constraint-panel");
    if (!panel) return;
    const c = constraintsCache;
    if (!c) {
        panel.innerHTML = `<div class="text-muted" style="font-size:13px;">约束配置加载失败</div>`;
        return;
    }
    const cats = c.category_constraints || [];
    panel.innerHTML = `
        <div style="font-size:13px;line-height:2;">
            <div class="flex-between" style="padding:6px 0;border-bottom:1px solid var(--border-subtle);">
                <span class="text-muted">单票权重范围</span>
                <span class="mono">${Utils.formatPct(c.single_min_weight, 0)} ~ ${Utils.formatPct(c.single_max_weight, 0)}</span>
            </div>
            ${cats.map((cc) => `
                <div class="flex-between" style="padding:6px 0;border-bottom:1px solid var(--border-subtle);">
                    <span class="text-muted">${Utils.escapeHtml(cc.category_key)} = ${Utils.escapeHtml(cc.category_value)}</span>
                    <span class="mono">${Utils.formatPct(cc.min_weight, 0)} ~ ${Utils.formatPct(cc.max_weight, 0)}</span>
                </div>`).join("")}
            ${!cats.length ? `<div class="text-muted" style="font-size:12px;">未配置分类约束</div>` : ""}
        </div>`;
}

function currentStrategyName() {
    const active = document.querySelector("#strategy-tabs .tab-pill.active");
    return active?.dataset.strategy || strategyMetas[0]?.name;
}

async function renderParamForm(strategyName) {
    const meta = strategyMetas.find((s) => s.name === strategyName);
    const box = document.getElementById("strategy-params");
    if (!meta) {
        box.innerHTML = `<div class="text-muted">暂无参数</div>`;
        return;
    }

    let html = `<div class="form-row">`;
    meta.params_schema.forEach((p) => {
        if (p.type === "json") {
            html += `
                <div class="form-group" style="grid-column: 1 / -1;">
                    <label>${Utils.escapeHtml(p.label)}</label>
                    <textarea id="param-${p.name}" class="form-control" rows="3" placeholder='[{"assets":[{"sec":"510300.SH","weight":1.0}],"q":0.05,"confidence":0.8}]'></textarea>
                </div>`;
        } else if (p.type === "bool") {
            html += `
                <div class="form-group">
                    <label>${Utils.escapeHtml(p.label)}</label>
                    <select id="param-${p.name}" class="form-select">
                        <option value="true" ${p.default === true ? "selected" : ""}>是</option>
                        <option value="false" ${p.default === false ? "selected" : ""}>否</option>
                    </select>
                </div>`;
        } else if (p.type === "category_weights" || p.type === "category_exponents") {
            const isWeights = p.type === "category_weights";
            const max = isWeights ? 1 : 5;
            const step = isWeights ? 0.05 : 0.1;
            const def = p.default || {};
            html += `
                <div class="form-group" style="grid-column: 1 / -1;">
                    <label>${Utils.escapeHtml(p.label)}</label>
                    <div id="param-${p.name}">
                        ${(p.options || []).map((c) => `
                            <div class="slider-row">
                                <span style="width:64px;font-size:12px;color:var(--text-secondary,#6e6e6e);">${Utils.escapeHtml(c.display_name)}</span>
                                <input type="range" class="slider" data-cat="${Utils.escapeHtml(c.key)}"
                                    min="${isWeights ? 0 : 0.1}" max="${max}" step="${step}" value="${def[c.key] ?? (isWeights ? 0 : 1)}" />
                                <span class="slider-value" data-val="${Utils.escapeHtml(c.key)}">${(def[c.key] ?? (isWeights ? 0 : 1)).toFixed(2)}</span>
                            </div>`).join("")}
                    </div>
                </div>`;
        } else {
            const numeric = p.type === "int" || p.type === "float";
            const hasRange = p.min !== null && p.min !== undefined && p.max !== null && p.max !== undefined;
            if (numeric && hasRange) {
                const step = p.step || (p.type === "int" ? 1 : 0.05);
                html += `
                    <div class="form-group">
                        <label>${Utils.escapeHtml(p.label)}</label>
                        <div class="slider-row">
                            <input type="range" class="slider" id="param-${p.name}-range"
                                min="${p.min}" max="${p.max}" step="${step}" value="${p.default ?? p.min}" />
                            <span class="slider-value" id="param-${p.name}-val">${p.default ?? p.min}</span>
                        </div>
                        <div class="form-hint">${p.type === "float" ? "请输入小数或拖动滑块" : "键盘方向键可微调"}</div>
                    </div>`;
            } else {
                html += `
                    <div class="form-group">
                        <label>${Utils.escapeHtml(p.label)}</label>
                        <input id="param-${p.name}" type="number" class="form-control"
                            value="${p.default ?? ""}"
                            ${p.min !== null && p.min !== undefined ? `min="${p.min}"` : ""}
                            ${p.max !== null && p.max !== undefined ? `max="${p.max}"` : ""}
                            ${p.step ? `step="${p.step}"` : ""} />
                    </div>`;
            }
        }
    });
    html += `</div>`;
    box.innerHTML = html;

    // Wire up sliders
    meta.params_schema.forEach((p) => {
        const range = document.getElementById(`param-${p.name}-range`);
        const val = document.getElementById(`param-${p.name}-val`);
        if (range && val) {
            const sync = () => {
                val.textContent = p.type === "float"
                    ? Number(range.value).toFixed(2)
                    : String(range.value);
            };
            range.addEventListener("input", sync);
            sync();
        }
    });

    // Wire up category sliders (weights / exponents)
    meta.params_schema.forEach((p) => {
        if (p.type !== "category_weights" && p.type !== "category_exponents") return;
        const box = document.getElementById(`param-${p.name}`);
        if (!box) return;
        box.querySelectorAll("input[type=range]").forEach((r) => {
            const lbl = box.querySelector(`[data-val="${r.dataset.cat}"]`);
            const sync = () => {
                if (lbl) lbl.textContent = Number(r.value).toFixed(2);
            };
            r.addEventListener("input", sync);
        });
    });
}

function setStep(n) {
    document.querySelectorAll("#step-wizard .step").forEach((el) => {
        const step = parseInt(el.dataset.step, 10);
        el.classList.toggle("active", step === n);
        el.classList.toggle("done", step < n);
    });
}

async function runStrategy() {
    const type = currentStrategyName();
    const meta = strategyMetas.find((s) => s.name === type);
    const params = {};

    meta.params_schema.forEach((p) => {
        const rangeEl = document.getElementById(`param-${p.name}-range`);
        const el = document.getElementById(`param-${p.name}`);
        if (p.type === "json") {
            try {
                params[p.name] = JSON.parse(el?.value || "[]");
            } catch (e) {
                Components.toast(`${p.label} JSON 格式错误`, "error");
                return;
            }
        } else if (p.type === "bool") {
            params[p.name] = el?.value === "true";
        } else if (p.type === "category_weights" || p.type === "category_exponents") {
            const box = document.getElementById(`param-${p.name}`);
            const obj = {};
            (box?.querySelectorAll("input[type=range]") || []).forEach((r) => {
                obj[r.dataset.cat] = parseFloat(r.value);
            });
            params[p.name] = obj;
        } else if (p.type === "int") {
            params[p.name] = parseInt(rangeEl?.value ?? el?.value, 10);
        } else if (p.type === "float") {
            params[p.name] = parseFloat(rangeEl?.value ?? el?.value);
        } else {
            params[p.name] = el?.value;
        }
    });

    const btn = document.getElementById("btn-run-strategy");
    const resultEl = document.getElementById("strategy-result");
    const constraintPanel = document.getElementById("constraint-panel");

    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>计算中...`;
    setStep(2);
    constraintPanel.innerHTML = `<div class="loading" style="padding:16px;"><div class="spinner"></div>校验约束中...</div>`;
    resultEl.innerHTML = `<div class="loading"><div class="spinner"></div>策略运行中，请稍候...</div>`;

    const resetButton = () => {
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>运行策略`;
        btn.disabled = false;
    };

    try {
        // Submit the strategy run; validation failures return immediately.
        const submitted = await API.runStrategy({ strategy_type: type, params });
        if (submitted.status === "failed") {
            setStep(1);
            resetButton();
            constraintPanel.innerHTML = renderConstraintsHtml();
            resultEl.innerHTML = `<div class="alert alert-error">运行失败: ${Utils.escapeHtml(submitted.error_msg || "未知错误")}</div>`;
            return;
        }

        // Poll the background task until it reaches a terminal state.
        let detail;
        while (true) {
            detail = await API.getRun(submitted.run_id);
            if (detail.status === "success" || detail.status === "failed") break;
            await new Promise((r) => setTimeout(r, 1000));
        }

        setStep(3);
        resetButton();

        // Rebuild the summary-shaped object from the persisted result_summary.
        const rs = detail.result_summary || {};
        const weights = rs.weights || {};
        const wDates = Object.keys(weights);
        const summary = {
            status: detail.status,
            error_msg: detail.error_msg,
            metrics: rs.metrics || {},
            nav_series: rs.equity_curve || {},
            weights: wDates.length ? weights[wDates[wDates.length - 1]] : {},
            constraint_violations: rs.constraint_violations || [],
        };
        renderRunSummary(resultEl, summary);
        renderConstraintCheck(constraintPanel, summary);
        loadRuns();
    } catch (e) {
        setStep(1);
        resetButton();
        constraintPanel.innerHTML = renderConstraintsHtml();
        resultEl.innerHTML = `<div class="alert alert-error">运行失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

function renderConstraintCheck(panel, summary) {
    if (summary.status !== "success") {
        panel.innerHTML = `<div class="alert alert-error">运行失败: ${Utils.escapeHtml(summary.error_msg || "未知错误")}</div>`;
        return;
    }
    if (summary.constraint_violations && summary.constraint_violations.length) {
        panel.innerHTML = `<div class="alert alert-warning">
            <strong>存在 ${summary.constraint_violations.length} 项约束警告</strong>
            <ul style="margin-top:6px;">${summary.constraint_violations
                .map((v) => `<li style="font-size:13px;">${Utils.escapeHtml(v.message)}</li>`)
                .join("")}</ul>
        </div>`;
    } else {
        panel.innerHTML = `<div class="alert alert-success">✅ 约束校验通过
            <div style="font-size:12px;margin-top:4px;">组合满足单票权重与分类约束要求</div></div>`;
    }
}

function renderConstraintsHtml() {
    const c = constraintsCache;
    if (!c) return `<div class="text-muted">约束配置加载失败</div>`;
    return `
        <div class="flex-between" style="padding:6px 0;border-bottom:1px solid var(--border-subtle);">
            <span class="text-muted">单票权重范围</span>
            <span class="mono">${Utils.formatPct(c.single_min_weight, 0)} ~ ${Utils.formatPct(c.single_max_weight, 0)}</span>
        </div>
        ${(c.category_constraints || []).map((cc) => `
            <div class="flex-between" style="padding:6px 0;border-bottom:1px solid var(--border-subtle);">
                <span class="text-muted">${Utils.escapeHtml(cc.category_key)} = ${Utils.escapeHtml(cc.category_value)}</span>
                <span class="mono">${Utils.formatPct(cc.min_weight, 0)} ~ ${Utils.formatPct(cc.max_weight, 0)}</span>
            </div>`).join("")}`;
}

function renderRunSummary(el, summary) {
    if (summary.status !== "success") {
        el.innerHTML = `<div class="alert alert-error">运行失败: ${Utils.escapeHtml(summary.error_msg || "未知错误")}</div>`;
        return;
    }

    const m = summary.metrics || {};

    el.innerHTML = `
        <div class="grid-12" style="gap:12px;">
            <div class="col-3 stat-card" style="padding:14px 16px;"><div class="stat-label">年化收益</div>
                <div class="stat-value stat-value-sm ${m.annual_return >= 0 ? "success-text" : "error-text"}">${Utils.formatPct(m.annual_return)}</div></div>
            <div class="col-3 stat-card" style="padding:14px 16px;"><div class="stat-label">夏普比率</div>
                <div class="stat-value stat-value-sm">${m.sharpe?.toFixed(3) ?? "-"}</div></div>
            <div class="col-3 stat-card" style="padding:14px 16px;"><div class="stat-label">最大回撤</div>
                <div class="stat-value stat-value-sm error-text">${Utils.formatPct(m.max_drawdown)}</div></div>
            <div class="col-3 stat-card" style="padding:14px 16px;"><div class="stat-label">总收益</div>
                <div class="stat-value stat-value-sm ${m.total_return >= 0 ? "success-text" : "error-text"}">${Utils.formatPct(m.total_return)}</div></div>
        </div>
        <div class="grid-12" style="margin-top:16px;gap:16px;">
            <div class="col-6" style="grid-column: span 6;">
                <div class="card-title card-title-sm">净值曲线</div>
                <div id="nav-chart" class="chart-sm"></div>
            </div>
            <div class="col-6" style="grid-column: span 6;">
                <div class="card-title card-title-sm">最新持仓权重</div>
                <div id="weights-chart" class="chart-sm"></div>
            </div>
        </div>`;

    renderNavChart(summary.nav_series);
    renderWeightsChart(summary.weights);
}

function renderNavChart(navSeries) {
    const el = document.getElementById("nav-chart");
    if (!el) return;
    const dates = Object.keys(navSeries);
    if (!dates.length) {
        el.innerHTML = `<div class="empty-state"><div class="empty-title">无净值数据</div></div>`;
        return;
    }
    Charts.render(el, () => ({
        tooltip: {
            trigger: "axis",
            className: "chart-tooltip-custom",
            formatter: (params) => {
                const p = params[0];
                return `<div class="tt-title">${p.axisValue}</div>
                    <div class="tt-row"><span>NAV</span><span class="tt-value">${Number(p.value).toFixed(4)}</span></div>`;
            },
        },
        grid: { left: 56, right: 20, top: 16, bottom: 56 },
        xAxis: { type: "category", data: dates },
        yAxis: { type: "value", scale: true },
        dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
        series: [{
            type: "line",
            data: Object.values(navSeries),
            smooth: true,
            showSymbol: false,
            name: "NAV",
            lineStyle: { width: 2 },
            areaStyle: { opacity: 0.08 },
        }],
        extra: {},
    }));
}

function renderWeightsChart(weights) {
    const el = document.getElementById("weights-chart");
    if (!el) return;
    const codes = Object.keys(weights);
    if (!codes.length) {
        el.innerHTML = `<div class="empty-state"><div class="empty-title">无持仓数据</div></div>`;
        return;
    }
    Charts.render(el, () => ({
        tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            className: "chart-tooltip-custom",
            formatter: (params) => {
                const p = params[0];
                return `<div class="tt-title">${Utils.escapeHtml(p.name)}</div>
                    <div class="tt-row"><span>权重</span><span class="tt-value">${Utils.formatPct(p.value)}</span></div>`;
            },
        },
        grid: { left: 80, right: 24, top: 16, bottom: 40 },
        xAxis: { type: "category", data: codes, axisLabel: { rotate: 30 } },
        yAxis: { type: "value", axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%" } },
        series: [{
            type: "bar",
            data: Object.values(weights),
            barMaxWidth: 28,
            itemStyle: { color: Charts.semanticColor("up"), opacity: 0.9 },
            label: { show: true, position: "top", formatter: (p) => (p.value * 100).toFixed(0) + "%", fontSize: 10 },
        }],
        extra: {},
    }));
}

async function loadRuns() {
    const tbody = document.querySelector("#runs-table tbody");
    if (!tbody) return;
    try {
        const runs = await API.listRuns(10);
        if (!runs.length) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-muted">暂无运行记录</td></tr>`;
            return;
        }
        const badgeMap = {
            success: "badge-success",
            failed: "badge-danger",
            running: "badge-info",
            pending: "badge-muted",
        };
        tbody.innerHTML = runs
            .map((r) => {
                const paramSummary = r.params
                    ? Object.entries(r.params)
                          .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`)
                          .join(" | ")
                          .slice(0, 60)
                    : "-";
                return `
                <tr>
                    <td>${r.id}</td>
                    <td>${Utils.escapeHtml(r.strategy_type)}</td>
                    <td><span class="badge ${badgeMap[r.status] || "badge-muted"}">${Utils.escapeHtml(r.status)}</span></td>
                    <td class="text-muted" style="font-size:12px;max-width:260px;overflow:hidden;text-overflow:ellipsis;" title="${Utils.escapeHtml(paramSummary)}">${Utils.escapeHtml(paramSummary)}</td>
                    <td class="text-muted" style="font-size:12px;">${Utils.formatDate(r.created_at)}</td>
                    <td>
                        <div class="row-actions btn-group">
                            <button class="btn btn-sm" onclick="viewRunDetail(${r.id})">详情</button>
                            <button class="btn btn-sm" onclick="exportRunCsv(${r.id})">导出</button>
                        </div>
                    </td>
                </tr>`;
            })
            .join("");
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-muted">加载失败: ${Utils.escapeHtml(e.message)}</td></tr>`;
    }
}

async function viewRunDetail(id) {
    try {
        const detail = await API.getRun(id);
        const m = detail.result_summary?.metrics || {};
        Components.modal({
            title: `运行详情 #${detail.id}`,
            body: `
                <div style="font-size:13px;line-height:2;">
                    <div><span class="text-muted">策略:</span> ${Utils.escapeHtml(detail.strategy_type)}</div>
                    <div><span class="text-muted">状态:</span> <span class="badge badge-success">${Utils.escapeHtml(detail.status)}</span></div>
                    <div><span class="text-muted">年化收益:</span> <span class="${m.annual_return >= 0 ? "success-text" : "error-text"}">${Utils.formatPct(m.annual_return)}</span></div>
                    <div><span class="text-muted">夏普:</span> ${m.sharpe?.toFixed(3) ?? "-"}</div>
                    <div><span class="text-muted">最大回撤:</span> ${Utils.formatPct(m.max_drawdown)}</div>
                    <div><span class="text-muted">创建时间:</span> ${Utils.formatDate(detail.created_at)}</div>
                    ${detail.error_msg ? `<div><span class="text-muted">错误:</span> <span class="error-text">${Utils.escapeHtml(detail.error_msg)}</span></div>` : ""}
                </div>`,
            actions: [{ label: "关闭" }],
        });
    } catch (e) {
        Components.toast(`加载失败: ${e.message}`, "error");
    }
}

async function exportRunCsv(id) {
    try {
        const data = await API.exportRun(id);
        const blob = new Blob([data.csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = data.filename || `strategy_run_${id}_nav.csv`;
        a.click();
        URL.revokeObjectURL(url);
        Components.toast("已导出净值 CSV", "success");
    } catch (e) {
        Components.toast(`导出失败: ${e.message}`, "error");
    }
}

window.renderStrategies = renderStrategies;
window.viewRunDetail = viewRunDetail;
window.exportRunCsv = exportRunCsv;
