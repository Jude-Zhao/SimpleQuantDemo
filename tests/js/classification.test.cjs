"use strict";

/* BUG-13/16 Node 测试：classification.js 页面脚本行为。
 * 使用 node:test + vm 加载真实页面 JS 与真实 utils.js，DOM 用最小替身
 * （仅覆盖页面脚本实际用到的 API：innerHTML 解析、getElementById、
 * querySelector(All)、addEventListener/dispatchEvent、value/style/dataset 等）。
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

// ══ 最小 DOM 替身 ═══════════════════════════════════════════════════
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
    const calls = {
        getConstraints: 0,
        updateConstraints: [],
        addUniverse: [],
        removeUniverse: [],
    };
    const api = {
        listStrategies: async () => [],
        listRuns: async () => [],
        getUniverse: async () => [],
        getClassifications: async () => [],
        listRules: async () => [],
        getConstraints: async () => { throw new Error("getConstraints 未在测试中配置"); },
        updateConstraints: async (payload) => {
            calls.updateConstraints.push(JSON.parse(JSON.stringify(payload)));
            return JSON.parse(JSON.stringify(payload));
        },
        addUniverse: async (items) => { calls.addUniverse.push(items); return items; },
        removeUniverse: async (code) => { calls.removeUniverse.push(code); return { success: true }; },
        ...overrides,
    };
    return { api, calls };
}

function createContextEnv({ api, components } = {}) {
    const doc = createDocument();
    const state = { toasts: [], confirmMessages: [] };
    const sandbox = {
        console,
        setTimeout: () => 0, // toast 自动关闭等计时器在测试中不调度
        clearTimeout: () => {},
        Event: class Event { constructor(type) { this.type = type; } },
        document: doc,
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    runIn(sandbox, readJs("utils.js"), "utils.js");
    if (components === "real") {
        runIn(sandbox, readJs("components.js"), "components.js");
    } else {
        sandbox.Components = components || {
            toast: (message, type) => { state.toasts.push({ message, type: type || "info" }); },
            modal: () => { throw new Error("modal 不应在本测试中调用"); },
            confirmDialog: async (message) => {
                state.confirmMessages.push(message);
                return state.confirmResult === undefined ? true : state.confirmResult;
            },
            runStatusBadge: (s) => `<span class="badge">${s}</span>`,
        };
    }
    sandbox.API = api;
    return { sandbox, doc, state };
}

async function flush(turns = 10) {
    for (let i = 0; i < turns; i++) await new Promise((resolve) => setImmediate(resolve));
}

// ══ 测试 ════════════════════════════════════════════════════════════
const LOADED = {
    single_min_weight: 0.01,
    single_max_weight: 0.15,
    category_constraints: [
        { category_key: "category", category_value: "宽基", min_weight: 0.1, max_weight: 0.4 },
    ],
    turnover_limit: 0,
};

test("BUG-13：classification.js 使用页面独有函数名，不再定义全局 loadConstraints", async () => {
    const { api } = makeApiStub({ getConstraints: async () => LOADED });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    assert.equal(env.sandbox.loadConstraints, undefined, "不得再定义全局同名 loadConstraints");
    assert.equal(typeof env.sandbox.loadClassificationConstraints, "function");
    assert.equal(typeof env.sandbox.saveConstraints, "function");
});

test("BUG-16：成功加载后填充三个可见输入并建立快照", async () => {
    const { api, calls } = makeApiStub({ getConstraints: async () => LOADED });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    const minEl = env.doc.getElementById("constraint-single-min");
    const maxEl = env.doc.getElementById("constraint-single-max");
    const catsEl = env.doc.getElementById("constraint-categories");
    assert.equal(String(minEl.value), "0.01");
    assert.equal(String(maxEl.value), "0.15");
    assert.ok(catsEl.value.includes('"category_value": "宽基"'));
    assert.equal(calls.updateConstraints.length, 0, "加载本身不发 PUT");
});

test("BUG-16：0 保留为 0，隐藏字段 turnover_limit 从快照保留", async () => {
    const { api, calls } = makeApiStub({ getConstraints: async () => LOADED });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    env.doc.getElementById("constraint-single-min").value = "0";
    env.doc.getElementById("constraint-single-max").value = "0";
    await env.sandbox.saveConstraints();

    assert.equal(calls.updateConstraints.length, 1);
    const payload = calls.updateConstraints[0];
    assert.equal(payload.single_min_weight, 0, "0 必须保留为 0（不是 null）");
    assert.equal(payload.single_max_weight, 0, "0 必须保留为 0（不是 null）");
    assert.equal(payload.turnover_limit, 0, "隐藏字段 turnover_limit 从快照复制保留");
    assert.deepEqual(payload.category_constraints, LOADED.category_constraints);
});

test("BUG-16：空串解析为 null", async () => {
    const { api, calls } = makeApiStub({ getConstraints: async () => LOADED });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    env.doc.getElementById("constraint-single-min").value = "";
    env.doc.getElementById("constraint-single-max").value = "   ";
    await env.sandbox.saveConstraints();

    const payload = calls.updateConstraints[0];
    assert.equal(payload.single_min_weight, null);
    assert.equal(payload.single_max_weight, null);
});

test("BUG-16：非法数字（badInput/NaN/Infinity）提示且不发请求", async () => {
    const { api, calls } = makeApiStub({ getConstraints: async () => LOADED });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    const minEl = env.doc.getElementById("constraint-single-min");
    const lastToast = () => env.state.toasts[env.state.toasts.length - 1]?.message || "";

    // 1) 浏览器 badInput（value 会被浏览器置空，但不得当作空串→null）
    minEl.value = "";
    minEl.validity.badInput = true;
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0, "badInput 不得当 null 发请求");
    assert.ok(lastToast().includes("单票最小权重"), lastToast());

    // 2) "1abc"：即使 badInput 未置位，Number("1abc") 为 NaN 也必须拒绝
    minEl.validity.badInput = false;
    minEl.value = "1abc";
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0, "Number('1abc') 是 NaN，不得发请求");
    assert.ok(lastToast().includes("单票最小权重"), lastToast());

    // 3) Infinity：非有限值拒绝
    minEl.value = "Infinity";
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0, "Infinity 不得发请求");
    assert.ok(lastToast().includes("有限数字"), lastToast());
});

test("BUG-16：分类约束 JSON 非法或不是数组时不发请求", async () => {
    const { api, calls } = makeApiStub({ getConstraints: async () => LOADED });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    const catsEl = env.doc.getElementById("constraint-categories");
    const lastToast = () => env.state.toasts[env.state.toasts.length - 1]?.message || "";

    catsEl.value = "{bad json";
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0);
    assert.ok(lastToast().includes("分类约束 JSON 格式错误"), lastToast());

    catsEl.value = '{"a": 1}';
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0);
    assert.ok(lastToast().includes("数组"), lastToast());
});

test("BUG-13/16：首次加载失败可见，保存入口拒绝、不发请求", async () => {
    const { api, calls } = makeApiStub({
        getConstraints: async () => { throw new Error("服务不可用"); },
    });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    const lastToast = () => env.state.toasts[env.state.toasts.length - 1]?.message || "";
    assert.ok(lastToast().includes("约束配置加载失败"), lastToast());
    assert.ok(lastToast().includes("服务不可用"), lastToast());

    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0, "未加载成功不得保存");
    assert.ok(lastToast().includes("尚未加载成功"), lastToast());
});

test("BUG-13/16：重渲染加载失败可见，且旧快照不被覆盖（恢复后可正常保存）", async () => {
    let fail = false;
    const { api, calls } = makeApiStub({
        getConstraints: async () => {
            if (fail) throw new Error("网络中断");
            return LOADED;
        },
    });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    // 第二次渲染（返回页面）：加载失败
    fail = true;
    env.sandbox.renderClassification(container);
    await flush();
    const lastToast = () => env.state.toasts[env.state.toasts.length - 1]?.message || "";
    assert.ok(lastToast().includes("网络中断"), lastToast());

    // 失败后保存被拒：不得用空输入覆盖旧快照
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 0, "加载失败后保存必须被拒绝");

    // 恢复加载成功后保存可用，且快照保留隐藏字段
    fail = false;
    await env.sandbox.loadClassificationConstraints();
    await flush();
    env.doc.getElementById("constraint-single-min").value = "0.05";
    env.doc.getElementById("constraint-single-max").value = "0.2";
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 1);
    assert.equal(calls.updateConstraints[0].turnover_limit, 0);
    assert.equal(calls.updateConstraints[0].single_min_weight, 0.05);
});

test("BUG-16：PUT 返回完整配置后更新快照", async () => {
    const { api, calls } = makeApiStub({
        getConstraints: async () => LOADED,
        updateConstraints: async (payload) => {
            calls.updateConstraints.push(JSON.parse(JSON.stringify(payload)));
            // 模拟后端 PUT 返回完整配置（带出新的隐藏字段值）
            return { ...JSON.parse(JSON.stringify(payload)), turnover_limit: 0.7 };
        },
    });
    const env = createContextEnv({ api });
    runIn(env.sandbox, readJs("pages/classification.js"), "pages/classification.js");

    const container = env.doc.createElement("div");
    env.doc.body.appendChild(container);
    env.sandbox.renderClassification(container);
    await flush();

    env.doc.getElementById("constraint-single-min").value = "0.05";
    env.doc.getElementById("constraint-single-max").value = "0.2";
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 1);

    // 第二次保存：快照已被 PUT 响应更新（turnover_limit=0.7）
    await env.sandbox.saveConstraints();
    assert.equal(calls.updateConstraints.length, 2);
    assert.equal(calls.updateConstraints[1].turnover_limit, 0.7);
    assert.equal(calls.updateConstraints[1].single_min_weight, 0.05);
});
