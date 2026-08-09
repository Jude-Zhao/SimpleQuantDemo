/* Universe management page — enhanced table with sorting and batch actions */

let universeCache = [];
let classificationCache = {}; // sec_code -> {category_key: category_value}

function renderUniverse(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">标的池管理</h1>
            <div class="btn-group">
                <button class="btn btn-sm" id="btn-refresh-universe">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    刷新
                </button>
            </div>
        </div>

        <div id="batch-bar-container"></div>

        <div class="card">
            <div class="card-title">添加标的</div>
            <div class="form-group" style="margin:0;">
                <label>ETF 代码（每行一个；可带名称，自动识别 .SH/.SZ 后缀）</label>
                <textarea id="add-etf-input" class="form-control" rows="4" placeholder="510300.SH 或 510300 沪深300ETF&#10;159915.SZ 或 159915 创业板ETF"></textarea>
            </div>
            <div class="form-group" style="margin-top:12px;">
                <label>三维分类（可选；留"不指定"则需之后到「分类约束」页配置）</label>
                <div class="grid-3" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary, #6e6e6e);">资产类别</label>
                        <select id="add-asset-type" class="form-select"></select>
                        <input id="add-asset-type-custom" class="form-control" style="display:none;margin-top:6px;" placeholder="自定义取值" />
                    </div>
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary, #6e6e6e);">风格</label>
                        <select id="add-style" class="form-select"></select>
                        <input id="add-style-custom" class="form-control" style="display:none;margin-top:6px;" placeholder="自定义取值" />
                    </div>
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary, #6e6e6e);">行业</label>
                        <select id="add-sector" class="form-select"></select>
                        <input id="add-sector-custom" class="form-control" style="display:none;margin-top:6px;" placeholder="自定义取值" />
                    </div>
                </div>
            </div>
            <div class="btn-group" style="margin-top:8px;">
                <button class="btn btn-primary" id="btn-add-etfs">+ 添加到标的池</button>
            </div>
        </div>

        <div class="card">
            <div class="card-title">标的分类概览</div>
            <div id="classification-card">
                <div class="text-muted" style="font-size:13px;">加载中...</div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">当前标的池 <span class="badge badge-accent" id="universe-count">0</span></div>
            <div class="alert" style="margin:0 0 12px;font-size:13px;">
                类别由「分类约束」页的规则决定，如需调整分类请到分类约束页面修改规则。
            </div>
            <div id="universe-table-area"></div>
        </div>
    `;

    loadUniverse();
    loadDimensionOptions();

    document.getElementById("btn-refresh-universe").addEventListener("click", () => {
        loadUniverse();
    });
    document.getElementById("btn-add-etfs").addEventListener("click", addManualEtfs);
}

async function loadUniverse() {
    const area = document.getElementById("universe-table-area");
    area.innerHTML = Utils.skeleton(3);
    try {
        const [items, classification] = await Promise.all([
            API.getUniverse(),
            API.getClassifications().catch(() => []),
        ]);
        universeCache = items;
        classificationCache = {};
        (classification || []).forEach((r) => {
            classificationCache[r.sec_code] = r.categories || {};
        });
        document.getElementById("universe-count").textContent = items.length;
        renderClassificationCards();
        if (!items.length) {
            area.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📦</div>
                    <div class="empty-title">标的池为空</div>
                    <div class="empty-desc">请在上方输入 ETF 代码添加到标的池，开始因子分析</div>
                    <button class="btn btn-primary btn-sm" onclick="document.getElementById('add-etf-input').focus()">去添加标的</button>
                </div>`;
            return;
        }
        renderUniverseTable(items);
    } catch (e) {
        area.innerHTML = `<div class="alert alert-error">加载失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

let sortState = { key: "category", dir: 1 };

// Group universe by asset_type; within each group sort by the current sort key
// (falling back to sec_code when sorting by category itself).
function groupedUniverse() {
    const groups = new Map();
    universeCache.forEach((u) => {
        const cat = (classificationCache[u.sec_code] || {}).asset_type || "未分类";
        if (!groups.has(cat)) groups.set(cat, []);
        groups.get(cat).push(u);
    });
    const { key, dir } = sortState;
    const innerKey = key === "category" ? "sec_code" : key;
    const val = (it) => {
        if (innerKey === "added_at") return it.added_at || "";
        return it[innerKey] || "";
    };
    const cmp = (a, b) => String(val(a)).localeCompare(String(val(b)), "zh-CN", { numeric: true }) * dir;
    groups.forEach((arr) => arr.sort(cmp));
    const names = [...groups.keys()].sort((a, b) => {
        if (a === "未分类") return 1;
        if (b === "未分类") return -1;
        return a.localeCompare(b, "zh-CN") * dir;
    });
    // 未分类 pinned last regardless of sort direction
    return names.map((name) => ({ name, items: groups.get(name) }));
}

const DIMENSIONS = [
    ["asset_type", "资产类别"],
    ["style", "风格"],
    ["sector", "行业"],
];

async function loadDimensionOptions() {
    let valuesByKey = {};
    try {
        const rules = await API.listRules(false);
        (rules || []).forEach((r) => {
            if (!r.is_active) return;
            const v = r.config && r.config.category_value;
            if (v) {
                (valuesByKey[r.category_key] = valuesByKey[r.category_key] || new Set()).add(v);
            }
        });
    } catch (e) {
        // leave dropdowns empty; add still works without classification
    }
    DIMENSIONS.forEach(([key, id]) => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const values = [...(valuesByKey[key] || [])].sort((a, b) => a.localeCompare(b, "zh-CN"));
        sel.innerHTML =
            `<option value="">不指定</option>` +
            values.map((v) => `<option value="${Utils.escapeHtml(v)}">${Utils.escapeHtml(v)}</option>`).join("") +
            `<option value="__custom__">自定义…</option>`;

        const custom = document.getElementById(`${id}-custom`);
        const sync = () => {
            if (custom) custom.style.display = sel.value === "__custom__" ? "block" : "none";
        };
        sel.addEventListener("change", sync);
        sync();
    });
}

function readDimension(selectId, customId) {
    const sel = document.getElementById(selectId);
    const custom = document.getElementById(customId);
    if (sel && sel.value === "__custom__") return (custom?.value || "").trim();
    return sel ? sel.value : "";
}

function renderClassificationCards() {
    const card = document.getElementById("classification-card");
    if (!card) return;
    if (!universeCache.length) {
        card.innerHTML = `<div class="text-muted" style="font-size:13px;">标的池为空</div>`;
        return;
    }
    card.innerHTML = DIMENSIONS.map(([key, label]) => {
        const groups = new Map();
        let unclassified = 0;
        universeCache.forEach((u) => {
            const v = (classificationCache[u.sec_code] || {})[key];
            if (v) groups.set(v, (groups.get(v) || 0) + 1);
            else unclassified += 1;
        });
        const chips = [...groups.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([v, c]) => `<span class="badge badge-accent">${Utils.escapeHtml(v)} <b>${c}</b></span>`)
            .join(" ");
        const unc = unclassified
            ? `<span class="badge badge-danger">未分类 ${unclassified}</span>`
            : "";
        return `<div style="margin-bottom:12px;">
            <div style="font-size:12px;color:var(--text-secondary,#6e6e6e);margin-bottom:6px;">${label}</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;">${chips || '<span class="text-muted" style="font-size:13px;">-</span>'} ${unc}</div>
        </div>`;
    }).join("");
}

function renderUniverseTable(items) {
    const area = document.getElementById("universe-table-area");
    const groups = groupedUniverse();
    const th = (key, label) => {
        const arrow = sortState.key === key ? (sortState.dir > 0 ? "▲" : "▼") : "";
        return `<th class="sortable" data-sort="${key}">${label} <span class="sort-arrow">${arrow}</span></th>`;
    };

    const rows = groups.map((g) => `
        <tr class="group-row">
            <td colspan="9" style="background:var(--accent-subtle);font-weight:600;padding:6px 12px;">
                ${Utils.escapeHtml(g.name)}
                <span class="badge badge-accent">${g.items.length}</span>
            </td>
        </tr>
        ${g.items.map((u) => {
            const cats = classificationCache[u.sec_code] || {};
            const badge = (v) => v ? `<span class="badge badge-accent">${Utils.escapeHtml(v)}</span>` : '<span class="text-muted">-</span>';
            return `
            <tr data-code="${Utils.escapeHtml(u.sec_code)}">
                <td><input type="checkbox" class="row-check" value="${Utils.escapeHtml(u.sec_code)}" style="accent-color:var(--accent);" /></td>
                <td class="mono">${Utils.escapeHtml(u.sec_code)}</td>
                <td>${Utils.escapeHtml(u.sec_name || "-")}</td>
                <td>${badge(cats.asset_type)}</td>
                <td>${badge(cats.style)}</td>
                <td>${badge(cats.sector)}</td>
                <td><span class="badge ${u.is_active ? "badge-success" : "badge-danger"}">${u.is_active ? "在池" : "已移除"}</span></td>
                <td class="text-muted" style="font-size:12px;">${Utils.formatDate(u.added_at)}</td>
                <td>
                    <div class="row-actions btn-group">
                        <button class="btn btn-sm btn-danger" onclick="removeEtf('${Utils.escapeHtml(u.sec_code)}')">移除</button>
                    </div>
                </td>
            </tr>`;}).join("")}
    `).join("");

    area.innerHTML = `
        <div class="table-wrap">
            <table class="table" id="universe-table">
                <thead>
                    <tr>
                        <th style="width:36px;"><input type="checkbox" id="select-all" style="accent-color:var(--accent);" /></th>
                        ${th("sec_code", "代码")}
                        ${th("sec_name", "名称")}
                        ${th("category", "资产类别")}
                        ${th("style", "风格")}
                        ${th("sector", "行业")}
                        ${th("is_active", "状态")}
                        ${th("added_at", "加入时间")}
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>`;

    // Sorting
    area.querySelectorAll("th.sortable").forEach((thEl) => {
        thEl.addEventListener("click", () => {
            const key = thEl.dataset.sort;
            if (sortState.key === key) {
                sortState.dir *= -1;
            } else {
                sortState.key = key;
                sortState.dir = 1;
            }
            renderUniverseTable(universeCache);
        });
    });

    // Row selection
    const selectAll = area.querySelector("#select-all");
    selectAll.addEventListener("change", () => {
        area.querySelectorAll(".row-check").forEach((cb) => {
            cb.checked = selectAll.checked;
            cb.closest("tr").classList.toggle("selected", cb.checked);
        });
        updateBatchBar();
    });
    area.querySelectorAll(".row-check").forEach((cb) => {
        cb.addEventListener("change", () => {
            cb.closest("tr").classList.toggle("selected", cb.checked);
            selectAll.checked = area.querySelectorAll(".row-check:checked").length === area.querySelectorAll(".row-check").length;
            updateBatchBar();
        });
    });

    updateBatchBar();
}

function selectedCodes() {
    return Array.from(document.querySelectorAll(".row-check:checked")).map((c) => c.value);
}

function updateBatchBar() {
    const container = document.getElementById("batch-bar-container");
    const codes = selectedCodes();
    if (!codes.length) {
        container.innerHTML = "";
        return;
    }
    container.innerHTML = `
        <div class="batch-bar">
            <span class="batch-count">已选 ${codes.length} 个标的</span>
            <span class="text-muted" style="font-size:12px;flex:1;">支持批量操作</span>
            <button class="btn btn-sm btn-danger" id="batch-remove">批量移除</button>
            <button class="btn btn-sm" id="batch-clear">取消</button>
        </div>`;

    container.querySelector("#batch-clear").addEventListener("click", () => {
        document.querySelectorAll(".row-check:checked").forEach((cb) => {
            cb.checked = false;
            cb.closest("tr").classList.remove("selected");
        });
        const selectAll = document.getElementById("select-all");
        if (selectAll) selectAll.checked = false;
        updateBatchBar();
    });
    container.querySelector("#batch-remove").addEventListener("click", async () => {
        const ok = await Components.confirmDialog(`确定从标的池移除选中的 ${codes.length} 个标的吗？`, { danger: true, okText: "移除" });
        if (!ok) return;
        try {
            await Promise.all(codes.map((c) => API.removeUniverse(c)));
            Components.toast(`已移除 ${codes.length} 个标的`, "success");
            loadUniverse();
        } catch (e) {
            Components.toast(`移除失败: ${e.message}`, "error");
        }
    });
}

function normalizeSecCode(raw) {
    const code = String(raw || "").trim().toUpperCase();
    if (!code) return "";
    if (code.includes(".")) return code;
    if (/^5\d{5}$/.test(code)) return code + ".SH";
    if (/^1\d{5}$/.test(code)) return code + ".SZ";
    if (/^0\d{5}$/.test(code)) return code + ".SZ";
    return code;
}

async function addManualEtfs() {
    const input = document.getElementById("add-etf-input");
    const lines = (input.value || "")
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);

    if (!lines.length) {
        Components.toast("请输入至少一个 ETF 代码", "warning");
        return;
    }

    const classification = {};
    DIMENSIONS.forEach(([key, id]) => {
        const v = readDimension(id, `${id}-custom`);
        if (v) classification[key] = v;
    });

    const items = [];
    for (const line of lines) {
        const parts = line.split(/\s+/);
        const sec_code = normalizeSecCode(parts[0]);
        if (!sec_code) continue;
        const sec_name = parts.length > 1 ? parts.slice(1).join(" ") : sec_code;
        items.push({ sec_code, sec_name, meta: {}, classification });
    }

    if (!items.length) {
        Components.toast("未解析到有效的 ETF 代码", "warning");
        return;
    }

    try {
        await API.addUniverse(items);
        Components.toast(`已添加 ${items.length} 个标的`, "success");
        input.value = "";
        loadUniverse();
    } catch (e) {
        Components.toast(`添加失败: ${e.message}`, "error");
    }
}

async function removeEtf(secCode) {
    const ok = await Components.confirmDialog(`确定从标的池移除 ${secCode} 吗？`, { danger: true, okText: "移除" });
    if (!ok) return;
    try {
        await API.removeUniverse(secCode);
        Components.toast(`已移除 ${secCode}`, "success");
        loadUniverse();
    } catch (e) {
        Components.toast(`移除失败: ${e.message}`, "error");
    }
}

window.renderUniverse = renderUniverse;
window.removeEtf = removeEtf;
