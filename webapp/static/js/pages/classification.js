/* Classification & constraints page — rule flow visualization + live preview */

let rulesCache = [];
let previewCache = null;
let nameMap = {}; // sec_code -> sec_name

function renderClassification(container) {
    container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">分类约束</h1>
            <div class="btn-group">
                <button class="btn btn-primary btn-sm" id="btn-apply-classification">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>
                    应用分类
                </button>
            </div>
        </div>

        <div class="card">
            <div class="card-title">规则执行流程</div>
            <div id="rule-flow"></div>
        </div>

        <div class="grid-12" style="align-items:start;">
            <div class="col-8">
                <div class="card">
                    <div class="card-title">
                        规则列表
                        <button class="btn btn-sm" id="btn-new-rule">+ 新增规则</button>
                    </div>
                    <div class="table-wrap">
                        <table class="table" id="rules-table">
                            <thead><tr><th>优先级</th><th>规则名称</th><th>分类键</th><th>类型</th><th>状态</th><th>操作</th></tr></thead>
                            <tbody><tr><td colspan="6" class="text-muted">加载中...</td></tr></tbody>
                        </table>
                    </div>
                    <div class="btn-group mt-16">
                        <button class="btn btn-sm" id="btn-refresh-rules">🔄 刷新</button>
                    </div>
                </div>
            </div>

            <div class="col-4">
                <div class="card">
                    <div class="card-title">约束配置</div>
                    <div class="form-group">
                        <label>单票最小权重</label>
                        <input id="constraint-single-min" type="number" step="0.01" min="0" max="1" class="form-control" placeholder="0">
                    </div>
                    <div class="form-group">
                        <label>单票最大权重</label>
                        <input id="constraint-single-max" type="number" step="0.01" min="0" max="1" class="form-control" placeholder="0.15">
                    </div>
                    <div class="form-group">
                        <label>分类约束 (JSON)</label>
                        <textarea id="constraint-categories" class="form-control" rows="4" placeholder='[{"category_key":"category","category_value":"宽基","max_weight":0.5,"min_weight":0.1}]'></textarea>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" id="btn-save-constraints">💾 保存约束</button>
                    </div>
                    <div id="constraint-dirty-bar" style="display:none;margin-top:12px;">
                        <div class="alert alert-warning" style="margin:0;font-size:13px;">有未保存的更改</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">分类结果预览
                <span class="text-muted" id="preview-meta" style="font-size:12px;font-weight:400;"></span>
            </div>
            <div id="classification-preview">
                <div class="empty-state">
                    <div class="empty-icon">🏷️</div>
                    <div class="empty-title">暂无分类结果</div>
                    <div class="empty-desc">点击"应用分类"查看标的分类结果</div>
                </div>
            </div>
        </div>
    `;

    loadRules();
    loadClassificationConstraints();
    // Auto-load the classification preview so the page isn't empty on entry.
    applyClassification(true);

    document.getElementById("btn-refresh-rules").addEventListener("click", loadRules);
    document.getElementById("btn-apply-classification").addEventListener("click", applyClassification);
    document.getElementById("btn-save-constraints").addEventListener("click", saveConstraints);
    document.getElementById("btn-new-rule").addEventListener("click", () => showRuleModal());

    // Dirty state on constraint edits
    ["constraint-single-min", "constraint-single-max", "constraint-categories"].forEach((id) => {
        document.getElementById(id).addEventListener("input", () => {
            document.getElementById("constraint-dirty-bar").style.display = "block";
        });
    });
}

async function loadRules() {
    const tbody = document.querySelector("#rules-table tbody");
    try {
        const rules = await API.listRules(false);
        rulesCache = rules;
        if (!rules.length) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-muted">暂无规则，点击"新增规则"创建</td></tr>`;
        } else {
            const sorted = [...rules].sort((a, b) => (a.priority || 0) - (b.priority || 0));
            tbody.innerHTML = sorted
                .map(
                    (r) => `
                    <tr>
                        <td><span class="badge badge-accent">${r.priority}</span></td>
                        <td>${Utils.escapeHtml(r.rule_name)}</td>
                        <td>${Utils.escapeHtml(r.category_key)}</td>
                        <td><span class="badge badge-muted">${Utils.escapeHtml(r.rule_type)}</span></td>
                        <td><span class="badge ${r.is_active ? "badge-success" : "badge-danger"}">${r.is_active ? "启用" : "停用"}</span></td>
                        <td>
                            <div class="row-actions btn-group">
                                <button class="btn btn-sm" onclick="editRule(${r.id})">编辑</button>
                                <button class="btn btn-sm btn-danger" onclick="deleteRule(${r.id})">删除</button>
                            </div>
                        </td>
                    </tr>`
                )
                .join("");
        }
        renderRuleFlow(rules);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-muted">加载失败: ${Utils.escapeHtml(e.message)}</td></tr>`;
    }
}

