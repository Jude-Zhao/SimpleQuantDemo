/* Macro data page — field browsing, multi-select line chart, sync */

let macroFieldsCache = [];
let selectedFields = new Set();
let activeFrequency = "daily";

const CATEGORY_ICONS = {
    "利率": "💰",
    "汇率": "💱",
    "商品": "🛢️",
    "估值": "📊",
    "波动率": "📉",
    "海外": "🌍",
    "货币": "🏦",
    "经济": "🏭",
};

function renderMacro(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">宏观数据</h1>
            <div class="btn-group">
                <div class="tab-bar-pill" id="macro-freq-toggle" style="margin:0;">
                    <button class="tab-pill active" data-freq="daily">日频</button>
                    <button class="tab-pill" data-freq="monthly">月频</button>
                </div>
                <button class="btn btn-sm" id="btn-refresh-macro">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    刷新
                </button>
                <button class="btn btn-sm btn-primary" id="btn-sync-macro">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    同步数据
                </button>
            </div>
        </div>

        <div id="macro-sync-progress" style="display:none;">
            <div class="card" style="border-color:var(--accent);">
                <div class="flex-between" style="margin-bottom:6px;">
                    <span id="macro-sync-status" style="font-size:13px;">准备中...</span>
                    <span id="macro-sync-percent" style="font-size:13px; color:var(--text-muted);">0%</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="macro-sync-fill" style="width:0%;"></div>
                </div>
                <p id="macro-sync-result" style="font-size:13px; color:var(--text-muted); margin-top:8px; display:none;"></p>
            </div>
        </div>

        <div class="card">
            <div class="card-title">字段选择 <span class="badge badge-accent" id="macro-selected-count">0</span></div>
            <div id="macro-field-chips" class="flex-between" style="justify-content:flex-start; gap:8px; flex-wrap:wrap;"></div>
        </div>

        <div class="card">
            <div class="card-title">走势图</div>
            <div class="form-row" style="grid-template-columns: 1fr 1fr auto; align-items:end; margin-bottom:12px;">
                <div class="form-group" style="margin:0;">
                    <label>起始日期</label>
                    <input type="date" id="macro-start-date" class="form-control" value="2024-01-01" />
                </div>
                <div class="form-group" style="margin:0;">
                    <label>结束日期</label>
                    <input type="date" id="macro-end-date" class="form-control" />
                </div>
                <button class="btn btn-sm" id="btn-apply-macro-range">应用</button>
            </div>
            <div id="macro-chart" class="chart" style="height:420px;"></div>
            <div id="macro-chart-empty" class="empty-state" style="display:none;">
                <div class="empty-icon">📈</div>
                <div class="empty-title">请选择要查看的字段</div>
                <div class="empty-desc">在下方字段列表中选择 1 个或多个字段</div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">数据表格 <span class="badge badge-muted" id="macro-table-count">0 行</span></div>
            <div class="table-wrap" style="max-height:360px; overflow-y:auto;">
                <table class="table" id="macro-table">
                    <thead><tr id="macro-table-head"></tr></thead>
                    <tbody id="macro-table-body"></tbody>
                </table>
            </div>
        </div>
    `;

    // Default end date = today
    const endInput = document.getElementById("macro-end-date");
    if (!endInput.value) {
        endInput.value = new Date().toISOString().split("T")[0];
    }

    loadMacroFields();
    setupMacroEvents();
}

function setupMacroEvents() {
    // Frequency toggle
    document.querySelectorAll("#macro-freq-toggle .tab-pill").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#macro-freq-toggle .tab-pill").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            activeFrequency = btn.dataset.freq;
            selectedFields.clear();
            document.getElementById("macro-selected-count").textContent = "0";
            loadMacroFields();
        });
    });

    document.getElementById("btn-refresh-macro").addEventListener("click", () => {
        loadMacroFields();
        loadMacroData();
    });

    document.getElementById("btn-apply-macro-range").addEventListener("click", loadMacroData);

    document.getElementById("btn-sync-macro").addEventListener("click", syncMacroData);
}

async function loadMacroFields() {
    const chipsEl = document.getElementById("macro-field-chips");
    chipsEl.innerHTML = Utils.skeleton(2);
    try {
        const fields = await API.macroFields(activeFrequency);
        macroFieldsCache = fields;

        const grouped = {};
        fields.forEach((f) => {
            (grouped[f.category] = grouped[f.category] || []).push(f);
        });

        let html = "";
        Object.entries(grouped).forEach(([category, list]) => {
            html += `<div style="width:100%; font-size:12px; color:var(--text-muted); font-weight:600; margin-top:4px;">
                ${CATEGORY_ICONS[category] || "📌"} ${category}
            </div>`;
            list.forEach((f) => {
                const checked = selectedFields.has(f.name) ? "checked" : "";
                html += `<label class="factor-chip ${checked}">
                    <input type="checkbox" value="${f.name}" ${checked} />
                    ${Utils.escapeHtml(f.label)}
                    <span style="font-size:11px; color:var(--text-muted);">${Utils.escapeHtml(f.unit)}</span>
                </label>`;
            });
        });
        chipsEl.innerHTML = html;

        chipsEl.querySelectorAll(".factor-chip").forEach((chip) => {
            const input = chip.querySelector("input");
            input.addEventListener("change", () => {
                chip.classList.toggle("checked", input.checked);
                if (input.checked) selectedFields.add(input.value);
                else selectedFields.delete(input.value);
                document.getElementById("macro-selected-count").textContent = selectedFields.size;
                loadMacroData();
            });
        });

        // Auto-select first field on first load
        if (selectedFields.size === 0 && fields.length > 0) {
            const first = fields[0];
            selectedFields.add(first.name);
            const chip = chipsEl.querySelector(`input[value="${first.name}"]`);
            if (chip) {
                chip.checked = true;
                chip.closest(".factor-chip").classList.add("checked");
                document.getElementById("macro-selected-count").textContent = "1";
            }
        }

        loadMacroData();
    } catch (e) {
        chipsEl.innerHTML = `<div class="alert alert-error">加载字段失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

async function loadMacroData() {
    if (selectedFields.size === 0) {
        document.getElementById("macro-chart").style.display = "none";
        document.getElementById("macro-chart-empty").style.display = "block";
        renderMacroTable({ dates: [], fields: {} });
        return;
    }

    const startDate = document.getElementById("macro-start-date").value;
    const endDate = document.getElementById("macro-end-date").value;
    const fieldList = [...selectedFields];

    try {
        let data;
        if (activeFrequency === "daily") {
            data = await API.macroDaily({ start_date: startDate, end_date: endDate, fields: fieldList });
        } else {
            const startMonth = startDate ? startDate.slice(0, 7) : undefined;
            const endMonth = endDate ? endDate.slice(0, 7) : undefined;
            data = await API.macroMonthly({ start_month: startMonth, end_month: endMonth, fields: fieldList });
        }

        document.getElementById("macro-chart").style.display = "block";
        document.getElementById("macro-chart-empty").style.display = "none";
        renderMacroChart(data);
        renderMacroTable(data);
    } catch (e) {
        document.getElementById("macro-chart").style.display = "none";
        document.getElementById("macro-chart-empty").style.display = "block";
        document.getElementById("macro-chart-empty").innerHTML =
            `<div class="empty-icon">⚠️</div><div class="empty-title">加载失败</div><div class="empty-desc">${Utils.escapeHtml(e.message)}</div>`;
    }
}

function renderMacroChart(data) {
    const el = document.getElementById("macro-chart");
    const dates = data.dates || [];
    const fields = data.fields || {};

    const series = Object.entries(fields).map(([name, values]) => {
        const meta = macroFieldsCache.find((f) => f.name === name) || {};
        return {
            name: meta.label || name,
            type: "line",
            smooth: true,
            showSymbol: false,
            data: values,
        };
    });

    Charts.render(el, () => ({
        tooltip: { trigger: "axis" },
        legend: { top: 8 },
        grid: { left: 64, right: 24, top: 48, bottom: 56 },
        xAxis: { type: "category", data: dates, axisLabel: { rotate: 30 } },
        yAxis: { type: "value", scale: true },
        series,
        dataZoom: [
            { type: "inside", start: 0, end: 100 },
            { type: "slider", start: 0, end: 100, bottom: 8 },
        ],
    }));
}

function renderMacroTable(data) {
    const dates = data.dates || [];
    const fields = data.fields || {};
    const head = document.getElementById("macro-table-head");
    const body = document.getElementById("macro-table-body");
    document.getElementById("macro-table-count").textContent = `${dates.length} 行`;

    const fieldNames = Object.keys(fields);
    head.innerHTML = `<th>日期</th>` + fieldNames.map((n) => {
        const meta = macroFieldsCache.find((f) => f.name === n) || {};
        return `<th>${Utils.escapeHtml(meta.label || n)}</th>`;
    }).join("");

    if (dates.length === 0) {
        body.innerHTML = `<tr><td colspan="${fieldNames.length + 1}" style="text-align:center; color:var(--text-muted);">暂无数据</td></tr>`;
        return;
    }

    // Show last 200 rows max
    const maxRows = 200;
    const startIdx = Math.max(0, dates.length - maxRows);
    let html = "";
    for (let i = startIdx; i < dates.length; i++) {
        html += `<tr><td class="mono">${dates[i]}</td>`;
        fieldNames.forEach((n) => {
            const v = fields[n][i];
            html += `<td class="mono">${v === null || v === undefined ? "-" : Number(v).toFixed(4)}</td>`;
        });
        html += `</tr>`;
    }
    body.innerHTML = html;
}

async function syncMacroData() {
    const ok = await Components.runMacroSync({
        frequency: activeFrequency,
        btn: document.getElementById("btn-sync-macro"),
        wrap: document.getElementById("macro-sync-progress"),
        fill: document.getElementById("macro-sync-fill"),
        statusText: document.getElementById("macro-sync-status"),
        percentText: document.getElementById("macro-sync-percent"),
        resultText: document.getElementById("macro-sync-result"),
    });
    if (ok) {
        Components.toast("宏观数据同步完成", "success");
        // Refresh chart after sync
        setTimeout(() => { loadMacroFields(); loadMacroData(); }, 500);
    } else {
        Components.toast("宏观数据同步失败", "error");
    }
}

window.renderMacro = renderMacro;
