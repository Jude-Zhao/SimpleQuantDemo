"use strict";

/* B7 Node 测试：strategies.js 的 renderRunDiagnostics（决策日明细/约束检查）。
 * 运行完成页（renderRunSummary）与历史弹窗（viewRunDetail）必须调用同一渲染器；
 * 版本缺失/未来版本/结构损坏/空结果显示明确文案；完整无排除与有排除分别展示。
 * Node 测试不冒充真实浏览器；布局与展开操作由手工浏览器核验另记录。
 */

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const JS_DIR = path.join(REPO_ROOT, "webapp", "static", "js");

function readJs(rel) {
    return fs.readFileSync(path.join(JS_DIR, rel), "utf8");
}

// ══ 最小 DOM 替身（与既有 4 个测试文件相同的内联实现）══════════════════
const VOID_TAGS = new Set([
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
]);

function decodeEntities(s) {
    return String(s)
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&apos;/g, "'")
        .replace(/&nbsp;/g, " ")
        .replace(/&amp;/g, "&");
}

function parseSimpleSelector(part) {
    const spec = { tag: null, id: null, classes: [], attrs: [], pseudo: null };
    const re = /([a-zA-Z][a-zA-Z0-9-]*)|#([a-zA-Z0-9_-]+)|\.([a-zA-Z0-9_-]+)|\[([^\]=\s]+)(?:=["']?([^\]"']*)["']?)?\]|(:[a-zA-Z-]+)/g;
    let m;
    while ((m = re.exec(part)) !== null) {
        if (m[1] !== undefined) spec.tag = m[1].toLowerCase();
        else if (m[2] !== undefined) spec.id = m[2];
        else if (m[3] !== undefined) spec.classes.push(m[3]);
        else if (m[4] !== undefined) spec.attrs.push({ name: m[4].toLowerCase(), value: m[5] });
        else if (m[6] !== undefined) spec.pseudo = m[6];
    }
    return spec;
}

function matchesSimple(el, spec) {
    if (spec.tag && el.tagName.toLowerCase() !== spec.tag) return false;
    if (spec.id && el.id !== spec.id) return false;
    for (const c of spec.classes) if (!el._classes.has(c)) return false;
    for (const a of spec.attrs) {
        const v = el.attrs[a.name];
        if (a.value === undefined) {
            if (v === undefined) return false;
        } else if (String(v) !== a.value) {
            return false;
        }
    }
    return true;
}

function matchesParts(el, parts) {
    if (!matchesSimple(el, parts[parts.length - 1])) return false;
    let idx = parts.length - 2;
    let anc = el.parentNode;
    while (anc && idx >= 0) {
        if (matchesSimple(anc, parts[idx])) idx -= 1;
        anc = anc.parentNode;
    }
    return idx < 0;
}

function querySelectorAllIn(root, selector) {
    const parts = String(selector).trim().split(/\s+/).filter(Boolean).map(parseSimpleSelector);
    const out = [];
    const walk = (el) => {
        for (const child of el.children) {
            if (matchesParts(child, parts)) out.push(child);
            walk(child);
        }
    };
    walk(root);
    return out;
}

class FakeElement {
    constructor(tag) {
        this.tagName = String(tag || "div").toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.style = {};
        this.dataset = {};
        this.attrs = {};
        this.value = "";
        this.textContent = "";
        this.disabled = false;
        this.id = "";
        this._classes = new Set();
        this._listeners = Object.create(null);
        this._innerHTML = "";
        this._doc = null;
    }
    get className() { return [...this._classes].join(" "); }
    set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
    get classList() {
        const self = this;
        return {
            add: (...cs) => cs.forEach((c) => self._classes.add(c)),
            remove: (...cs) => cs.forEach((c) => self._classes.delete(c)),
            toggle: (c, force) => {
                const want = force === undefined ? !self._classes.has(c) : Boolean(force);
                if (want) self._classes.add(c);
                else self._classes.delete(c);
                return want;
            },
            contains: (c) => self._classes.has(c),
        };
    }
    get innerHTML() { return this._innerHTML; }
    set innerHTML(html) {
        this._innerHTML = String(html);
        this.children = [];
        parseHtml(this._innerHTML, this);
    }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
    dispatchEvent(event) {
        event.target = event.target || this;
        (this._listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event));
    }
    querySelector(selector) { return querySelectorAllIn(this, selector)[0] || null; }
    querySelectorAll(selector) { return querySelectorAllIn(this, selector); }
}