function renderRuleFlow(rules) {
    const flowEl = document.getElementById("rule-flow");
    if (!flowEl) return;
    const active = rules.filter((r) => r.is_active).sort((a, b) => (a.priority || 0) - (b.priority || 0));
    const arrow = `<span class="flow-arrow">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
    </span>`;

    if (!active.length) {
        flowEl.innerHTML = `<div class="text-muted" style="font-size:13px;">暂无启用的规则</div>`;
        return;
    }

    flowEl.innerHTML = `
        <div class="rule-flow">
            ${active
                .map(
                    (r, i) => `
                    ${i > 0 ? arrow : ""}
                    <div class="flow-node" title="${Utils.escapeHtml(r.rule_name)}">
                        <span>${i + 1}. ${Utils.escapeHtml(r.rule_name)}</span>
                        <span class="flow-sub">priority ${r.priority}</span>
                    </div>`
                )
                .join("")}
            ${arrow}
            <div class="flow-end">
                <span>输出分类结果</span>
                <span class="flow-sub">${active.length} 条规则</span>
            </div>
        </div>`;
}

function showRuleModal(rule) {
    const r = rule || {};
    const config = r.config || {};
    Components.modal({
        title: r.id ? "编辑规则" : "新增规则",
        body: `
            <div class="form-group">
                <label>规则名称</label>
                <input id="rule-name" class="form-control" value="${Utils.escapeHtml(r.rule_name || "")}" />
            </div>
            <div class="form-group">
                <label>分类键</label>
                <input id="rule-category-key" class="form-control" value="${Utils.escapeHtml(r.category_key || "category")}" />
            </div>
            <div class="form-group">
                <label>规则类型</label>
                <select id="rule-type" class="form-select">
                    <option value="manual" ${r.rule_type === "manual" ? "selected" : ""}>手动指定 (manual)</option>
                    <option value="by_field" ${r.rule_type === "by_field" ? "selected" : ""}>字段匹配 (by_field)</option>
                    <option value="by_range" ${r.rule_type === "by_range" ? "selected" : ""}>数值区间 (by_range)</option>
                </select>
            </div>
            <div class="form-group">
                <label>配置 (JSON)</label>
                <textarea id="rule-config" class="form-control" rows="5">${Utils.escapeHtml(JSON.stringify(config, null, 2))}</textarea>
            </div>
            <div class="form-group">
                <label>优先级 (数字越小越优先)</label>
                <input id="rule-priority" type="number" class="form-control" value="${r.priority ?? 100}" />
            </div>
            <div class="form-group">
                <label style="display:flex;align-items:center;gap:8px;font-weight:500;">
                    <input type="checkbox" id="rule-active" style="accent-color:var(--accent);" ${r.is_active === false ? "" : "checked"} /> 启用
                </label>
            </div>`,
        actions: [
            { label: "取消" },
            {
                label: "保存",
                variant: "primary",
                onClick: async (overlay, close) => {
                    const payload = {
                        rule_name: overlay.querySelector("#rule-name").value,
                        category_key: overlay.querySelector("#rule-category-key").value,
                        rule_type: overlay.querySelector("#rule-type").value,
                        priority: parseInt(overlay.querySelector("#rule-priority").value, 10) || 100,
                        is_active: overlay.querySelector("#rule-active").checked,
                    };
                    try {
                        payload.config = JSON.parse(overlay.querySelector("#rule-config").value || "{}");
                    } catch (e) {
                        Components.toast("配置 JSON 格式错误", "error");
                        return false;
                    }
                    try {
                        if (r.id) {
                            await API.updateRule(r.id, payload);
                        } else {
                            await API.createRule(payload);
                        }
                        close();
                        Components.toast(r.id ? "规则已更新" : "规则已创建", "success");
                        await loadRules();
                        // Live preview: re-apply classification automatically
                        applyClassification(true);
                    } catch (e) {
                        Components.toast(`保存失败: ${e.message}`, "error");
                        return false;
                    }
                },
            },
        ],
    });
}

