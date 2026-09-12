"use strict";

/* BUG-14 / BUG-17 Node 测试：universe.js 页面脚本 + api.js 的 removeUniverse。
 * BUG-14：DIMENSIONS 分类键/显示名/控件 id 分离后，三维选项加载与提交逐字段正确。
 * BUG-17：移除按钮不再拼接内联 onclick，addEventListener 闭包收到原始 sec_code；
 *         api.js removeUniverse 对路径段编码。
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

// ══ 最小 DOM 替身（与其他 *.test.cjs 相同的最小实现）═══════════════════
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
    const calls = { addUniverse: [], removeUniverse: [] };
    const api = {
        listStrategies: async () => [],
        listRuns: async () => [],
        getUniverse: async () => [],
        getClassifications: async () => [],
        listRules: async () => [],
        getConstraints: async () => ({}),
        updateConstraints: async (payload) => payload,
        addUniverse: async (items) => { calls.addUniverse.push(items); return items; },
        removeUniverse: async (code) => { calls.removeUniverse.push(code); return { success: true }; },
        ...overrides,
    };
    return { api, calls };
}

function createContextEnv({ api } = {}) {
    const doc = createDocument();
    const state = { toasts: [], confirmMessages: [] };
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
        confirmDialog: async (message) => {
            state.confirmMessages.push(message);
            return state.confirmResult === undefined ? true : state.confirmResult;
        },
        runStatusBadge: (s) => `<span class="badge">${s}</span>`,
    };
    sandbox.API = api;
    return { sandbox, doc, state };
}

async function flush(turns = 10) {
    for (let i = 0; i < turns; i++) await new Promise((resolve) => setImmediate(resolve));
}

// ══ BUG-14 测试 ═════════════════════════════════════════════════════
const RULES = [
    { id: 1, is_active: true, category_key: "asset_type", config: { category_value: "宽基" } },
    { id: 2, is_active: true, category_key: "asset_type", config: { category_value: "跨境" } },
    { id: 3, is_active: true, category_key: "sector", config: { category_value: "金融" } },
    { id: 4, is_active: false, category_key: "asset_type", config: { category_value: "停用值" } },
];

test("BUG-14：三维下拉按真实控件 id 加载选项", async () => {
    const { api } = makeApiStub({ listRules: async () => RULES });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/universe.js"), "pages/universe.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderUniverse(container);
    await flush();

    const assetSel = env.doc.getElementById("add-asset-type");
    assert.ok(assetSel, "select 必须按真实 id 找到（不再是中文标签）");
    assert.ok(assetSel.innerHTML.includes('<option value="">不指定</option>'), assetSel.innerHTML);
    assert.ok(assetSel.innerHTML.includes('value="宽基"'), assetSel.innerHTML);
    assert.ok(assetSel.innerHTML.includes('value="跨境"'), assetSel.innerHTML);
    assert.ok(!assetSel.innerHTML.includes("停用值"), "停用规则不出现在选项中");
    assert.ok(assetSel.innerHTML.includes('value="__custom__"'), assetSel.innerHTML);

    const styleSel = env.doc.getElementById("add-style");
    assert.ok(styleSel.innerHTML.includes('value="__custom__"'));
    assert.ok(!styleSel.innerHTML.includes("宽基"), "无 style 规则时不得串入其他维度取值");

    const sectorSel = env.doc.getElementById("add-sector");
    assert.ok(sectorSel.innerHTML.includes('value="金融"'), sectorSel.innerHTML);

    // 自定义输入框按 customId 找到并随选择切换显示
    assert.ok(env.doc.getElementById("add-asset-type-custom"));
    assert.ok(env.doc.getElementById("add-style-custom"));
    assert.ok(env.doc.getElementById("add-sector-custom"));
});

test("BUG-14：请求 classification 逐字段等于选择（已有 + 自定义 trim + 未指定缺省）", async () => {
    const { api, calls } = makeApiStub({ listRules: async () => RULES });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/universe.js"), "pages/universe.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderUniverse(container);
    await flush();

    env.doc.getElementById("add-asset-type").value = "宽基"; // 已有取值
    env.doc.getElementById("add-style").value = "__custom__";
    env.doc.getElementById("add-style-custom").value = "  动量  "; // 空白自定义，trim 后传入
    // sector 保持 ""（未指定）
    env.doc.getElementById("add-etf-input").value = "510300 沪深300ETF\n159915.SZ";
    env.doc.getElementById("btn-add-etfs").click();
    await flush();

    assert.equal(calls.addUniverse.length, 1);
    const items = calls.addUniverse[0];
    assert.equal(items.length, 2);
    assert.equal(items[0].sec_code, "510300.SH");
    assert.equal(items[0].sec_name, "沪深300ETF");
    // classification 对象诞生于 vm context 内（跨 realm 原型不同），拷到宿主侧再比较
    assert.deepEqual({ ...items[0].classification }, { asset_type: "宽基", style: "动量" },
        "已有取值原样、自定义 trim、未指定的 sector 不生成字段");
    assert.deepEqual({ ...items[1].classification }, { asset_type: "宽基", style: "动量" });
});

test("BUG-14：全部未指定时不生成虚假分类值", async () => {
    const { api, calls } = makeApiStub({ listRules: async () => RULES });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/universe.js"), "pages/universe.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderUniverse(container);
    await flush();

    env.doc.getElementById("add-etf-input").value = "510300";
    env.doc.getElementById("btn-add-etfs").click();
    await flush();

    assert.equal(calls.addUniverse.length, 1);
    assert.deepEqual({ ...calls.addUniverse[0][0].classification }, {});
});

// ══ BUG-17 测试 ═════════════════════════════════════════════════════
const NASTY = "510300.SH'+alert(1)+'";

test("BUG-17：渲染无动态内联事件，回调收到原始 sec_code（引号 payload 不额外执行）", async () => {
    const { api, calls } = makeApiStub({
        getUniverse: async () => [
            { sec_code: "510300.SH", sec_name: "沪深300ETF", is_active: true, added_at: "2026-01-05T10:00:00" },
            { sec_code: NASTY, sec_name: "x", is_active: true, added_at: "" },
        ],
    });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/universe.js"), "pages/universe.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderUniverse(container);
    await flush();

    const area = env.doc.getElementById("universe-table-area");
    assert.ok(!area.innerHTML.includes("onclick"), "HTML 中不得再拼接 onclick 事件");
    assert.ok(area.innerHTML.includes('data-remove-code="510300.SH"'), area.innerHTML);

    // 含引号的 payload 只以 HTML 转义文本出现（不拼进可执行 JS）
    assert.ok(!area.innerHTML.includes(NASTY), "原始带引号字符串不得原样出现在 HTML 中");
    assert.ok(area.innerHTML.includes("&#39;"), "引号必须被转义为文本实体");

    // 绑定存在且闭包拿到的是原始字符串（dataset 解码回原文）
    const btns = area.querySelectorAll("button[data-remove-code]");
    const nastyBtn = btns.find((b) => b.dataset.removeCode === NASTY);
    assert.ok(nastyBtn, "data-remove-code 必须能解码回原始 sec_code");
    nastyBtn.click();
    await flush();

    assert.ok(env.state.confirmMessages[0].includes(NASTY), "确认文案使用原始字符串");
    assert.deepEqual(calls.removeUniverse, [NASTY], "removeUniverse 收到原始 sec_code");
});

test("BUG-17：重渲染后绑定不重复（一次点击一次移除）", async () => {
    const { api, calls } = makeApiStub({
        getUniverse: async () => [
            { sec_code: "510300.SH", sec_name: "沪深300ETF", is_active: true, added_at: "" },
        ],
    });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/universe.js"), "pages/universe.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderUniverse(container);
    await flush();

    // 刷新触发重渲染
    env.doc.getElementById("btn-refresh-universe").click();
    await flush();
    assert.equal(calls.removeUniverse.length, 0);

    const btns = env.doc.getElementById("universe-table-area").querySelectorAll("button[data-remove-code]");
    const target = btns.find((b) => b.dataset.removeCode === "510300.SH");
    target.click();
    await flush();
    assert.equal(calls.removeUniverse.length, 1, "一次点击只触发一次移除（无重复绑定）");
});

test("BUG-17：api.removeUniverse 对路径段编码", async () => {
    const fetchCalls = [];
    const doc = createDocument();
    const sandbox = {
        console,
        setTimeout: () => 0,
        clearTimeout: () => {},
        document: doc,
    };
    sandbox.window = sandbox;
    sandbox.fetch = async (path) => {
        fetchCalls.push(path);
        return { ok: true, status: 200, json: async () => ({ success: true }) };
    };
    vm.createContext(sandbox);
    runIn(sandbox, readJs("api.js"), "api.js");

    await sandbox.API.removeUniverse("510300.SH");
    assert.equal(fetchCalls[0], "/api/universe/510300.SH");

    const weird = "510300.SH+ '/x?y'";
    await sandbox.API.removeUniverse(weird);
    assert.equal(fetchCalls[1], `/api/universe/${encodeURIComponent(weird)}`);
    assert.ok(fetchCalls[1].includes("%2B"), "特殊字符必须被编码");
});
