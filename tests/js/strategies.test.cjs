"use strict";

/* BUG-13 Node 测试：strategies.js / classification.js 顶层同名函数冲突修复。
 * 在同一 vm 上下文加载两个页面脚本（对应 app.js scriptCache 的行为：脚本
 * 只加载一次、函数长期共存），按三种访问顺序渲染页面，断言互不覆盖、
 * 各自读写各自缓存，且加载失败可见。
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

// ══ 最小 DOM 替身（与 classification.test.cjs 相同的最小实现）══════════
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
    if (spec.pseudo === ":checked" && !el.checked) return false;
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
        this.checked = false;
        this.selected = false;
        this.id = "";
        this._classes = new Set();
        this._listeners = Object.create(null);
        this._innerHTML = "";
        this._doc = null;
        this.validity = { badInput: false };
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
    remove() {
        if (this.parentNode) {
            const i = this.parentNode.children.indexOf(this);
            if (i >= 0) this.parentNode.children.splice(i, 1);
            this.parentNode = null;
        }
    }
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
    removeEventListener(type, fn) {
        const list = this._listeners[type];
        if (!list) return;
        const i = list.indexOf(fn);
        if (i >= 0) list.splice(i, 1);
    }
    dispatchEvent(event) {
        event.target = event.target || this;
        (this._listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event));
    }
    click() { this.dispatchEvent({ type: "click", target: this }); }
    closest(selector) {
        const parts = String(selector).trim().split(/\s+/).filter(Boolean).map(parseSimpleSelector);
        let el = this;
        while (el) {
            if (matchesParts(el, parts)) return el;
            el = el.parentNode;
        }
        return null;
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
        else if (name === "checked") el.checked = true;
        else if (name === "disabled") el.disabled = true;
        else if (name === "selected") el.selected = true;
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

// ══ vm 环境组装 ═════════════════════════════════════════════════════
function runIn(sandbox, code, filename) {
    vm.runInContext(code, sandbox, { filename });
}

function makeApiStub(overrides = {}) {
    const calls = { getConstraints: 0 };
    const api = {
        listStrategies: async () => [],
        listRuns: async () => [],
        getUniverse: async () => [],
        getClassifications: async () => [],
        listRules: async () => [],
        getConstraints: async () => { throw new Error("getConstraints 未在测试中配置"); },
        updateConstraints: async (payload) => payload,
        addUniverse: async (items) => items,
        removeUniverse: async (code) => ({ success: true }),
        ...overrides,
    };
    return { api, calls };
}

function createContextEnv({ api } = {}) {
    const doc = createDocument();
    const state = { toasts: [] };
    const sandbox = {
        console,
        setTimeout: () => 0,
        clearTimeout: () => {},
        Event: class Event { constructor(type) { this.type = type; } },
        document: doc,
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    runIn(sandbox, readJs("utils.js"), "utils.js");
    sandbox.Components = {
        toast: (message, type) => { state.toasts.push({ message, type: type || "info" }); },
        modal: () => { throw new Error("modal 不应在本测试中调用"); },
        confirmDialog: async () => true,
        runStatusBadge: (s) => `<span class="badge">${s}</span>`,
    };
    sandbox.API = api;
    return { sandbox, doc, state };
}

async function flush(turns = 10) {
    for (let i = 0; i < turns; i++) await new Promise((resolve) => setImmediate(resolve));
}

function loadBothPages(sandbox) {
    // 同一上下文先后加载两个页面脚本（等价 app.js scriptCache：脚本仅执行一次）。
    runIn(sandbox, readJs("pages/strategies.js"), "pages/strategies.js");
    runIn(sandbox, readJs("pages/classification.js"), "pages/classification.js");
}

// 每次调用返回不同配置：S/C/S2/C2 各带可区分的 single_max_weight。
const S = { single_min_weight: 0.01, single_max_weight: 0.15, category_constraints: [] };
const C = { single_min_weight: 0.02, single_max_weight: 0.25, category_constraints: [] };
const S2 = { single_min_weight: 0.03, single_max_weight: 0.35, category_constraints: [] };
const C2 = { single_min_weight: 0.04, single_max_weight: 0.45, category_constraints: [] };

function queueApi(responses) {
    let n = 0;
    return makeApiStub({
        getConstraints: async () => {
            const v = responses[n];
            n += 1;
            if (v === undefined) throw new Error("多余的 getConstraints 调用");
            return v;
        },
    }).api;
}

test("BUG-13：两页面不再定义全局同名 loadConstraints", async () => {
    const env = createContextEnv({ api: queueApi([]) });
    loadBothPages(env.sandbox);
    assert.equal(env.sandbox.loadConstraints, undefined, "全局不得存在同名冲突函数");
    assert.equal(typeof env.sandbox.loadStrategyConstraints, "function");
    assert.equal(typeof env.sandbox.loadClassificationConstraints, "function");
});

test("BUG-13：先策略后分类再返回策略——互不覆盖，各自读写各自缓存", async () => {
    const env = createContextEnv({ api: queueApi([S, C, S2]) });
    loadBothPages(env.sandbox);

    // 1) 策略页：约束面板显示策略数据（0.15 → 15%）
    const containerA = env.doc.createElement("div");
    env.doc.body.appendChild(containerA);
    env.sandbox.renderStrategies(containerA);
    await flush();
    let panel = env.doc.getElementById("constraint-panel");
    assert.ok(panel.innerHTML.includes("15%"), panel.innerHTML);
    assert.ok(!panel.innerHTML.includes("25%"), panel.innerHTML);

    // 2) 分类页：输入被分类数据填充（0.25）
    const containerB = env.doc.createElement("div");
    env.doc.body.appendChild(containerB);
    env.sandbox.renderClassification(containerB);
    await flush();
    const maxEl = env.doc.getElementById("constraint-single-max");
    assert.equal(String(maxEl.value), "0.25");

    // 3) 返回策略页：面板重新显示策略数据（0.35 → 35%），而非被分类缓存覆盖
    env.sandbox.renderStrategies(containerA);
    await flush();
    panel = env.doc.getElementById("constraint-panel");
    assert.ok(panel.innerHTML.includes("35%"), panel.innerHTML);
    assert.ok(!panel.innerHTML.includes("15%"), panel.innerHTML);
});

test("BUG-13：先分类后策略再返回分类——互不覆盖", async () => {
    const env = createContextEnv({ api: queueApi([C, S, C2]) });
    loadBothPages(env.sandbox);

    const containerB = env.doc.createElement("div");
    env.doc.body.appendChild(containerB);
    env.sandbox.renderClassification(containerB);
    await flush();
    assert.equal(String(env.doc.getElementById("constraint-single-max").value), "0.25");

    const containerA = env.doc.createElement("div");
    env.doc.body.appendChild(containerA);
    env.sandbox.renderStrategies(containerA);
    await flush();
    const panel = env.doc.getElementById("constraint-panel");
    assert.ok(panel.innerHTML.includes("15%"), panel.innerHTML);

    env.sandbox.renderClassification(containerB);
    await flush();
    assert.equal(String(env.doc.getElementById("constraint-single-max").value), "0.45");
    const minEl = env.doc.getElementById("constraint-single-min");
    assert.equal(String(minEl.value), "0.04");
});

test("BUG-13：往返重复访问仍各自加载", async () => {
    const env = createContextEnv({ api: queueApi([S, C, S2, C2]) });
    loadBothPages(env.sandbox);

    const containerA = env.doc.createElement("div");
    const containerB = env.doc.createElement("div");
    env.doc.body.appendChild(containerA);
    env.doc.body.appendChild(containerB);

    env.sandbox.renderStrategies(containerA);
    await flush();
    env.sandbox.renderClassification(containerB);
    await flush();
    env.sandbox.renderStrategies(containerA);
    await flush();
    env.sandbox.renderClassification(containerB);
    await flush();

    // 第 4 次加载（C2）写入分类输入；第 3 次加载（S2）写入策略面板。
    assert.equal(String(env.doc.getElementById("constraint-single-max").value), "0.45");
    const panel = env.doc.getElementById("constraint-panel");
    assert.ok(panel.innerHTML.includes("35%"), panel.innerHTML);
});

test("BUG-13：策略页约束加载失败可见且可恢复", async () => {
    let fail = true;
    const { api } = makeApiStub({
        getConstraints: async () => {
            if (fail) throw new Error("网络中断");
            return S;
        },
    });
    const env = createContextEnv({ api });
    loadBothPages(env.sandbox);

    const containerA = env.doc.createElement("div");
    env.doc.body.appendChild(containerA);
    env.sandbox.renderStrategies(containerA);
    await flush();

    const panel = env.doc.getElementById("constraint-panel");
    assert.ok(panel.innerHTML.includes("约束配置加载失败"), panel.innerHTML);
    assert.ok(panel.innerHTML.includes("网络中断"), panel.innerHTML);

    // 恢复：再次加载成功后面板显示数据
    fail = false;
    await env.sandbox.loadStrategyConstraints();
    await flush();
    assert.ok(panel.innerHTML.includes("15%"), panel.innerHTML);
    assert.ok(!panel.innerHTML.includes("加载失败"), panel.innerHTML);
});

test("BUG-13：分类页约束加载失败可见（toast）", async () => {
    const { api } = makeApiStub({
        getConstraints: async () => { throw new Error("服务不可用"); },
    });
    const env = createContextEnv({ api });
    loadBothPages(env.sandbox);

    const containerB = env.doc.createElement("div");
    env.doc.body.appendChild(containerB);
    env.sandbox.renderClassification(containerB);
    await flush();

    const lastToast = () => env.state.toasts[env.state.toasts.length - 1]?.message || "";
    assert.ok(lastToast().includes("约束配置加载失败"), lastToast());
    assert.ok(lastToast().includes("服务不可用"), lastToast());
});
