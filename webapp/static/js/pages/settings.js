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
            ${dsCard("备数据源", ds.secondary, "主源失败时自动切换", true)}
            ${dsCard("SQLite 数据库", data.system.database_connected ? "已连接" : "未连接",
                data.system.database_connected ? "数据持久化正常" : "数据库不可用", data.system.database_connected)}`;

        const cacheEnabled = ds.cache_enabled ? "启用" : "禁用";
        document.getElementById("cache-config").innerHTML = `
            ${dsCard("缓存开关", cacheEnabled, "行情数据本地缓存", ds.cache_enabled)}
            ${dsCard("日线缓存", `${ds.cache_days_daily} 天`, "日线数据保留天数", true)}
            ${dsCard("分钟缓存", `${ds.cache_days_minute} 天`, "分钟线数据保留天数", true)}`;

        document.getElementById("setting-version").value = data.system.version;
        document.getElementById("setting-db-url").value = data.system.database_url;
    } catch (e) {
        document.getElementById("datasource-cards").innerHTML =
            `<div class="col-12"><div class="alert alert-error">加载设置失败: ${Utils.escapeHtml(e.message)}</div></div>`;
        document.getElementById("setting-version").value = `加载失败: ${e.message}`;
    }
}

window.renderSettings = renderSettings;
