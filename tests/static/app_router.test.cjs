// F24 回归测试：SPA 路由乱序渲染——晚到的旧页面脚本不得覆盖当前路由。
// 断言修复后的正确行为（与 docs/audits/2026-09-14/reproduce-router.cjs 相反，
// 后者固定断言有 bug 的行为，修复后应当失败）。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const events = {};
const scripts = [];
const elements = new Map();
const element = () => ({innerHTML: '', textContent: '', style: {}, addEventListener() {},
    classList: {toggle() {}, add() {}, remove() {}, contains() {return false;}}});
const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    querySelectorAll() {return [];}, addEventListener() {},
    createElement() {return {};}, head: {appendChild(s) {scripts.push(s);}}, body: element(), title: ''
};
const window = {location: {hash: '#/dashboard'}, innerWidth: 1200,
    addEventListener(name, fn) {events[name] = fn;},
    renderDashboard(el) {el.innerHTML = 'dashboard';},
    renderFactors(el) {el.innerHTML = 'factors';},
    renderUniverse(el) {el.innerHTML = 'universe';},
    renderSettings(el) {el.innerHTML = 'settings';}
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../webapp/static/js/app.js'), 'utf8'),
    {window, document, Charts: {clearAll() {}}, Utils: {escapeHtml: String}, Date});

const container = () => document.getElementById('page-container');
const flush = async (n = 4) => { for (let i = 0; i < n; i++) await Promise.resolve(); };

(async () => {
    // 场景 1（审计反例）：A 加载慢，B 先完成，A 晚到不得覆盖
    events.hashchange();                          // dashboard 开始加载
    window.location.hash = '#/factors';
    events.hashchange();                          // factors 开始加载
    scripts[1].onload();
    await flush();
    assert.equal(container().innerHTML, 'factors');
    scripts[0].onload();                          // dashboard 晚到
    await flush();
    assert.equal(container().innerHTML, 'factors', '晚到的旧页面脚本覆盖了当前路由');
    assert.equal(document.title.includes('因子看板'), true, '标题被旧路由覆盖');
    assert.equal(document.getElementById('crumb-current').textContent, '因子看板', '导航被旧路由覆盖');

    // 场景 2：A→B→A 始终显示最新路由；同一页面复用加载中 Promise（脚本去重）
    window.location.hash = '#/dashboard';
    events.hashchange();                          // 重新渲染 dashboard（脚本仍在缓存）
    await flush();
    scripts[0].onload();                          // 仅一次 onload，驱动 seq1（弃）与 seq3（生效）
    await flush();
    assert.equal(container().innerHTML, 'dashboard');
    assert.equal(document.title.includes('首页'), true);
    assert.equal(scripts.length, 2, '脚本被重复插入');

    // 场景 3：未加载完成时重复进入同一页面 → 只插入一次脚本
    window.location.hash = '#/universe';
    events.hashchange();
    events.hashchange();
    assert.equal(scripts.length, 3, '加载中的页面脚本被重复插入');
    scripts[2].onload();
    await flush();
    assert.equal(container().innerHTML, 'universe');

    // 场景 4：加载失败清理缓存，允许重试
    window.location.hash = '#/settings';
    events.hashchange();
    scripts[3].onerror();
    await flush();
    assert.equal(container().innerHTML.includes('加载页面失败'), true);
    events.hashchange();                          // 重试
    assert.equal(scripts.length, 5, '失败 Promise 未清理，无法重试');
    scripts[4].onload();
    await flush();
    assert.equal(container().innerHTML, 'settings');

    console.log('F24 router regression: all scenarios passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
