// Offline reproduction: intentionally assert current buggy routing behavior.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const events = {};
const scripts = [];
const elements = new Map();
const element = () => ({innerHTML: '', textContent: '', addEventListener() {},
    classList: {toggle() {}, add() {}, remove() {}, contains() {return false;}}});
const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    querySelectorAll() {return [];}, addEventListener() {},
    createElement() {return {};}, head: {appendChild(s) {scripts.push(s);}}, body: element(), title: ''
};
const window = {location: {hash: '#/dashboard'}, innerWidth: 1200,
    addEventListener(name, fn) {events[name] = fn;},
    renderDashboard(el) {el.innerHTML = 'dashboard';},
    renderFactors(el) {el.innerHTML = 'factors';}
};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../../webapp/static/js/app.js'), 'utf8'),
    {window, document, Charts: {clearAll() {}}, Utils: {escapeHtml: String}, Date});
(async () => {
    events.hashchange();                         // A starts loading dashboard.js
    window.location.hash = '#/factors';
    events.hashchange();                         // B starts loading factors.js
    scripts[1].onload();                         // B completes first
    await Promise.resolve(); await Promise.resolve();
    assert.equal(document.getElementById('page-container').innerHTML, 'factors');
    scripts[0].onload();                         // A late response overwrites B
    await Promise.resolve(); await Promise.resolve();
    assert.equal(document.getElementById('page-container').innerHTML, 'dashboard');
    const evidence = {url: window.location.hash, content: document.getElementById('page-container').innerHTML,
        reproduced: true};
    fs.writeFileSync(path.join(__dirname, 'router-evidence.json'), JSON.stringify(evidence, null, 2));
    console.log(evidence);
})().catch(e => {console.error(e); process.exitCode = 1;});
