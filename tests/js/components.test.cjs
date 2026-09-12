"use strict";

/* BUG-15 Node 测试：components.js 的 modal（真实实现 + DOM 替身）。
 * 覆盖：同步 false、同步 throw、Promise false、Promise reject、
 * 未决 Promise 双击防重入、显式 close 与无回调关闭、close 幂等、
 * pending 时 Escape/遮罩不丢表单。
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

// ══ 加载真实 components.js ══════════════════════════════════════════
function loadComponents() {
    const doc = createDocument();
    const sandbox = {
        console,
        setTimeout: () => 0, // toast 自动关闭计时器不调度
        clearTimeout: () => {},
        Event: class Event { constructor(type) { this.type = type; } },
        document: doc,
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(readJs("utils.js"), sandbox, { filename: "utils.js" });
    vm.runInContext(readJs("components.js"), sandbox, { filename: "components.js" });
    return { doc, Components: sandbox.Components };
}

async function flush(turns = 10) {
    for (let i = 0; i < turns; i++) await new Promise((resolve) => setImmediate(resolve));
}

const actionBtn = (overlay, i) => overlay.querySelector(`[data-action="${i}"]`);
const isOpen = (overlay) => overlay.parentNode !== null;
const toastTexts = (doc) => doc.querySelectorAll(".toast").map((el) => el.innerHTML);

test("BUG-15：同步回调返回 false——弹窗保留", async () => {
    const { Components } = loadComponents();
    let calls = 0;
    const m = Components.modal({
        title: "编辑",
        actions: [{ label: "保存", onClick: () => { calls += 1; return false; } }],
    });
    actionBtn(m.overlay, 0).click();
    await flush(3);
    assert.equal(calls, 1);
    assert.ok(isOpen(m.overlay), "返回 false 必须保留弹窗");
});

test("BUG-15：同步回调 throw——弹窗保留并 toast 错误", async () => {
    const { doc, Components } = loadComponents();
    const m = Components.modal({
        actions: [{ label: "保存", onClick: () => { throw new Error("boom"); } }],
    });
    actionBtn(m.overlay, 0).click();
    await flush(3);
    assert.ok(isOpen(m.overlay), "回调异常必须保留弹窗");
    assert.ok(toastTexts(doc).some((t) => t.includes("操作失败: boom")), toastTexts(doc).join("|"));
});

test("BUG-15：Promise 回调 resolve false——等待完成后弹窗保留", async () => {
    const { Components } = loadComponents();
    const m = Components.modal({
        actions: [{ label: "保存", onClick: async () => { await Promise.resolve(); return false; } }],
    });
    actionBtn(m.overlay, 0).click();
    await flush(3);
    assert.ok(isOpen(m.overlay), "异步 false 必须保留弹窗（旧实现会立即关闭）");
});

test("BUG-15：Promise 回调 reject——弹窗保留、输入不丢并 toast", async () => {
    const { doc, Components } = loadComponents();
    const m = Components.modal({
        body: '<input id="rule-name" value="我的输入" />',
        actions: [{ label: "保存", onClick: () => Promise.reject(new Error("api down")) }],
    });
    actionBtn(m.overlay, 0).click();
    await flush(3);
    assert.ok(isOpen(m.overlay), "reject 必须保留弹窗");
    assert.equal(m.overlay.querySelector("#rule-name").value, "我的输入", "表单输入不丢");
    assert.ok(toastTexts(doc).some((t) => t.includes("api down")), toastTexts(doc).join("|"));
});

test("BUG-15：未决 Promise 双击防重入——只触发一次请求，期间按钮禁用", async () => {
    const { Components } = loadComponents();
    let calls = 0;
    let resolveFn;
    const m = Components.modal({
        actions: [{
            label: "保存",
            onClick: () => {
                calls += 1;
                return new Promise((res) => { resolveFn = res; });
            },
        }],
    });
    const btn = actionBtn(m.overlay, 0);
    btn.click();
    btn.click(); // 未决期间双击
    await flush(2);
    assert.equal(calls, 1, "防重入：未决期间第二次点击不得触发回调");
    assert.equal(btn.disabled, true, "保存期间操作按钮禁用");

    resolveFn(undefined);
    await flush(5);
    assert.equal(calls, 1);
    assert.ok(!isOpen(m.overlay), "完成后自动关闭");
    assert.equal(btn.disabled, false, "结束后按钮恢复");
});

test("BUG-15：显式 close 与无回调动作均可关闭，close 幂等", async () => {
    const { Components } = loadComponents();

    // 显式 close（回调内部调用 close 且不返回值）
    const m1 = Components.modal({
        actions: [{ label: "OK", onClick: (overlay, close) => { close(); } }],
    });
    actionBtn(m1.overlay, 0).click();
    await flush(3);
    assert.ok(!isOpen(m1.overlay), "显式 close 后关闭");
    assert.ok(!isOpen(m1.overlay));

    // 无回调动作
    const m2 = Components.modal({ actions: [{ label: "关闭" }] });
    actionBtn(m2.overlay, 0).click();
    await flush(3);
    assert.ok(!isOpen(m2.overlay), "无回调动作默认关闭");

    // close 幂等：重复调用不抛错
    const m3 = Components.modal({ actions: [{ label: "X" }] });
    m3.close();
    m3.close();
    assert.ok(!isOpen(m3.overlay));
});

test("BUG-15：pending 时 Escape/遮罩点击不关闭（表单不丢），结束后恢复关闭行为", async () => {
    const { doc, Components } = loadComponents();
    let resolveFn;
    const m = Components.modal({
        body: '<input id="field" value="重要数据" />',
        actions: [{ label: "保存", onClick: () => new Promise((res) => { resolveFn = res; }) }],
    });
    actionBtn(m.overlay, 0).click();
    await flush(2);

    doc.dispatchEvent({ type: "keydown", key: "Escape" });
    assert.ok(isOpen(m.overlay), "pending 时 Escape 不得关闭");
    m.overlay.dispatchEvent({ type: "click", target: m.overlay });
    assert.ok(isOpen(m.overlay), "pending 时遮罩点击不得关闭");
    assert.equal(m.overlay.querySelector("#field").value, "重要数据");

    resolveFn(undefined);
    await flush(5);
    assert.ok(!isOpen(m.overlay), "完成后关闭");

    // 非 pending 时 Escape 正常关闭
    const m2 = Components.modal({ actions: [{ label: "OK" }] });
    doc.dispatchEvent({ type: "keydown", key: "Escape" });
    assert.ok(!isOpen(m2.overlay), "非 pending 时 Escape 关闭");
});

test("BUG-15：同步确认路径不回归（同步 true 关闭）", async () => {
    const { Components } = loadComponents();
    const m = Components.modal({
        actions: [{ label: "确认", onClick: () => true }],
    });
    actionBtn(m.overlay, 0).click();
    await flush(3);
    assert.ok(!isOpen(m.overlay), "同步返回 true 关闭");
});