const ATTR_RE = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const TAG_RE = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;

function applyAttrs(el, attrStr, doc) {
    ATTR_RE.lastIndex = 0;
    let am;
    while ((am = ATTR_RE.exec(attrStr)) !== null) {
        const name = am[1].toLowerCase();
        const value = decodeEntities(am[2] !== undefined ? am[2] : am[3]);
        el.attrs[name] = value;
        if (name === "id") {
            el.id = value;
            if (doc) doc._idCache.set(value, el);
        } else if (name === "class") {
            String(value).split(/\s+/).filter(Boolean).forEach((c) => el._classes.add(c));
        } else if (name.startsWith("data-")) {
            el.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
        } else if (name === "value") el.value = value;
        else if (name === "disabled") el.disabled = true;
    }
}

function parseHtml(html, parent) {
    const doc = parent._doc;
    TAG_RE.lastIndex = 0;
    const stack = [parent];
    let m;
    while ((m = TAG_RE.exec(html)) !== null) {
        const closing = m[1] === "/";
        const tag = m[2].toLowerCase();
        const attrStr = m[3] || "";
        if (closing) {
            for (let i = stack.length - 1; i > 0; i--) {
                if (stack[i].tagName.toLowerCase() === tag) {
                    stack.splice(i, stack.length - i);
                    break;
                }
            }
            continue;
        }
        const selfClosed = attrStr.trimEnd().endsWith("/") || VOID_TAGS.has(tag);
        const el = new FakeElement(tag);
        el._doc = doc;
        applyAttrs(el, attrStr, doc);
        stack[stack.length - 1].appendChild(el);
        if (!selfClosed) stack.push(el);
    }
    return parent.children;
}

function createDocument() {
    const doc = new FakeElement("#document");
    doc._doc = doc;
    doc._idCache = new Map();
    doc.body = new FakeElement("body");
    doc.body._doc = doc;
    doc.body.parentNode = doc;
    doc.title = "";
    doc.createElement = (tag) => {
        const el = new FakeElement(tag);
        el._doc = doc;
        return el;
    };
    doc.getElementById = (id) => {
        if (doc._idCache.has(id)) return doc._idCache.get(id);
        const found = querySelectorAllIn(doc.body, "#" + id)[0];
        if (found) {
            doc._idCache.set(id, found);
            return found;
        }
        const el = new FakeElement("div");
        el._doc = doc;
        el.id = id;
        doc._idCache.set(id, el);
        return el;
    };
    doc.querySelector = (selector) => querySelectorAllIn(doc.body, selector)[0] || null;
    doc.querySelectorAll = (selector) => querySelectorAllIn(doc.body, selector);
    return doc;
}

