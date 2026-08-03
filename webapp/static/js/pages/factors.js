/* Factor analysis page — left factor list + right analysis workbench */

function renderFactors(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">因子看板</h1>
            <div class="btn-group">
                <button class="btn btn-primary btn-sm" id="btn-run-factor">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>
                    计算因子
                </button>
                <button class="btn btn-sm" id="btn-refresh-factors">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    刷新
                </button>
            </div>
        </div>

        <div class="card" style="padding:16px 20px;">
            <div class="flex-between">
                <div class="form-group" style="margin:0;">
                    <label style="margin-bottom:4px;">调仓窗口</label>
                    <select id="factor-horizon" class="form-select" style="width:auto;">
                        <option value="5">5 个交易日</option>
                        <option value="10">10 个交易日</option>
                        <option value="20">20 个交易日</option>
                    </select>
                </div>
                <div class="text-muted" style="font-size:12px;">选择左侧因子后点击"计算因子"</div>
            </div>
        </div>

        <div class="grid-12" style="align-items:start;">
            <div class="col-4" style="grid-column: span 4;">
                <div class="card">
                    <div class="card-title">因子列表</div>
                    <div id="factor-list" style="max-height:420px;overflow-y:auto;">
                        ${Utils.skeleton(5)}
                    </div>
                </div>
                <div id="factor-detail-card" class="card" style="display:none;"></div>
            </div>

            <div class="col-8">
                <div class="card">
                    <div class="tab-bar-pill" id="analysis-tabs">
                        <button class="tab-pill active" data-tab="ic">IC 时序</button>
                        <button class="tab-pill" data-tab="corr">相关性热力图</button>
                        <button class="tab-pill" data-tab="group">分组收益</button>
                    </div>
                    <div id="analysis-panel">
                        <div class="empty-state">
                            <div class="empty-icon">🧮</div>
                            <div class="empty-title">选择因子并计算</div>
                            <div class="empty-desc">勾选左侧因子，点击"计算因子"开始分析</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    loadFactorList();

    document.getElementById("btn-run-factor").addEventListener("click", runSelectedFactors);
    document.getElementById("btn-refresh-factors").addEventListener("click", loadFactorList);

    // Analysis tabs
    const tabs = document.querySelectorAll("#analysis-tabs .tab-pill");
    tabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            tabs.forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            renderAnalysisTab(tab.dataset.tab);
        });
    });
}

let factorMetasCache = [];
let lastResults = null; // { results: [...], correlation: [...] }

