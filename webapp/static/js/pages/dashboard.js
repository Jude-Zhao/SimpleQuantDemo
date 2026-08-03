/* Dashboard home page — three-row dashboard grid */

function renderDashboard(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">首页</h1>
            <div class="btn-group">
                <button class="btn btn-sm" id="btn-refresh-dashboard">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    刷新
                </button>
            </div>
        </div>

        <div class="grid-12">
            <div class="col-3 stat-card">
                <div class="stat-label">标的池数量</div>
                <div class="stat-value" id="stat-universe">-</div>
                <div class="stat-detail">ETF 池规模</div>
            </div>
            <div class="col-3 stat-card">
                <div class="stat-label">因子数量</div>
                <div class="stat-value" id="stat-factors">-</div>
                <div class="stat-detail">插件式因子库</div>
            </div>
            <div class="col-3 stat-card">
                <div class="stat-label">今日运行次数</div>
                <div class="stat-value" id="stat-runs">-</div>
                <div class="stat-detail">策略回测任务</div>
            </div>
            <div class="col-3 stat-card">
                <div class="stat-label">系统状态</div>
                <div class="stat-value stat-value-sm" id="stat-status">-</div>
                <div class="stat-detail" id="stat-detail">-</div>
            </div>

            <div class="col-8">
                <div class="card">
                    <div class="card-title">
                        ETF 走势
                        <span class="text-muted" id="etf-freshness" style="font-size:12px;font-weight:400;"></span>
                    </div>
                    <div class="flex-between" style="margin-bottom:12px;">
                        <div class="btn-group" id="etf-tabs">
                            <button class="tab-pill" data-period="5">近 5 日</button>
                            <button class="tab-pill active" data-period="20">近 20 日</button>
                            <button class="tab-pill" data-period="60">近 60 日</button>
                        </div>
                        <select id="etf-select" class="form-select" multiple style="width:auto;min-width:180px;height:120px;" title="Ctrl/Shift 点击多选"></select>
                    </div>
                    <div id="etf-chart" class="chart"></div>
                </div>
            </div>

            <div class="col-4">
                <div class="card">
                    <div class="card-title">因子 IC 排名</div>
                    <div id="factor-ranking-chart" class="chart-sm"></div>
                </div>
                <div class="card">
                    <div class="card-title">最近策略运行</div>
                    <table class="table" id="recent-runs-table" style="margin:-8px -4px;">
                        <thead><tr><th>策略</th><th>状态</th><th>总收益</th><th>时间</th></tr></thead>
                        <tbody><tr><td colspan="4" class="text-muted">加载中...</td></tr></tbody>
                    </table>
                </div>
            </div>

            <div class="col-12">
                <div class="card">
                    <div class="card-title">净值曲线对比
                        <span class="text-muted" id="nav-compare-hint" style="font-size:12px;font-weight:400;">最近 3 次成功运行</span>
                    </div>
                    <div id="nav-compare-chart" class="chart"></div>
                </div>
            </div>
        </div>
    `;

    // Skeleton placeholder while loading
    document.getElementById("etf-chart").parentElement.insertAdjacentHTML(
        "beforeend",
        `<div id="etf-skeleton">${Utils.skeleton(2)}</div>`
    );

    loadStats();
    loadEtfSelect();
    loadFactorRanking();
    loadRecentRuns();
    loadNavCompare();

    document.getElementById("btn-refresh-dashboard").addEventListener("click", () => {
        loadStats();
        loadFactorRanking();
        loadRecentRuns();
        loadNavCompare();
        // Re-trigger ETF chart with current selection
        const sel = document.getElementById("etf-select");
        if (sel && selectedEtfCodes()) {
            loadEtfChart(selectedEtfCodes(), currentPeriod());
        }
        Components.toast("看板数据已刷新", "success", 1500);
    });

    // Period tabs
    const tabs = document.querySelectorAll("#etf-tabs .tab-pill");
    tabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            tabs.forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            if (selectedEtfCodes()) {
                loadEtfChart(selectedEtfCodes(), currentPeriod());
            }
        });
    });
}

function currentPeriod() {
    const active = document.querySelector("#etf-tabs .tab-pill.active");
    return parseInt(active?.dataset.period || "20", 10);
}

async function loadStats() {
    try {
        const stats = await API.request("/api/dashboard/stats");
        Utils.animateNumber(document.getElementById("stat-universe"), stats.universe_count);
        Utils.animateNumber(document.getElementById("stat-factors"), stats.factor_count);
        Utils.animateNumber(document.getElementById("stat-runs"), stats.run_count_today);
        const statusEl = document.getElementById("stat-status");
        statusEl.textContent = "正常";
        statusEl.style.color = "var(--success)";
        document.getElementById("stat-detail").textContent = "API 服务运行中";
    } catch (e) {
        const statusEl = document.getElementById("stat-status");
        statusEl.textContent = "异常";
        statusEl.style.color = "var(--danger)";
        document.getElementById("stat-detail").textContent = e.message;
    }
}

async function loadEtfSelect() {
    try {
        const universe = await API.getUniverse();
        const select = document.getElementById("etf-select");
        const codes = universe.map((u) => u.sec_code);
        select.innerHTML = codes
            .map((c) => `<option value="${c}">${c}</option>`)
            .join("");
        if (codes.length) {
            // Preselect up to 5 codes for the initial multi-line chart
            for (let i = 0; i < Math.min(5, select.options.length); i++) {
                select.options[i].selected = true;
            }
            loadEtfChart(selectedEtfCodes(), currentPeriod());
            select.addEventListener("change", () => {
                if (selectedEtfCodes().length) {
                    loadEtfChart(selectedEtfCodes(), currentPeriod());
                }
            });
        } else {
            document.getElementById("etf-chart").innerHTML =
                `<div class="empty-state"><div class="empty-title">标的池为空</div><div class="empty-desc">请先在标的池页面添加 ETF</div></div>`;
            document.getElementById("etf-skeleton")?.remove();
        }
    } catch (e) {
        document.getElementById("etf-select").innerHTML = "<option>加载失败</option>";
        document.getElementById("etf-skeleton")?.remove();
    }
}

function selectedEtfCodes() {
    const select = document.getElementById("etf-select");
    if (!select) return "";
    const codes = Array.from(select.selectedOptions).map((o) => o.value);
    return codes.join(",");
}

function loadEtfChart(codes, days) {
    const el = document.getElementById("etf-chart");
    if (!el) return;
    const skeletonEl = document.getElementById("etf-skeleton");
    if (skeletonEl) skeletonEl.style.display = "none";

    API.request(`/api/dashboard/etf-price?codes=${encodeURIComponent(codes)}`)
        .then((points) => {
            // Keep the last N trading days
            const byCode = {};
            points.forEach((p) => {
                if (!byCode[p.sec_code]) byCode[p.sec_code] = [];
                byCode[p.sec_code].push([p.date, p.close]);
            });
            const allDates = [...new Set(points.map((p) => p.date))].sort();
            const cutoff = allDates[Math.max(0, allDates.length - days)];
            const filtered = Object.entries(byCode).map(([code, pts]) => [
                code,
                pts.filter(([d]) => d >= cutoff),
            ]);
            const dates = [...new Set(filtered.flatMap(([, pts]) => pts.map((p) => p[0])))].sort();

            const freshness = allDates.length ? `数据更新至 ${allDates[allDates.length - 1]}` : "";
            const freshEl = document.getElementById("etf-freshness");
            if (freshEl) freshEl.textContent = freshness;

            Charts.render(el, () => ({
                tooltip: {
                    trigger: "axis",
                    formatter: (params) => {
                        let html = `<div class="tt-title">${params[0]?.axisValue || ""}</div>`;
                        params.forEach((p) => {
                            const color = p.color || Charts.semanticColor("zero");
                            html += `<div class="tt-row">
                                <span class="tt-dot" style="background:${color}"></span>
                                <span>${Utils.escapeHtml(p.seriesName)}</span>
                                <span class="tt-value">${Number(p.value).toFixed(3)}</span>
                            </div>`;
                        });
                        return html;
                    },
                    className: "chart-tooltip-custom",
                },
                legend: { data: filtered.map(([code]) => code), top: 0 },
                grid: { left: 56, right: 24, top: 36, bottom: 56 },
                xAxis: { type: "category", data: dates },
                yAxis: { type: "value", scale: true, axisLabel: { formatter: (v) => v.toFixed(2) } },
                dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
                series: filtered.map(([code, pts]) => ({
                    name: code,
                    type: "line",
                    data: pts.map((p) => p[1]),
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: 1.5 },
                    areaStyle: { opacity: 0.06 },
                })),
                extra: {},
            }));
        })
        .catch((e) => {
            el.innerHTML = `<div class="alert alert-error">加载行情失败: ${Utils.escapeHtml(e.message)}</div>`;
        });
}

function loadFactorRanking() {
    const el = document.getElementById("factor-ranking-chart");
    if (!el) return;

    API.request("/api/dashboard/factor-ranking")
        .then((ranking) => {
            if (!ranking.length) {
                el.innerHTML = `<div class="empty-state"><div class="empty-title">暂无因子数据</div></div>`;
                return;
            }
            const sorted = [...ranking].sort((a, b) => b.ic_mean - a.ic_mean);
            const names = sorted.map((r) => r.display_name || r.name);
            const values = sorted.map((r) => r.ic_mean);
            const up = Charts.semanticColor("up");
            const down = Charts.semanticColor("down");

            Charts.render(el, () => ({
                tooltip: {
                    trigger: "axis",
                    axisPointer: { type: "shadow" },
                    formatter: (params) => {
                        const p = params[0];
                        const item = sorted[p.dataIndex];
                        return `<div class="tt-title">${Utils.escapeHtml(p.name)}</div>
                            <div class="tt-row"><span>IC 均值</span><span class="tt-value">${item.ic_mean.toFixed(4)}</span></div>
                            <div class="tt-row"><span>ICIR</span><span class="tt-value">${item.icir.toFixed(4)}</span></div>`;
                    },
                    className: "chart-tooltip-custom",
                },
                grid: { left: 96, right: 40, top: 8, bottom: 24 },
                xAxis: { type: "value", axisLabel: { formatter: (v) => v.toFixed(2) } },
                yAxis: { type: "category", data: names },
                series: [{
                    type: "bar",
                    data: values.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? up : down } })),
                    barWidth: 12,
                    label: {
                        show: true,
                        position: "right",
                        formatter: (p) => p.value.toFixed(4),
                        fontSize: 11,
                    },
                }],
                extra: {},
            }));
        })
        .catch((e) => {
            el.innerHTML = `<div class="alert alert-error">加载排名失败: ${Utils.escapeHtml(e.message)}</div>`;
        });
}

function loadRecentRuns() {
    const tbody = document.querySelector("#recent-runs-table tbody");
    if (!tbody) return;
    API.request("/api/dashboard/recent-runs?limit=6")
        .then((runs) => {
            if (!runs.length) {
                tbody.innerHTML = `<tr><td colspan="4" class="text-muted">暂无运行记录</td></tr>`;
                return;
            }
            const badgeMap = {
                success: "badge-success",
                failed: "badge-danger",
                running: "badge-info",
                pending: "badge-muted",
            };
            tbody.innerHTML = runs
                .map((r) => `
                    <tr>
                        <td>${Utils.escapeHtml(r.strategy_type)}</td>
                        <td><span class="badge ${badgeMap[r.status] || "badge-muted"}">${Utils.escapeHtml(r.status)}</span></td>
                        <td class="${r.total_return !== null && r.total_return !== undefined ? (r.total_return >= 0 ? "success-text" : "error-text") : ""}">${Utils.formatPct(r.total_return)}</td>
                        <td class="text-muted" style="font-size:12px;">${Utils.formatDate(r.created_at)}</td>
                    </tr>`)
                .join("");
        })
        .catch((e) => {
            tbody.innerHTML = `<tr><td colspan="4" class="text-muted">加载失败: ${Utils.escapeHtml(e.message)}</td></tr>`;
        });
}

function loadNavCompare() {
    const el = document.getElementById("nav-compare-chart");
    if (!el) return;
    const hint = document.getElementById("nav-compare-hint");

    API.request("/api/dashboard/recent-runs?limit=6")
        .then((runs) => {
            const successRuns = runs.filter((r) => r.status === "success").slice(0, 3);
            if (!successRuns.length) {
                el.innerHTML = `<div class="empty-state"><div class="empty-title">暂无净值数据</div><div class="empty-desc">运行策略后这里将展示净值曲线对比</div></div>`;
                return;
            }
            hint.textContent = `最近 ${successRuns.length} 次成功运行`;

            return Promise.all(
                successRuns.map((r) =>
                    API.request(`/api/strategies/runs/${r.id}`).catch(() => null)
                )
            );
        })
        .then((details) => {
            const valid = (details || []).filter(Boolean);
            const series = valid.map((d) => {
                const curve = d.result_summary?.equity_curve || d.result_summary?.nav_series || {};
                return {
                    name: `#${d.id} ${d.strategy_type}`,
                    curve: Object.entries(curve).sort((a, b) => a[0].localeCompare(b[0])),
                };
            });
            const allDates = [...new Set(series.flatMap((s) => s.curve.map(([d]) => d)))]
                .filter(Boolean)
                .sort();

            if (!allDates.length || !series.length) {
                el.innerHTML = `<div class="empty-state"><div class="empty-title">暂无净值数据</div><div class="empty-desc">运行策略后这里将展示净值曲线对比</div></div>`;
                return;
            }

            Charts.render(el, () => ({
                tooltip: { trigger: "axis", className: "chart-tooltip-custom" },
                legend: { top: 0 },
                grid: { left: 64, right: 24, top: 36, bottom: 56 },
                xAxis: { type: "category", data: allDates },
                yAxis: { type: "value", scale: true },
                dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
                series: series.map((s, i) => ({
                    name: s.name,
                    type: "line",
                    data: allDates.map((d) => {
                        const hit = s.curve.find(([cd]) => cd === d);
                        return hit ? hit[1] : null;
                    }),
                    connectNulls: true,
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: i === 0 ? 2.5 : 1.5 },
                    areaStyle: i === 0 ? { opacity: 0.06 } : undefined,
                })),
                extra: {},
            }));
        })
        .catch((e) => {
            el.innerHTML = `<div class="alert alert-error">加载净值对比失败: ${Utils.escapeHtml(e.message)}</div>`;
        });
}

window.renderDashboard = renderDashboard;
