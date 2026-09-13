/* Settings page — datasource status cards */

function renderSettings(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">设置</h1>
            <div class="btn-group">
                <button class="btn btn-sm" id="btn-refresh-settings">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    刷新
                </button>
            </div>
        </div>

        <div class="card">
            <div class="card-title">数据源状态</div>
            <div id="datasource-cards" class="grid-12" style="gap:12px;">${Utils.skeleton(2)}</div>
        </div>

        <div class="card">
            <div class="card-title">缓存配置</div>
            <div id="cache-config" class="grid-12" style="gap:12px;">${Utils.skeleton(2)}</div>
        </div>

        <div class="card">
            <div class="card-title">系统信息</div>
            <div class="form-row" style="grid-template-columns: repeat(3, 1fr);">
                <div class="form-group">
                    <label>版本</label>
                    <input id="setting-version" class="form-control" readonly />
                </div>
                <div class="form-group" style="grid-column: span 2;">
                    <label>数据库地址</label>
                    <input id="setting-db-url" class="form-control mono" readonly />
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">数据同步</div>
            <div class="form-row" style="grid-template-columns: 1fr 1fr auto;">
                <div class="form-group">
                    <label>起始日期</label>
                    <input type="date" id="sync-start-date" class="form-control" value="2021-01-04" />
                </div>
                <div class="form-group">
                    <label>结束日期</label>
                    <input type="date" id="sync-end-date" class="form-control" />
                </div>
                <div class="form-group" style="align-self: end;">
                    <button class="btn btn-primary" id="btn-sync-etf">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                        同步行情数据
                    </button>
                </div>
            </div>
            <div id="sync-progress-wrap" style="display:none; margin-top:16px;">
                <div class="flex-between" style="margin-bottom:6px;">
                    <span id="sync-status-text" style="font-size:13px;">准备中...</span>
                    <span id="sync-percent" style="font-size:13px; color: var(--text-muted);">0%</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="sync-progress-fill" style="width: 0%;"></div>
                </div>
                <p id="sync-result-text" class="text-muted mt-8" style="font-size:13px; display:none;"></p>
            </div>
            <p class="text-muted mt-8" style="font-size:13px;">
                全量同步将删除指定日期范围内的旧数据并重新拉取，确保后复权数据最新。默认从 2021-01-04 同步至最新交易日。
            </p>

            <div class="card-title" style="margin-top:20px;">宏观数据同步</div>
            <div class="form-row" style="grid-template-columns: 1fr 1fr auto;">
                <div class="form-group">
                    <label>频率</label>
                    <select id="macro-sync-freq" class="form-select">
                        <option value="daily">日频</option>
                        <option value="monthly">月频</option>
                    </select>
                </div>
                <div class="form-group"></div>
                <div class="form-group" style="align-self: end;">
                    <button class="btn btn-primary" id="btn-sync-macro">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                        同步宏观数据
                    </button>
                </div>
            </div>
            <div id="macro-sync-progress-wrap" style="display:none; margin-top:16px;">
                <div class="flex-between" style="margin-bottom:6px;">
                    <span id="macro-sync-status" style="font-size:13px;">准备中...</span>
                    <span id="macro-sync-percent" style="font-size:13px; color: var(--text-muted);">0%</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="macro-sync-fill" style="width: 0%;"></div>
                </div>
                <p id="macro-sync-result" class="text-muted mt-8" style="font-size:13px; display:none;"></p>
            </div>
            <p class="text-muted mt-8" style="font-size:13px;">
                全量同步将删除旧数据并重新拉取宏观指标（约需 1-3 分钟）。
            </p>
        </div>

        <div class="card">
            <div class="card-title">操作</div>
            <div class="btn-group">
                <button class="btn" id="btn-clear-cache">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
                    清空本地缓存
                </button>
            </div>
            <p class="text-muted mt-8" style="font-size:13px;">清空缓存会要求下次行情请求重新从数据源拉取。</p>
        </div>
    `;

    loadSettings();
    setupSyncButton();
    setupMacroSyncButton();
    document.getElementById("btn-refresh-settings").addEventListener("click", loadSettings);
    document.getElementById("btn-clear-cache").addEventListener("click", async () => {
        const ok = await Components.confirmDialog("确定要清空本地行情缓存吗？", { okText: "清空", danger: true });
        if (!ok) return;
        try {
            localStorage.clear();
            Components.toast("缓存已清空", "success");
        } catch (e) {
            Components.toast(`清空失败: ${e.message}`, "error");
        }
    });
}

function dsCard(name, status, detail, ok) {
    return `
        <div class="col-4 stat-card" style="padding:16px;">
            <div class="flex-between" style="margin-bottom:8px;">
                <span style="font-weight:600;font-size:14px;">${Utils.escapeHtml(name)}</span>
                <span class="status-dot ${ok ? "online" : "offline"}" style="display:inline-block;"></span>
            </div>
            <div style="font-size:14px;${ok ? "" : "color:var(--danger);"}">${Utils.escapeHtml(status)}</div>
            <div class="stat-detail">${Utils.escapeHtml(detail)}</div>
        </div>`;
}

async function loadSettings() {
    try {
        const data = await API.request("/api/settings");

        const ds = data.datasource;
        document.getElementById("datasource-cards").innerHTML = `
            ${dsCard("主数据源", ds.primary, "行情获取首选", true)}
            ${dsCard("SQLite 数据库", data.system.database_connected ? "已连接" : "未连接",
                data.system.database_connected ? "数据持久化正常" : "数据库不可用", data.system.database_connected)}`;

        const cacheEnabled = ds.cache_enabled ? "启用" : "禁用";
        document.getElementById("cache-config").innerHTML = `
            ${dsCard("缓存开关", cacheEnabled, "行情数据本地缓存", ds.cache_enabled)}
            ${dsCard("日线缓存", `${ds.cache_days_daily} 天`, "日线数据保留天数", true)}`;

        document.getElementById("setting-version").value = data.system.version;
        document.getElementById("setting-db-url").value = data.system.database_url;
    } catch (e) {
        document.getElementById("datasource-cards").innerHTML =
            `<div class="col-12"><div class="alert alert-error">加载设置失败: ${Utils.escapeHtml(e.message)}</div></div>`;
        document.getElementById("setting-version").value = `加载失败: ${e.message}`;
    }
}

function setupSyncButton() {
    const btn = document.getElementById("btn-sync-etf");
    const progressWrap = document.getElementById("sync-progress-wrap");
    const progressFill = document.getElementById("sync-progress-fill");
    const statusText = document.getElementById("sync-status-text");
    const percentText = document.getElementById("sync-percent");
    const resultText = document.getElementById("sync-result-text");

    // Set default end date to today
    const endInput = document.getElementById("sync-end-date");
    if (!endInput.value) {
        endInput.value = new Date().toISOString().split("T")[0];
    }

    btn.addEventListener("click", async () => {
        const startDate = document.getElementById("sync-start-date").value;
        const endDate = endInput.value;

        const ok = await Components.confirmDialog(
            `确定要全量同步行情数据吗？<br><br>范围：${startDate} ~ ${endDate}<br>将删除旧数据并重新拉取，确保后复权数据最新。`,
            { okText: "开始同步", okClass: "btn-primary" }
        );
        if (!ok) return;

        btn.disabled = true;
        btn.innerHTML = `<svg class="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg> 同步中...`;
        progressWrap.style.display = "block";
        resultText.style.display = "none";
        progressFill.style.width = "0%";
        percentText.textContent = "0%";
        statusText.textContent = "准备中...";

        try {
            const task = await API.syncEtf({
                start_date: startDate,
                end_date: endDate,
                period: "daily",
            });

            const finalTask = await API.pollSyncTask(task.task_id, (t) => {
                const pct = t.total > 0 ? Math.round((t.current / t.total) * 100) : 0;
                progressFill.style.width = pct + "%";
                percentText.textContent = pct + "%";
                statusText.textContent = t.message;
            });

            if (finalTask.status === "completed") {
                const r = finalTask.result || {};
                resultText.style.display = "block";
                resultText.innerHTML = `✅ 同步完成：成功 <strong>${r.success_count || 0}</strong> 只，失败 <strong>${r.failed_count || 0}</strong> 只，熔断跳过 <strong>${r.skipped_count || 0}</strong> 只，共 <strong>${r.total_rows || 0}</strong> 条数据`;
                resultText.innerHTML += renderSyncDetail(r);
                Components.toast("行情数据同步完成",
                    (r.failed_count || 0) + (r.skipped_count || 0) > 0 ? "warning" : "success");
            } else {
                resultText.style.display = "block";
                resultText.innerHTML = `❌ 同步失败：${Utils.escapeHtml(finalTask.error || "未知错误")}`;
                Components.toast("同步失败", "error");
            }
        } catch (e) {
            resultText.style.display = "block";
            resultText.innerHTML = `❌ 同步失败：${Utils.escapeHtml(e.message)}`;
            Components.toast(`同步失败: ${e.message}`, "error");
        } finally {
            btn.disabled = false;
            btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg> 同步行情数据`;
        }
    });
}

function setupMacroSyncButton() {
    const btn = document.getElementById("btn-sync-macro");
    const wrap = document.getElementById("macro-sync-progress-wrap");
    const fill = document.getElementById("macro-sync-fill");
    const statusText = document.getElementById("macro-sync-status");
    const percentText = document.getElementById("macro-sync-percent");
    const resultText = document.getElementById("macro-sync-result");
    const freqSelect = document.getElementById("macro-sync-freq");

    btn.addEventListener("click", async () => {
        const ok = await Components.runMacroSync({
            frequency: freqSelect.value,
            btn,
            wrap,
            fill,
            statusText,
            percentText,
            resultText,
        });
        Components.toast(ok ? "宏观数据同步完成" : "宏观数据同步失败", ok ? "success" : "error");
    });
}

function renderSyncDetail(r) {
    /* 逐标的明细：状态/来源/写入区间/行数/具体原因，失败高亮，未尝试灰色。 */
    const rows = r.results || [];
    const warnings = r.warnings || [];
    if (!rows.length && !warnings.length) return "";
    const border = "border-bottom:1px solid rgba(128,128,128,0.25);";
    const statusBadge = (s) => {
        if (s === "success") return `<span style="color:var(--success,#16a34a);">成功</span>`;
        if (s === "skipped") return `<span style="color:var(--text-muted,#9ca3af);">未尝试</span>`;
        return `<span style="color:var(--danger,#dc2626);">失败</span>`;
    };
    const esc = (v) => Utils.escapeHtml(v == null ? "" : String(v));

    let html = "";
    if (rows.length > 0) {
        html += `<details style="margin-top:8px;">
            <summary style="cursor:pointer;font-size:13px;">逐标的明细（${rows.length} 只）</summary>
            <div style="max-height:320px;overflow:auto;margin-top:8px;">
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr>
                <th style="text-align:left;padding:4px 6px;${border}">证券</th>
                <th style="text-align:left;padding:4px 6px;${border}">状态</th>
                <th style="text-align:left;padding:4px 6px;${border}">来源</th>
                <th style="text-align:left;padding:4px 6px;${border}">写入区间</th>
                <th style="text-align:right;padding:4px 6px;${border}">行数</th>
                <th style="text-align:left;padding:4px 6px;${border}">原因</th>
            </tr></thead><tbody>`;
        for (const it of rows) {
            const range = it.written_start ? `${esc(it.written_start)} ~ ${esc(it.written_end)}` : "—";
            html += `<tr>
                <td style="padding:4px 6px;${border}">${esc(it.sec_code)}</td>
                <td style="padding:4px 6px;${border}">${statusBadge(it.status)}</td>
                <td style="padding:4px 6px;${border}">${esc(it.source)}</td>
                <td style="padding:4px 6px;${border}">${range}</td>
                <td style="padding:4px 6px;${border}text-align:right;">${it.rows ?? 0}</td>
                <td style="padding:4px 6px;${border}">${esc(it.reason)}</td>
            </tr>`;
        }
        html += `</tbody></table></div></details>`;
    }
    if (warnings.length > 0) {
        const wItems = warnings
            .map((w) => `${esc(w.sec)} ${esc(w.date)} 单日涨跌幅 ${w.chg == null ? "N/A" : esc(w.chg)}%`)
            .join("<br>");
        html += `<details style="margin-top:6px;">
            <summary style="cursor:pointer;font-size:13px;color:var(--warning,#d97706);">断崖告警（${warnings.length} 条）</summary>
            <div style="font-size:12px;margin-top:6px;">${wItems}</div></details>`;
    }
    return html;
}

window.renderSettings = renderSettings;