async function loadFactorList() {
    const listEl = document.getElementById("factor-list");
    try {
        factorMetasCache = await API.listFactors();
        if (!factorMetasCache.length) {
            listEl.innerHTML = `<div class="empty-state"><div class="empty-title">暂无因子</div></div>`;
            return;
        }
        listEl.innerHTML = factorMetasCache
            .map(
                (f, i) => `
                <label class="factor-chip" style="display:flex;width:100%;margin-bottom:8px;border-radius:8px;" data-factor="${f.name}">
                    <input type="checkbox" value="${f.name}" ${i < 3 ? "checked" : ""} style="flex-shrink:0;" />
                    <span style="flex:1;">${Utils.escapeHtml(f.display_name || f.name)}</span>
                    <span class="text-muted" style="font-size:11px;">${Utils.escapeHtml(f.category || "")}</span>
                </label>`
            )
            .join("");

        listEl.querySelectorAll(".factor-chip").forEach((chip) => {
            const checkbox = chip.querySelector("input");
            const sync = () => {
                chip.classList.toggle("checked", checkbox.checked);
                if (chip.dataset.factor) {
                    renderFactorDetail(chip.dataset.factor);
                }
            };
            checkbox.addEventListener("change", sync);
            chip.addEventListener("click", (e) => {
                if (e.target.tagName !== "INPUT") {
                    renderFactorDetail(chip.dataset.factor, true);
                }
            });
            sync();
        });
    } catch (e) {
        listEl.innerHTML = `<div class="alert alert-error">加载失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

function renderFactorDetail(name, force = false) {
    const card = document.getElementById("factor-detail-card");
    const meta = factorMetasCache.find((f) => f.name === name);
    if (!meta) return;
    card.style.display = "block";
    const dirClass = meta.direction === "正向" || meta.direction === "positive"
        ? "text-success" : meta.direction === "负向" || meta.direction === "negative"
            ? "text-danger" : "";
    card.innerHTML = `
        <div class="card-title card-title-sm" style="margin-bottom:12px;">📌 ${Utils.escapeHtml(meta.display_name || meta.name)}</div>
        <div style="font-size:13px;line-height:1.9;">
            <div><span class="text-muted">类别:</span> ${Utils.escapeHtml(meta.category || "-")}</div>
            <div><span class="text-muted">方向:</span> <span class="${dirClass}">${Utils.escapeHtml(meta.direction || "-")}</span></div>
            <div><span class="text-muted">公式:</span> <code>${Utils.escapeHtml(meta.formula || "-")}</code></div>
            <div><span class="text-muted">描述:</span> ${Utils.escapeHtml(meta.description || "-")}</div>
            ${meta.params_schema ? `<div><span class="text-muted">参数:</span>
                <code>${Object.entries(meta.params_schema)
                    .map(([k, v]) => `${k}=${v.default ?? ""}`)
                    .join(", ")}</code></div>` : ""}
        </div>`;
    if (!force) {
        // Auto-open detail for the first checked factor
        const firstChecked = document.querySelector("#factor-list input:checked");
        if (firstChecked && firstChecked.value !== name) return;
    }
}

function selectedFactorNames() {
    return Array.from(document.querySelectorAll("#factor-list input:checked")).map((c) => c.value);
}

async function runSelectedFactors() {
    const names = selectedFactorNames();
    if (!names.length) {
        Components.toast("请至少选择一个因子", "warning");
        return;
    }
    const horizon = parseInt(document.getElementById("factor-horizon").value, 10);
    const panel = document.getElementById("analysis-panel");
    panel.innerHTML = `<div class="loading"><div class="spinner"></div>计算中...</div>`;

    try {
        // Compute each factor's IC series and group returns in parallel
        const results = await Promise.all(
            names.map((name) =>
                API.computeFactor({ factor_name: name, params: {}, horizon })
            )
        );
        // Correlation matrix (best effort)
        let correlation = null;
        try {
            const corrResp = await API.factorCorrelation(names);
            correlation = corrResp.correlation_matrix || null;
        } catch (e) {
            correlation = null;
        }

        lastResults = { results, correlation, names, horizon };
        document.querySelector('#analysis-tabs .tab-pill[data-tab="ic"]').classList.add("active");
        document.querySelectorAll("#analysis-tabs .tab-pill").forEach((t) => {
            t.classList.toggle("active", t.dataset.tab === "ic");
        });
        renderAnalysisTab("ic");
        Components.toast(`已计算 ${results.length} 个因子`, "success");
    } catch (e) {
        panel.innerHTML = `<div class="alert alert-error">计算失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

function renderAnalysisTab(tab) {
    const panel = document.getElementById("analysis-panel");
    if (!lastResults) {
        panel.innerHTML = `<div class="empty-state">
            <div class="empty-icon">🧮</div>
            <div class="empty-title">选择因子并计算</div>
            <div class="empty-desc">勾选左侧因子，点击"计算因子"开始分析</div>
        </div>`;
        return;
    }
    if (tab === "ic") renderIcTab(panel);
    else if (tab === "corr") renderCorrTab(panel);
    else if (tab === "group") renderGroupTab(panel);
}

function renderIcTab(panel) {
    const { results } = lastResults;
    panel.innerHTML = `<div id="ic-multi-chart" class="chart"></div>`;

    const el = document.getElementById("ic-multi-chart");
    const allDates = [
        ...new Set(results.flatMap((r) => Object.keys(r.ic_result.ic_series || {}))),
    ].sort();

    Charts.render(el, () => ({
        tooltip: {
            trigger: "axis",
            className: "chart-tooltip-custom",
            formatter: (params) => {
                let html = `<div class="tt-title">${params[0]?.axisValue || ""}</div>`;
                params.forEach((p) => {
                    html += `<div class="tt-row">
                        <span class="tt-dot" style="background:${p.color}"></span>
                        <span>${Utils.escapeHtml(p.seriesName)}</span>
                        <span class="tt-value">${Number(p.value).toFixed(4)}</span>
                    </div>`;
                });
                return html;
            },
        },
        legend: { top: 0, type: "scroll" },
        grid: { left: 64, right: 24, top: 40, bottom: 56 },
        xAxis: { type: "category", data: allDates },
        yAxis: { type: "value", axisLabel: { formatter: (v) => v.toFixed(2) } },
        dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
        series: results.flatMap((r) => {
            const name = r.display_name || r.factor_name;
            return [
                {
                    name: `${name} IC`,
                    type: "line",
                    data: allDates.map((d) => r.ic_result.ic_series?.[d] ?? null),
                    connectNulls: true,
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: 2 },
                },
                {
                    name: `${name} RankIC`,
                    type: "line",
                    data: allDates.map((d) => r.ic_result.rank_ic_series?.[d] ?? null),
                    connectNulls: true,
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: 1.2, type: "dashed", opacity: 0.8 },
                },
            ];
        }),
        extra: {},
    }));
}

