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

let factorMetasCache = []; // list of category groups { key, display_name, is_empty, factors: [...] }
let lastResults = null; // { results: [...], correlation: {...}, ids: [...], horizon }

async function loadFactorList() {
    const listEl = document.getElementById("factor-list");
    try {
        factorMetasCache = await API.listFactors();
        const all = factorMetasCache.flatMap((c) => c.factors || []);
        if (!all.length) {
            listEl.innerHTML = `<div class="empty-state"><div class="empty-title">暂无因子</div></div>`;
            return;
        }

        // Build grouped markup: category header + instance checkboxes.
        let checkedCount = 0;
        const html = factorMetasCache.map((cat) => {
            if (cat.is_empty) {
                return `
                    <div class="fr-list-group">
                        <div class="fr-list-head">${Utils.escapeHtml(cat.display_name)}<span class="text-muted">（暂无）</span></div>
                    </div>`;
            }
            return `
                <div class="fr-list-group">
                    <div class="fr-list-head">${Utils.escapeHtml(cat.display_name)}</div>
                    ${cat.factors.map((f) => {
                        const checked = checkedCount < 3;
                        if (checked) checkedCount++;
                        return `
                        <label class="factor-chip" style="display:flex;width:100%;margin-bottom:6px;border-radius:8px;" data-factor-id="${f.id}">
                            <input type="checkbox" value="${f.id}" ${checked ? "checked" : ""} style="flex-shrink:0;" />
                            <span style="flex:1;">${Utils.escapeHtml(f.display_name || f.id)}</span>
                            <span class="text-muted" style="font-size:11px;">${Utils.escapeHtml(f.id)}</span>
                        </label>`;
                    }).join("")}
                </div>`;
        }).join("");

        listEl.innerHTML = html;

        listEl.querySelectorAll(".factor-chip").forEach((chip) => {
            const checkbox = chip.querySelector("input");
            const sync = () => {
                chip.classList.toggle("checked", checkbox.checked);
            };
            checkbox.addEventListener("change", sync);
            chip.addEventListener("click", (e) => {
                if (e.target.tagName !== "INPUT") {
                    renderFactorDetail(chip.dataset.factorId, true);
                }
            });
            sync();
        });

        // Auto-open detail for the first factor.
        const first = all[0];
        if (first) renderFactorDetail(first.id, false);
    } catch (e) {
        listEl.innerHTML = `<div class="alert alert-error">加载失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

function factorMetaById(id) {
    for (const cat of factorMetasCache) {
        const f = (cat.factors || []).find((x) => x.id === id);
        if (f) return f;
    }
    return null;
}

function renderFactorDetail(id, force = false) {
    const card = document.getElementById("factor-detail-card");
    const meta = factorMetaById(id);
    if (!meta) return;
    card.style.display = "block";
    const dirClass = meta.direction === "正向" || meta.direction === "positive"
        ? "text-success" : meta.direction === "负向" || meta.direction === "negative"
            ? "text-danger" : "";
    card.innerHTML = `
        <div class="card-title card-title-sm" style="margin-bottom:12px;">📌 ${Utils.escapeHtml(meta.display_name || meta.id)}</div>
        <div style="font-size:13px;line-height:1.9;">
            <div><span class="text-muted">类别:</span> ${Utils.escapeHtml(meta.category || "-")}</div>
            <div><span class="text-muted">方向:</span> <span class="${dirClass}">${Utils.escapeHtml(meta.direction || "-")}</span></div>
            <div><span class="text-muted">公式:</span> <code>${Utils.escapeHtml(meta.formula || "-")}</code></div>
            <div><span class="text-muted">描述:</span> ${Utils.escapeHtml(meta.description || "-")}</div>
            <div><span class="text-muted">实例:</span> <code>${Utils.escapeHtml(meta.id)}</code></div>
            ${meta.params && Object.keys(meta.params).length ? `<div><span class="text-muted">参数:</span>
                <code>${Object.entries(meta.params).map(([k, v]) => `${k}=${v}`).join(", ")}</code></div>` : ""}
        </div>`;
    if (!force) {
        // Auto-open detail for the first checked factor
        const firstChecked = document.querySelector("#factor-list input:checked");
        if (firstChecked && firstChecked.value !== id) return;
    }
}

function selectedFactorNames() {
    return Array.from(document.querySelectorAll("#factor-list input:checked")).map((c) => c.value);
}

async function runSelectedFactors() {
    const ids = selectedFactorNames();
    if (!ids.length) {
        Components.toast("请至少选择一个因子", "warning");
        return;
    }
    const horizon = parseInt(document.getElementById("factor-horizon").value, 10);
    const panel = document.getElementById("analysis-panel");
    panel.innerHTML = `<div class="loading"><div class="spinner"></div>计算中...</div>`;

    try {
        // Compute each factor instance's IC series and group returns in parallel
        const results = await Promise.all(
            ids.map((fid) =>
                API.computeFactor({ factor_id: fid, params: {}, horizon })
            )
        );
        // Correlation matrix (best effort), instance granularity by default.
        let correlation = null;
        try {
            const corrResp = await API.factorCorrelation({ granularity: "instance", factorIds: ids });
            correlation = corrResp || null;
        } catch (e) {
            correlation = null;
        }

        lastResults = { results, correlation, ids, horizon };
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
    const { correlation } = lastResults;
    if (!correlation) {
        panel.innerHTML = `<div class="empty-state">
            <div class="empty-icon">🔗</div>
            <div class="empty-title">无法计算相关性</div>
            <div class="empty-desc">因子相关性矩阵计算失败，可能是数据不足</div>
        </div>`;
        return;
    }
    panel.innerHTML = `
        <div class="flex-between" style="margin-bottom:8px;">
            <div class="text-muted" style="font-size:12px;">鼠标悬停查看相关系数，色阶：红(-1) ~ 白(0) ~ 绿(+1)</div>
            <div class="btn-group">
                <button class="btn btn-sm ${correlation.granularity === "instance" ? "btn-primary" : ""}" id="corr-btn-instance">按实例</button>
                <button class="btn btn-sm ${correlation.granularity === "class" ? "btn-primary" : ""}" id="corr-btn-class">按类</button>
            </div>
        </div>
        <div id="corr-chart" class="chart"></div>`;

    const el = document.getElementById("corr-chart");
    const labels = (correlation.labels || []).map((n) =>
        n.length > 12 ? n.slice(0, 12) + "…" : n
    );

    const draw = () => {
        Charts.render(el, () => ({
            tooltip: {
                position: "top",
                formatter: (p) => {
                    const v = p.value;
                    if (!Array.isArray(v) || typeof v[2] !== "number") return "";
                    return `<div class="tt-title">${Utils.escapeHtml((correlation.labels || [])[v[0]])} × ${Utils.escapeHtml((correlation.labels || [])[v[1]])}</div>
                        <div class="tt-row"><span class="tt-value">${v[2].toFixed(3)}</span></div>`;
                },
                className: "chart-tooltip-custom",
            },
            grid: { left: 90, right: 16, top: 16, bottom: 64 },
            xAxis: {
                type: "category",
                data: labels,
                splitArea: { show: true },
                axisLabel: { rotate: 40 },
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
                data: correlation.correlation_matrix
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
    };

    draw();

    async function fetchCorr(granularity) {
        panel.querySelector("#corr-chart").innerHTML = `<div class="loading"><div class="spinner"></div>计算中...</div>`;
        try {
            const resp = await API.factorCorrelation({
                granularity,
                factorIds: lastResults.ids, // class granularity derives classes server-side
            });
            correlation.granularity = resp.granularity;
            correlation.labels = resp.labels;
            correlation.correlation_matrix = resp.correlation_matrix;
            draw();
        } catch (e) {
            panel.querySelector("#corr-chart").innerHTML = `<div class="alert alert-error">计算失败: ${Utils.escapeHtml(e.message)}</div>`;
        }
    }

    panel.querySelector("#corr-btn-instance").addEventListener("click", () => fetchCorr("instance"));
    panel.querySelector("#corr-btn-class").addEventListener("click", () => fetchCorr("class"));
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
