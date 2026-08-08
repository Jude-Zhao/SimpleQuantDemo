/* Dashboard home page — stat cards + returns ranking + RankIC ranking + recent runs */

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
        </div>

        <div class="card" style="margin-top:var(--space-4);">
            <div class="flex-between" style="flex-wrap:wrap;gap:var(--space-3);">
                <div class="card-title" style="margin:0;">期间收益率排名</div>
                <div class="form-group" style="margin:0;">
                    <select id="ret-days" class="form-select" style="width:auto;">
                        <option value="5">近 5 日</option>
                        <option value="20" selected>近 20 日</option>
                        <option value="60">近 60 日</option>
                        <option value="120">近 120 日</option>
                    </select>
                </div>
            </div>
            <div class="text-muted" style="font-size:12px;margin:8px 0 12px;" id="ret-rank-hint">加载中...</div>
            <div class="grid-12">
                <div class="col-6">
                    <div class="card-title card-title-sm">动量 · TOP10 涨幅</div>
                    <div id="momentum-chart" class="chart"></div>
                </div>
                <div class="col-6">
                    <div class="card-title card-title-sm">反转 · BOTTOM10 跌幅</div>
                    <div id="reversal-chart" class="chart"></div>
                </div>
            </div>
        </div>

        <div class="grid-12" style="margin-top:var(--space-4);">
            <div class="col-4">
                <div class="card">
                    <div class="card-title">因子 RankIC 排名</div>
                    <div id="factor-ranking-chart" class="chart-sm"></div>
                </div>
            </div>
            <div class="col-8">
                <div class="card">
                    <div class="card-title">最近策略运行</div>
                    <table class="table" id="recent-runs-table" style="margin:-8px -4px;">
                        <thead><tr><th>策略</th><th>状态</th><th>总收益</th><th>时间</th></tr></thead>
                        <tbody><tr><td colspan="4" class="text-muted">加载中...</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    loadStats();
    loadReturnsRanking();
    loadFactorRanking();
    loadRecentRuns();

    document.getElementById("btn-refresh-dashboard").addEventListener("click", () => {
        loadStats();
        loadReturnsRanking();
        loadFactorRanking();
        loadRecentRuns();
        Components.toast("看板数据已刷新", "success", 1500);
    });

    document.getElementById("ret-days").addEventListener("change", () => {
        loadReturnsRanking();
    });
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

function loadReturnsRanking() {
    const days = parseInt(document.getElementById("ret-days").value, 10);
    const momentumEl = document.getElementById("momentum-chart");
    const reversalEl = document.getElementById("reversal-chart");
    if (!momentumEl || !reversalEl) return;

    momentumEl.innerHTML = Utils.skeleton(8);
    reversalEl.innerHTML = Utils.skeleton(8);

    API.request(`/api/dashboard/returns-ranking?days=${days}`)
        .then((data) => {
            const hint = document.getElementById("ret-rank-hint");
            hint.textContent = data.as_of
                ? `截至 ${data.as_of}，标的池 ${data.days} 个交易日区间收益率`
                : "暂无数据";

            if (!data.momentum.length) {
                momentumEl.innerHTML = `<div class="empty-state"><div class="empty-title">暂无数据</div><div class="empty-desc">请先在标的池添加并同步行情</div></div>`;
                reversalEl.innerHTML = "";
                return;
            }
            renderReturnBar(momentumEl, data.momentum);
            renderReturnBar(reversalEl, data.reversal);
        })
        .catch((e) => {
            momentumEl.innerHTML = `<div class="alert alert-error">加载失败: ${Utils.escapeHtml(e.message)}</div>`;
            reversalEl.innerHTML = "";
        });
}

function renderReturnBar(el, items) {
    const up = Charts.semanticColor("up");
    const down = Charts.semanticColor("down");
    // Reverse so the first item appears at the top of the category axis.
    const ordered = [...items].reverse();

    Charts.render(el, () => ({
        tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            formatter: (params) => {
                const p = params[0];
                const item = ordered[p.dataIndex];
                return `<div class="tt-title">${Utils.escapeHtml(item.sec_name)} (${Utils.escapeHtml(item.sec_code)})</div>
                    <div class="tt-row"><span>区间收益</span><span class="tt-value">${Utils.formatPct(item.return_pct)}</span></div>`;
            },
            className: "chart-tooltip-custom",
        },
        grid: { left: 96, right: 64, top: 8, bottom: 24 },
        xAxis: { type: "value", axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%" } },
        yAxis: { type: "category", data: ordered.map((it) => it.sec_code) },
        series: [{
            type: "bar",
            data: ordered.map((it) => ({
                value: it.return_pct,
                itemStyle: { color: it.return_pct >= 0 ? up : down, opacity: 0.9 },
            })),
            barWidth: 14,
            label: {
                show: true,
                position: "right",
                formatter: (p) => (p.value * 100).toFixed(1) + "%",
                fontSize: 11,
            },
        }],
        extra: {},
    }));
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
            const sorted = [...ranking].sort((a, b) => b.rank_ic_mean - a.rank_ic_mean);
            const names = sorted.map((r) => r.display_name || r.name);
            const values = sorted.map((r) => r.rank_ic_mean);
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
                            <div class="tt-row"><span>RankIC 均值</span><span class="tt-value">${item.rank_ic_mean.toFixed(4)}</span></div>
                            <div class="tt-row"><span>RankICIR</span><span class="tt-value">${item.rank_icir.toFixed(4)}</span></div>`;
                    },
                    className: "chart-tooltip-custom",
                },
                grid: { left: 96, right: 48, top: 8, bottom: 24 },
                xAxis: { type: "value", axisLabel: { formatter: (v) => v.toFixed(2) } },
                yAxis: { type: "category", data: names },
                series: [{
                    type: "bar",
                    data: values.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? up : down, opacity: 0.9 } })),
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

window.renderDashboard = renderDashboard;