function renderCorrTab(panel) {
    const { correlation, names, results } = lastResults;
    if (!correlation) {
        panel.innerHTML = `<div class="empty-state">
            <div class="empty-icon">🔗</div>
            <div class="empty-title">无法计算相关性</div>
            <div class="empty-desc">因子相关性矩阵计算失败，可能是数据不足</div>
        </div>`;
        return;
    }
    panel.innerHTML = `
        <div class="text-muted" style="font-size:12px;margin-bottom:8px;">鼠标悬停查看相关系数，色阶：红(-1) ~ 白(0) ~ 绿(+1)</div>
        <div id="corr-chart" class="chart"></div>`;

    const el = document.getElementById("corr-chart");
    const labels = names.map((n) => {
        const meta = results.find((r) => r.factor_name === n);
        return (meta?.display_name || n).length > 6 ? (meta?.display_name || n).slice(0, 6) + "…" : meta?.display_name || n;
    });

    Charts.render(el, () => ({
        tooltip: {
            position: "top",
            formatter: (p) => {
                const v = p.value;
                if (!Array.isArray(v) || typeof v[2] !== "number") return "";
                return `<div class="tt-title">${Utils.escapeHtml(labels[v[0]])} × ${Utils.escapeHtml(labels[v[1]])}</div>
                    <div class="tt-row"><span class="tt-value">${v[2].toFixed(3)}</span></div>`;
            },
            className: "chart-tooltip-custom",
        },
        grid: { left: 72, right: 16, top: 16, bottom: 48 },
        xAxis: {
            type: "category",
            data: labels,
            splitArea: { show: true },
            axisLabel: { rotate: 30 },
        },
        yAxis: { type: "category", data: labels, splitArea: { show: true } },
        visualMap: {
            min: -1,
            max: 1,
            calculable: true,
            orient: "horizontal",
            left: "center",
            bottom: 4,
            inRange: {
                color: [
                    Charts.semanticColor("down"),
                    "#e9e6e1",
                    "#ffffff",
                    "#e9e6e1",
                    Charts.semanticColor("up"),
                ],
            },
            textStyle: { color: cssTick() },
        },
        series: [{
            type: "heatmap",
            data: correlation
                .map((row, i) => row.map((v, j) => [j, i, v]))
                .flat(),
            label: {
                show: true,
                fontSize: 11,
                formatter: (p) => (typeof p.value[2] === "number" ? p.value[2].toFixed(2) : ""),
            },
            emphasis: { itemStyle: { borderColor: "#2c2c2c", borderWidth: 1 } },
        }],
        extra: {},
    }));
}

function cssTick() {
    return getComputedStyle(document.documentElement).getPropertyValue("--chart-tick").trim() || "#9e9e9e";
}

function renderGroupTab(panel) {
    const { results } = lastResults;
    panel.innerHTML = `
        <div class="text-muted" style="font-size:12px;margin-bottom:8px;">各因子分组年化收益（G1 最低组 ~ G5 最高组）</div>
        <div id="group-chart" class="chart"></div>`;

    const el = document.getElementById("group-chart");
    const groupLabels = results[0]?.group_returns.map((g) => `G${g.group}`) || [];
    const up = Charts.semanticColor("up");
    const down = Charts.semanticColor("down");

    Charts.render(el, () => ({
        tooltip: {
            trigger: "axis",
            className: "chart-tooltip-custom",
            formatter: (params) => {
                let html = `<div class="tt-title">${params[0]?.axisValue || ""}</div>`;
                params.forEach((p) => {
                    html += `<div class="tt-row">
                        <span class="tt-dot" style="background:${p.color}"></span>
                        <span>${Utils.escapeHtml(p.seriesName)}</span>
                        <span class="tt-value">${Utils.formatPct(p.value)}</span>
                    </div>`;
                });
                return html;
            },
        },
        legend: { top: 0, type: "scroll" },
        grid: { left: 64, right: 24, top: 40, bottom: 40 },
        xAxis: { type: "category", data: groupLabels },
        yAxis: { type: "value", axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%" } },
        series: results.map((r) => ({
            name: r.display_name || r.factor_name,
            type: "bar",
            barGap: "20%",
            data: r.group_returns.map((g) => ({
                value: g.annual_return,
                itemStyle: { color: g.annual_return >= 0 ? up : down, opacity: 0.85 },
            })),
        })),
        extra: {},
    }));
}

window.renderFactors = renderFactors;