async function editRule(id) {
    const rule = rulesCache.find((r) => r.id === id);
    if (rule) showRuleModal(rule);
}

async function deleteRule(id) {
    const ok = await Components.confirmDialog(`确定删除规则 #${id} 吗？`, { danger: true, okText: "删除" });
    if (!ok) return;
    try {
        await API.deleteRule(id);
        Components.toast("规则已删除", "success");
        await loadRules();
        applyClassification(true);
    } catch (e) {
        Components.toast(`删除失败: ${e.message}`, "error");
    }
}

async function applyClassification(silent = false) {
    const preview = document.getElementById("classification-preview");
    const metaEl = document.getElementById("preview-meta");
    if (!preview) return;
    if (!silent) {
        preview.innerHTML = `<div class="loading"><div class="spinner"></div>应用中...</div>`;
    }
    try {
        // Build a sec_code -> sec_name map once, for readable member lists.
        try {
            const universe = await API.getUniverse();
            nameMap = {};
            (universe || []).forEach((u) => {
                nameMap[u.sec_code] = u.sec_name || u.sec_code;
            });
        } catch (_) {
            nameMap = {};
        }

        // Explicit "应用分类" click re-runs the engine (POST); silent refresh
        // (page load / after rule edits) only needs the read-only view (GET).
        const result = silent ? await API.getClassifications() : await API.applyClassification();
        previewCache = result;
        renderClassificationPreview(preview, result);
        if (metaEl) metaEl.textContent = `共 ${result.length} 个标的 · ${new Date().toLocaleTimeString()}`;
        if (!silent) Components.toast("分类已应用", "success");
    } catch (e) {
        if (!silent) {
            preview.innerHTML = `<div class="alert alert-error">应用失败: ${Utils.escapeHtml(e.message)}</div>`;
        }
    }
}

function renderClassificationPreview(el, result) {
    if (!result.length) {
        el.innerHTML = `<div class="empty-state"><div class="empty-icon">🏷️</div><div class="empty-title">无分类结果</div></div>`;
        return;
    }

    // Group by (category_key, category_value) across all ETFs.
    const groups = new Map(); // key -> Map(value -> {count, members[]})
    result.forEach((item) => {
        Object.entries(item.categories || {}).forEach(([key, value]) => {
            if (!value) return;
            if (!groups.has(key)) groups.set(key, new Map());
            const byValue = groups.get(key);
            if (!byValue.has(value)) {
                byValue.set(value, { count: 0, members: [] });
            }
            const g = byValue.get(value);
            g.count += 1;
            g.members.push({
                code: item.sec_code,
                name: nameMap[item.sec_code] || item.sec_code,
            });
        });
    });

    const keyOrder = [...groups.keys()];
    const rows = [];
    keyOrder.forEach((key) => {
        groups.get(key).forEach((g, value) => {
            rows.push({ key, value, count: g.count, members: g.members });
        });
    });

    let html = `<div class="table-wrap"><table class="table" id="classification-summary-table">
        <thead><tr><th>分类维度</th><th>分类值</th><th>标的数量</th><th>标的（名称 · 代码）</th></tr></thead><tbody>`;

    rows.forEach((r) => {
        const memberText = r.members
            .map((m) => `${Utils.escapeHtml(m.name)} <span class="mono text-muted">${Utils.escapeHtml(m.code)}</span>`)
            .join("、");
        html += `<tr>
            <td><span class="badge badge-accent">${Utils.escapeHtml(r.key)}</span></td>
            <td><strong>${Utils.escapeHtml(r.value)}</strong></td>
            <td><span class="badge badge-muted">${r.count}</span></td>
            <td style="line-height:1.9;">${memberText}</td>
        </tr>`;
    });

    html += `</tbody></table></div>`;
    el.innerHTML = html;
}

// BUG-13：页面独有函数名（原 loadConstraints 与 strategies.js 顶层同名，
// 会被对方版本覆盖后读错页面元素并吞错）。同时承载 BUG-16 的快照保存：
// 成功加载后保存完整深拷贝快照，保存时从快照复制、只覆盖三个可见项，
// 未加载成功不得保存（防止误存空配置/清掉隐藏字段）。
let constraintsSnapshot = null; // 最近一次成功加载/保存的完整配置（深拷贝）
let constraintsReady = false; // 当前页面渲染的输入是否已成功填充

