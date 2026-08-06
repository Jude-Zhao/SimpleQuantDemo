/* SimpleQuant SPA: hash router, sidebar, topbar, global search, shortcuts */

(function () {
    const container = document.getElementById("page-container");
    const sidebar = document.getElementById("sidebar");
    const navItems = document.querySelectorAll(".nav-item");
    const crumbCurrent = document.getElementById("crumb-current");

    const ROUTES = {
        dashboard: { title: "首页", file: "dashboard.js", fn: "renderDashboard" },
        factors: { title: "因子看板", file: "factors.js", fn: "renderFactors" },
        strategies: { title: "策略运行", file: "strategies.js", fn: "renderStrategies" },
        universe: { title: "标的池", file: "universe.js", fn: "renderUniverse" },
        macro: { title: "宏观数据", file: "macro.js", fn: "renderMacro" },
        classification: { title: "分类约束", file: "classification.js", fn: "renderClassification" },
        settings: { title: "设置", file: "settings.js", fn: "renderSettings" },
    };

    const scriptCache = {};

    function loadScript(route) {
        const { file, fn } = ROUTES[route];
        if (scriptCache[route]) {
            return Promise.resolve(scriptCache[route]);
        }
        return new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = `/static/js/pages/${file}`;
            script.onload = () => {
                const renderFn = window[fn];
                if (typeof renderFn !== "function") {
                    reject(new Error(`页面模块 ${fn} 未定义`));
                    return;
                }
                scriptCache[route] = renderFn;
                resolve(renderFn);
            };
            script.onerror = () => reject(new Error(`加载 ${file} 失败`));
            document.head.appendChild(script);
        });
    }

    function setActiveNav(route) {
        navItems.forEach((item) => {
            item.classList.toggle("active", item.dataset.route === route);
        });
        crumbCurrent.textContent = ROUTES[route]?.title || "首页";
        // Refresh sidebar nav icons for active state
    }

    function currentRoute() {
        const hash = window.location.hash.replace("#/", "").split("?")[0];
        return ROUTES[hash] ? hash : "dashboard";
    }

    async function render(route) {
        const r = ROUTES[route] || ROUTES.dashboard;
        container.innerHTML = `<div class="loading"><div class="spinner"></div>加载中...</div>`;
        try {
            const renderFn = await loadScript(route);
            renderFn(container);
        } catch (e) {
            container.innerHTML = `<div class="alert alert-error">加载页面失败: ${Utils.escapeHtml(e.message)}</div>`;
        }
        setActiveNav(route);
        document.title = `${r.title} · SimpleQuant 量化看板`;
    }

    function navigate(route) {
        if (window.location.hash === `#/${route}`) {
            render(route);
        } else {
            window.location.hash = `#/${route}`;
        }
    }

    // ── Sidebar collapse / mobile ───────────────────────────
    const toggleBtn = document.getElementById("sidebar-toggle");
    const burger = document.getElementById("burger");
    const isMobile = () => window.innerWidth < 1024;

    toggleBtn.addEventListener("click", () => {
        if (isMobile()) {
            sidebar.classList.toggle("mobile-open");
            return;
        }
        document.body.classList.toggle("sidebar-collapsed");
    });

    burger.addEventListener("click", () => {
        sidebar.classList.add("mobile-open");
    });

    document.addEventListener("click", (e) => {
        if (isMobile() && sidebar.classList.contains("mobile-open") &&
            !sidebar.contains(e.target) && !burger.contains(e.target)) {
            sidebar.classList.remove("mobile-open");
        }
    });

    // ── Health check ────────────────────────────────────────
    const statusDots = document.querySelectorAll("#api-status-dot, #top-status-dot");
    const statusTexts = {};

    async function checkHealth() {
        const textEl = document.getElementById("api-status");
        const topText = document.getElementById("top-status-text");
        try {
            const data = await API.request("/api/health");
            const ok = data.status === "ok";
            statusDots.forEach((d) => {
                d.classList.toggle("online", ok);
                d.classList.toggle("offline", !ok);
            });
            textEl.textContent = ok ? "服务正常" : "服务异常";
            topText.textContent = ok ? "服务正常" : "服务异常";
        } catch (e) {
            statusDots.forEach((d) => {
                d.classList.remove("online");
                d.classList.add("offline");
            });
            textEl.textContent = "服务离线";
            topText.textContent = "服务离线";
        }
    }

    // ── Global search (Ctrl+K) ──────────────────────────────
    function buildSearchIndex() {
        const pages = Object.entries(ROUTES).map(([key, r]) => ({
            type: "页面",
            label: r.title,
            icon: "page",
            route: key,
        }));
        return pages;
    }

    let searchModal = null;

    function openSearch() {
        // Stale reference check: re-open if the previous overlay was removed
        if (searchModal && !document.body.contains(searchModal.overlay)) {
            searchModal = null;
        }
        if (searchModal) return;
        const pages = buildSearchIndex();
        let etfs = [];

        // Load ETF codes for searchable suggestions (best effort)
        API.getUniverse()
            .then((universe) => {
                etfs = universe.map((u) => ({
                    type: "ETF",
                    label: `${u.sec_code} ${u.sec_name || ""}`.trim(),
                    route: "universe",
                }));
            })
            .catch(() => {});

        searchModal = Components.modal({
            body: `
                <div class="search-input-wrap">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
                    <input id="global-search-input" type="text" placeholder="搜索页面、ETF 代码..." autocomplete="off" />
                    <span class="kbd" style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted);border:1px solid var(--border-subtle);border-radius:4px;padding:1px 5px;">Esc</span>
                </div>
                <div class="search-results" id="global-search-results"></div>
            `,
            onMount: (overlay) => {
                const input = overlay.querySelector("#global-search-input");
                const resultsEl = overlay.querySelector("#global-search-results");
                let items = [];
                let activeIndex = -1;

                function allItems() {
                    return [...pages, ...etfs];
                }

                function renderResults(query) {
                    const q = query.trim().toLowerCase();
                    const matches = q
                        ? allItems().filter(
                              (it) => it.label.toLowerCase().includes(q) || (it.type === "页面" && it.route.includes(q))
                          )
                        : allItems().slice(0, 12);

                    if (!matches.length) {
                        resultsEl.innerHTML = `<div class="search-empty">没有匹配结果</div>`;
                        items = [];
                        activeIndex = -1;
                        return;
                    }

                    items = matches;
                    activeIndex = -1;
                    resultsEl.innerHTML = matches
                        .map(
                            (it, i) => `
                            <div class="search-result-item ${i === activeIndex ? "active" : ""}" data-index="${i}">
                                <span class="sr-icon">${it.type === "页面" ? "📄" : "📈"}</span>
                                <span>${Utils.escapeHtml(it.label)}</span>
                                <span class="sr-meta">${it.type}</span>
                            </div>`
                        )
                        .join("");

                    resultsEl.querySelectorAll(".search-result-item").forEach((itemEl) => {
                        itemEl.addEventListener("click", () => {
                            const idx = parseInt(itemEl.dataset.index, 10);
                            pick(items[idx]);
                        });
                    });
                }

                function pick(item) {
                    if (!item) return;
                    closeSearch();
                    navigate(item.route);
                }

                function highlight(i) {
                    activeIndex = i;
                    resultsEl.querySelectorAll(".search-result-item").forEach((el) => {
                        el.classList.toggle("active", parseInt(el.dataset.index, 10) === i);
                    });
                    const activeEl = resultsEl.querySelector(`[data-index="${i}"]`);
                    activeEl?.scrollIntoView({ block: "nearest" });
                }

                input.addEventListener("input", () => {
                    renderResults(input.value);
                });

                input.addEventListener("keydown", (e) => {
                    if (e.key === "ArrowDown") {
                        e.preventDefault();
                        if (items.length) highlight((activeIndex + 1) % items.length);
                    } else if (e.key === "ArrowUp") {
                        e.preventDefault();
                        if (items.length) highlight((activeIndex - 1 + items.length) % items.length);
                    } else if (e.key === "Enter") {
                        e.preventDefault();
                        if (activeIndex >= 0) pick(items[activeIndex]);
                        else if (items.length) pick(items[0]);
                    }
                });

                setTimeout(() => input.focus(), 30);
                renderResults("");
            },
        });
        searchModal = { overlay: searchModal.overlay, close: searchModal.close };

        function closeSearch() {
            if (searchModal) {
                searchModal.close();
                searchModal = null;
            }
        }
    }

    document.getElementById("global-search-trigger").addEventListener("click", openSearch);

    // ── Theme toggle ────────────────────────────────────────
    document.getElementById("theme-toggle").addEventListener("click", () => Theme.toggle());

    // ── Keyboard shortcuts ──────────────────────────────────
    document.addEventListener("keydown", (e) => {
        const k = e.key.toLowerCase();
        // Ctrl+K: global search
        if (e.ctrlKey && k === "k") {
            e.preventDefault();
            openSearch();
            return;
        }
        // Ctrl+R: refresh current page
        if (e.ctrlKey && k === "r") {
            const route = currentRoute();
            if (route) {
                e.preventDefault();
                render(route);
                Components.toast("已刷新", "info", 1200);
            }
            return;
        }
        // Ctrl+1..7: switch pages
        if (e.ctrlKey && /^[1-7]$/.test(k)) {
            const routes = Object.keys(ROUTES);
            const target = routes[parseInt(k, 10) - 1];
            if (target) {
                e.preventDefault();
                navigate(target);
            }
        }
        // Esc: close mobile sidebar / search
        if (e.key === "Escape") {
            if (isMobile()) sidebar.classList.remove("mobile-open");
        }
    });

    // Sidebar nav click closes mobile drawer
    navItems.forEach((item) => {
        item.addEventListener("click", () => {
            if (isMobile()) sidebar.classList.remove("mobile-open");
        });
    });

    // ── Boot ────────────────────────────────────────────────
    window.addEventListener("hashchange", () => render(currentRoute()));
    window.addEventListener("DOMContentLoaded", () => {
        Theme.init();
        checkHealth();
        render(currentRoute());
        // Refresh health periodically
        setInterval(checkHealth, 30000);
    });
})();