// ══ vm 环境 ═════════════════════════════════════════════════════════
function createContextEnv({ apiOverrides = {}, modalCaptures = [] } = {}) {
    const doc = createDocument();
    const sandbox = {
        console,
        setTimeout: () => 0,
        clearTimeout: () => {},
        document: doc,
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(readJs("utils.js"), sandbox, { filename: "utils.js" });
    sandbox.Components = {
        toast: () => {},
        modal: (opts) => {
            modalCaptures.push(opts);
            const holder = doc.createElement("div");
            holder._doc = doc;
            doc.body.appendChild(holder);
            holder.innerHTML = opts.body || "";
        },
        runStatusBadge: (s) => `<span class="badge">${s}</span>`,
    };
    sandbox.Charts = { render: () => {} };
    sandbox.API = {
        getUniverse: async () => [],
        listRuns: async () => [],
        getRun: apiOverrides.getRun || (async () => { throw new Error("getRun 未配置"); }),
    };
    vm.runInContext(readJs("pages/strategies.js"), sandbox, { filename: "pages/strategies.js" });
    return { sandbox, doc, modalCaptures };
}

async function flush(turns = 10) {
    for (let i = 0; i < turns; i++) await new Promise((resolve) => setImmediate(resolve));
}

// ══ 固定 result_summary 样例 ════════════════════════════════════════
function makeSummaryV2({ withExclusions = true } = {}) {
    const exclusions = withExclusions
        ? [
              { sec: "518880.SH", category: "volume", factor: "mfi", instance_index: 0,
                params: { window: 12 }, reason: "missing" },
              { sec: "518880.SH", category: "volume", factor: "mfi", instance_index: 1,
                params: { window: 20 }, reason: "non_finite" },
          ]
        : [];
    return {
        schema_version: "2",
        framework_version: "1",
        execution_config: { initial_cash: 1.0, transaction_cost_bps: 0.5 },
        metrics_config: { annualization: 252, risk_free_rate: 0.01 },
        run_context: {
            strategy_type: "faa",
            params: { top_n: 2, rebalance_freq: "5d" },
            sec_names: { "510300.SH": "沪深300ETF", "518880.SH": "黄金ETF" },
            factor_categories: [],
            classifications: {},
            constraints: { single_max_weight: 0.15 },
        },
        decision_log: [
            {
                decision_date: "2026-01-05",
                eligible_count: 2,
                top_n: 2,
                status: "target_created",
                exclusions,
            },
            {
                decision_date: "2026-01-07",
                eligible_count: 1,
                top_n: 2,
                status: "skipped_insufficient",
                exclusions: [
                    { sec: "518880.SH", category: "volume", factor: "mfi",
                      instance_index: 0, params: { window: 12 }, reason: "missing" },
                ],
            },
            {
                decision_date: "2026-01-09",
                eligible_count: 2,
                top_n: 2,
                status: "target_created",
                exclusions: [],
            },
        ],
        execution_log: [
            { decision_date: "2026-01-05", execution_date: "2026-01-06",
              status: "executed", reason: "ok", missing_codes: [] },
            { decision_date: "2026-01-09", execution_date: null,
              status: "unexecuted_end", reason: "末日信号", missing_codes: [] },
        ],
        constraint_checks: [
            { scope: "history", decision_date: "2026-01-05", execution_date: "2026-01-06",
              status: "checked", violations: [], unsupported_constraints: [], not_evaluated_constraints: [] },
            { scope: "latest", decision_date: "2026-01-07", execution_date: null,
              status: "checked", violations: [], unsupported_constraints: [],
              not_evaluated_constraints: ["turnover_limit"] },
        ],
        metrics: { total_return: 0.05, annual_return: 0.1, annual_volatility: 0.08, sharpe: 1.2, max_drawdown: -0.03 },
        constraint_violations: [],
        equity_curve: { "2026-01-05": 1.0, "2026-01-06": 1.02, "2026-01-07": 1.05 },
        daily_returns: { "2026-01-05": 0.0, "2026-01-06": 0.02, "2026-01-07": 0.0294 },
        weights: {},
        turnover: {},
        costs: {},
        rebalance_dates: ["2026-01-05", "2026-01-07"],
        latest_weights: { "510300.SH": 0.5, "518880.SH": 0.5 },
        latest_data_date: "2026-01-07",
        latest_recommendation_reason: null,
    };
}

test("运行完成页与历史弹窗调用相同渲染器，明细一致", async () => {
    const modalCaptures = [];
    const { sandbox, doc } = createContextEnv({ modalCaptures });
    const summary = makeSummaryV2({ withExclusions: true });

    // 1) 运行完成页路径：renderRunSummary 内部调用 renderRunDiagnostics
    const resultEl = doc.createElement("div");
    doc.body.appendChild(resultEl);
    await sandbox.renderRunSummary(resultEl, { status: "success", error_msg: null, ...summary });
    const pageHtml = doc.getElementById("run-diagnostics").innerHTML;

    // 2) 历史弹窗路径：viewRunDetail → modal body 内的 run-detail-diagnostics
    sandbox.API.getRun = async () => ({
        id: 7, strategy_type: "faa", status: "success", error_msg: null,
        created_at: "2026-01-08T00:00:00", result_summary: summary,
    });
    await sandbox.viewRunDetail(7);
    assert.equal(modalCaptures.length, 1);
    const modalHtml = doc.getElementById("run-detail-diagnostics").innerHTML;

    assert.ok(pageHtml.includes("决策日明细"), "完成页渲染决策日明细");
    assert.ok(modalHtml.includes("决策日明细"), "弹窗渲染决策日明细");
    assert.equal(pageHtml, modalHtml, "两条路径必须产出相同的明细 HTML");
    // 直接调用渲染器也应得到同样输出（同一函数）
    const directEl = doc.createElement("div");
    sandbox.renderRunDiagnostics(summary, directEl);
    assert.equal(directEl.innerHTML, pageHtml);
});

test("版本缺失显示未记录明细（不是无排除）", () => {
    const { sandbox, doc } = createContextEnv();
    const el = doc.createElement("div");
    sandbox.renderRunDiagnostics({ metrics: {} }, el);
    assert.ok(el.innerHTML.includes("该历史运行未记录资格明细"), el.innerHTML);
    assert.ok(!el.innerHTML.includes("无排除"), el.innerHTML);
});

test("未来版本显示不支持", () => {
    const { sandbox, doc } = createContextEnv();
    const el = doc.createElement("div");
    sandbox.renderRunDiagnostics({ schema_version: "99" }, el);
    assert.ok(el.innerHTML.includes("不支持该结果版本"), el.innerHTML);
    assert.ok(el.innerHTML.includes("99"), el.innerHTML);
});

test("空结果与失败状态显示无结果数据/失败文案", () => {
    const { sandbox, doc } = createContextEnv();
    const el = doc.createElement("div");
    sandbox.renderRunDiagnostics(null, el);
    assert.ok(el.innerHTML.includes("无结果数据"), el.innerHTML);

    const failEl = doc.createElement("div");
    sandbox.renderRunSummary(failEl, { status: "failed", error_msg: "回测失败" });
    assert.ok(failEl.innerHTML.includes("运行失败"), failEl.innerHTML);
    assert.ok(failEl.innerHTML.includes("回测失败"), failEl.innerHTML);
});

test("结构损坏显示明细不可用", () => {
    const { sandbox, doc } = createContextEnv();
    const el = doc.createElement("div");
    sandbox.renderRunDiagnostics(
        { schema_version: "2", run_context: {}, decision_log: "broken" }, el
    );
    assert.ok(el.innerHTML.includes("明细不可用"), el.innerHTML);
});

test("完整无排除与有排除分别展示；同名不同参数实例全部保留", () => {
    const { sandbox, doc } = createContextEnv();

    const cleanEl = doc.createElement("div");
    sandbox.renderRunDiagnostics(makeSummaryV2({ withExclusions: false }), cleanEl);
    assert.ok(cleanEl.innerHTML.includes("无排除"), cleanEl.innerHTML);
    assert.ok(cleanEl.innerHTML.includes("2/2"), "合格数/TopN 展示");
    assert.ok(cleanEl.innerHTML.includes("未评价"), "latest 换手未评价展示");
    assert.ok(cleanEl.innerHTML.includes("turnover_limit"), cleanEl.innerHTML);

    const exclEl = doc.createElement("div");
    sandbox.renderRunDiagnostics(makeSummaryV2({ withExclusions: true }), exclEl);
    const html = exclEl.innerHTML;
    // 名称来自快照 sec_names，不调用 ensureUniverseNameMap/行情 API
    assert.ok(html.includes("黄金ETF"), html);
    assert.ok(html.includes("518880.SH"), html);
    assert.ok(html.includes("missing"), html);
    assert.ok(html.includes("non_finite"), html);
    assert.ok(html.includes("#0"), html);
    assert.ok(html.includes("#1"), html);
    assert.ok(html.includes("mfi"), html);
    // 同名不同参数两实例都出现（mfi 两次 + #0/#1）
    assert.ok(html.includes("资格不足跳过"), html);
});

test("target_created 不等于 executed：末日信号显示未执行", () => {
    const { sandbox, doc } = createContextEnv();
    const el = doc.createElement("div");
    sandbox.renderRunDiagnostics(makeSummaryV2({ withExclusions: false }), el);
    assert.ok(el.innerHTML.includes("已执行"), el.innerHTML);
    assert.ok(el.innerHTML.includes("未执行（末日信号）"), el.innerHTML);
});
