/* Universe management page — enhanced table with sorting and batch actions */

let universeCache = [];

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
            <div class="form-row" style="grid-template-columns: 1fr auto;align-items:end;">
                <div class="form-group" style="margin:0;">
                    <label>可添加 ETF（可多选）</label>
                    <select id="available-etfs" class="form-select" multiple style="height:160px;"></select>
                </div>
                <div class="btn-group" style="margin-bottom:8px;">
                    <button class="btn btn-primary" id="btn-add-etfs">+ 添加到标的池</button>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">当前标的池 <span class="badge badge-accent" id="universe-count">0</span></div>
            <div id="universe-table-area"></div>
        </div>
    `;

    loadUniverse();
    loadAvailableEtfs();

    document.getElementById("btn-refresh-universe").addEventListener("click", () => {
        loadUniverse();
        loadAvailableEtfs();
    });
    document.getElementById("btn-add-etfs").addEventListener("click", addSelectedEtfs);
}

async function loadUniverse() {
    const area = document.getElementById("universe-table-area");
    area.innerHTML = Utils.skeleton(3);
    try {
        const items = await API.getUniverse();
        universeCache = items;
        document.getElementById("universe-count").textContent = items.length;
        if (!items.length) {
            area.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📦</div>
                    <div class="empty-title">标的池为空</div>
                    <div class="empty-desc">从上方选择 ETF 添加到标的池，开始因子分析</div>
                    <button class="btn btn-primary btn-sm" onclick="document.getElementById('available-etfs').focus()">去添加标的</button>
                </div>`;
            return;
        }
        renderUniverseTable(items);
    } catch (e) {
        area.innerHTML = `<div class="alert alert-error">加载失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
}

let sortState = { key: "sec_code", dir: 1 };

function sortedUniverse() {
    const items = [...universeCache];
    const { key, dir } = sortState;
    const val = (it) => {
        if (key === "category") return it.meta?.category || "";
        if (key === "added_at") return it.added_at || "";
        return it[key] || "";
    };
    items.sort((a, b) => {
        const va = val(a);
        const vb = val(b);
        return String(va).localeCompare(String(vb), "zh-CN", { numeric: true }) * dir;
    });
    return items;
}

function renderUniverseTable(items) {
    const area = document.getElementById("universe-table-area");
    const sorted = sortedUniverse();
    const th = (key, label) => {
        const arrow = sortState.key === key ? (sortState.dir > 0 ? "▲" : "▼") : "";
        return `<th class="sortable" data-sort="${key}">${label} <span class="sort-arrow">${arrow}</span></th>`;
    };

    area.innerHTML = `
        <div class="table-wrap">
            <table class="table" id="universe-table">
                <thead>
                    <tr>
                        <th style="width:36px;"><input type="checkbox" id="select-all" style="accent-color:var(--accent);" /></th>
                        ${th("sec_code", "代码")}
                        ${th("sec_name", "名称")}
                        ${th("category", "类别")}
                        ${th("is_active", "状态")}
                        ${th("added_at", "加入时间")}
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${sorted.map((u) => `
                        <tr data-code="${Utils.escapeHtml(u.sec_code)}">
                            <td><input type="checkbox" class="row-check" value="${Utils.escapeHtml(u.sec_code)}" style="accent-color:var(--accent);" /></td>
                            <td class="mono">${Utils.escapeHtml(u.sec_code)}</td>
                            <td>${Utils.escapeHtml(u.sec_name || "-")}</td>
                            <td>${u.meta?.category ? `<span class="badge badge-accent">${Utils.escapeHtml(u.meta.category)}</span>` : '<span class="text-muted">-</span>'}</td>
                            <td><span class="badge ${u.is_active ? "badge-success" : "badge-danger"}">${u.is_active ? "在池" : "已移除"}</span></td>
                            <td class="text-muted" style="font-size:12px;">${Utils.formatDate(u.added_at)}</td>
                            <td>
                                <div class="row-actions btn-group">
                                    <button class="btn btn-sm" onclick="editCategory('${Utils.escapeHtml(u.sec_code)}')">改类别</button>
                                    <button class="btn btn-sm btn-danger" onclick="removeEtf('${Utils.escapeHtml(u.sec_code)}')">移除</button>
                                </div>
                            </td>
                        </tr>`).join("")}
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
            <button class="btn btn-sm" id="batch-set-category">修改类别</button>
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
            loadAvailableEtfs();
        } catch (e) {
            Components.toast(`移除失败: ${e.message}`, "error");
        }
    });
    container.querySelector("#batch-set-category").addEventListener("click", () => {
        showCategoryModal(codes);
    });
}

function showCategoryModal(codes) {
    const current = universeCache.find((u) => u.sec_code === codes[0])?.meta?.category || "";
    Components.modal({
        title: `修改类别（${codes.length} 个标的）`,
        body: `
            <div class="form-group">
                <label>类别</label>
                <input id="batch-category-input" class="form-control" value="${Utils.escapeHtml(current)}" placeholder="如：宽基 / 行业 / 商品 / 债券" />
            </div>`,
        actions: [
            { label: "取消" },
            {
                label: "保存",
                variant: "primary",
                onClick: async (overlay, close) => {
                    const cat = overlay.querySelector("#batch-category-input").value.trim();
                    try {
                        const items = universeCache
                            .filter((u) => codes.includes(u.sec_code))
                            .map((u) => ({
                                sec_code: u.sec_code,
                                sec_name: u.sec_name,
                                meta: { ...(u.meta || {}), category: cat },
                            }));
                        await API.addUniverse(items);
                        Components.toast(`已更新 ${items.length} 个标的类别`, "success");
                        close();
                        loadUniverse();
                    } catch (e) {
                        Components.toast(`保存失败: ${e.message}`, "error");
                        return false; // keep modal open
                    }
                },
            },
        ],
    });
}

async function loadAvailableEtfs() {
    const sel = document.getElementById("available-etfs");
    try {
        const etfs = await API.getAvailableEtfs();
        sel.innerHTML = etfs
            .map(
                (e) => `<option value="${e.sec_code}" ${e.in_universe ? "disabled" : ""}>
                    ${e.sec_code} - ${e.sec_name}${e.in_universe ? " (已添加)" : ""}
                </option>`
            )
            .join("");
        if (!etfs.length) {
            sel.innerHTML = `<option value="">暂无可用 ETF</option>`;
        }
    } catch (e) {
        sel.innerHTML = `<option>加载失败</option>`;
    }
}

async function addSelectedEtfs() {
    const sel = document.getElementById("available-etfs");
    const selected = Array.from(sel.selectedOptions)
        .filter((o) => !o.disabled)
        .map((o) => o.value);
    if (!selected.length) {
        Components.toast("请选择要添加的 ETF", "warning");
        return;
    }

    try {
        const allEtfs = await API.getAvailableEtfs();
        const items = allEtfs
            .filter((e) => selected.includes(e.sec_code))
            .map((e) => ({ sec_code: e.sec_code, sec_name: e.sec_name, meta: { category: e.category || "" } }));

        await API.addUniverse(items);
        Components.toast(`已添加 ${items.length} 个标的`, "success");
        loadUniverse();
        loadAvailableEtfs();
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
        loadAvailableEtfs();
    } catch (e) {
        Components.toast(`移除失败: ${e.message}`, "error");
    }
}

function editCategory(secCode) {
    showCategoryModal([secCode]);
}

window.renderUniverse = renderUniverse;
window.removeEtf = removeEtf;
window.editCategory = editCategory;