function deepCopyJson(value) {
    return value === null || value === undefined ? value : JSON.parse(JSON.stringify(value));
}

/**
 * 解析约束数字输入：先检查 input.validity.badInput（浏览器级非法输入，如 "1abc"），
 * 再用 Number() 严格解析（不接受 parseFloat 的 "1abc" → 1 截断），空串 → null，
 * 0 保留为 0，非有限值（Infinity 等）报错。解析失败 throw，由调用方提示并不发请求。
 */
function parseConstraintNumber(el, label) {
    if (el && el.validity && el.validity.badInput) {
        throw new Error(`${label}不是合法数字`);
    }
    const text = String(el?.value ?? "").trim();
    if (text === "") return null;
    const n = Number(text);
    if (!Number.isFinite(n)) {
        throw new Error(`${label}必须是有限数字`);
    }
    return n;
}

/** 校验分类约束数组：必须是数组，权重为有限数字、数量为整数。 */
function validateCategoryConstraints(cats) {
    if (!Array.isArray(cats)) {
        throw new Error("分类约束必须是数组");
    }
    cats.forEach((item, idx) => {
        if (!item || typeof item !== "object" || Array.isArray(item)) {
            throw new Error(`分类约束第 ${idx + 1} 项必须是对象`);
        }
        ["min_weight", "max_weight"].forEach((k) => {
            const v = item[k];
            if (v === undefined || v === null) return;
            if (typeof v !== "number" || !Number.isFinite(v)) {
                throw new Error(`分类约束第 ${idx + 1} 项的 ${k} 必须是有限数字`);
            }
        });
        ["min_count", "max_count"].forEach((k) => {
            const v = item[k];
            if (v === undefined || v === null) return;
            if (typeof v !== "number" || !Number.isInteger(v)) {
                throw new Error(`分类约束第 ${idx + 1} 项的 ${k} 必须是整数`);
            }
        });
    });
}

async function loadClassificationConstraints() {
    // 每次渲染后重新加载；就绪前禁止保存。
    constraintsReady = false;
    try {
        const c = await API.getConstraints();
        constraintsSnapshot = deepCopyJson(c);
        document.getElementById("constraint-single-min").value = c.single_min_weight ?? "";
        document.getElementById("constraint-single-max").value = c.single_max_weight ?? "";
        document.getElementById("constraint-categories").value = JSON.stringify(
            c.category_constraints || [],
            null,
            2
        );
        constraintsReady = true;
    } catch (e) {
        // 失败必须可见，且不得覆盖旧快照（constraintsSnapshot 保持原值）。
        Components.toast(`约束配置加载失败: ${e.message}`, "error");
    }
}

async function saveConstraints() {
    if (!constraintsSnapshot || !constraintsReady) {
        // 配置未成功加载时不得保存（避免把空值/空数组当配置写入）。
        Components.toast("约束配置尚未加载成功，不能保存", "error");
        return;
    }
    // payload 从快照深拷贝出发，只覆盖三个可见项（保留 turnover_limit 等未展示字段）。
    const payload = deepCopyJson(constraintsSnapshot);
    try {
        payload.single_min_weight = parseConstraintNumber(
            document.getElementById("constraint-single-min"),
            "单票最小权重"
        );
        payload.single_max_weight = parseConstraintNumber(
            document.getElementById("constraint-single-max"),
            "单票最大权重"
        );
        let cats;
        try {
            cats = JSON.parse(document.getElementById("constraint-categories").value || "[]");
        } catch (e) {
            throw new Error("分类约束 JSON 格式错误");
        }
        validateCategoryConstraints(cats);
        payload.category_constraints = cats;
    } catch (e) {
        // 非法数字/非法结构：提示且不发请求。
        Components.toast(e.message, "error");
        return;
    }

    try {
        const saved = await API.updateConstraints(payload);
        // PUT 返回完整配置 → 更新快照（保留 turnover_limit=0 等字段）。
        constraintsSnapshot = deepCopyJson(saved);
        constraintsReady = true;
        document.getElementById("constraint-dirty-bar").style.display = "none";
        Components.toast("约束已保存", "success");
    } catch (e) {
        Components.toast(`保存失败: ${e.message}`, "error");
    }
}

window.renderClassification = renderClassification;
window.editRule = editRule;
window.deleteRule = deleteRule;
