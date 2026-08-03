/* Theme management: light/dark with localStorage persistence */

const Theme = (() => {
    const STORAGE_KEY = "simplequant-theme";

    function current() {
        return document.documentElement.getAttribute("data-theme") || "light";
    }

    function resolveInitial() {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved === "light" || saved === "dark") return saved;
        // Fall back to OS preference
        if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
            return "dark";
        }
        return "light";
    }

    function apply(name, { persist = true } = {}) {
        document.documentElement.setAttribute("data-theme", name);
        if (persist) {
            try {
                localStorage.setItem(STORAGE_KEY, name);
            } catch (e) { /* ignore */ }
        }
        // Let charts re-render with the new palette
        window.dispatchEvent(new CustomEvent("themechange", { detail: name }));
        // Update toggle icon state
        document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
            btn.setAttribute("aria-label", name === "dark" ? "切换到浅色模式" : "切换到暗色模式");
        });
    }

    function toggle() {
        apply(current() === "dark" ? "light" : "dark");
    }

    function init() {
        apply(resolveInitial(), { persist: false });
        // Follow OS changes only when the user has not chosen explicitly
        if (window.matchMedia) {
            window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
                const saved = localStorage.getItem(STORAGE_KEY);
                if (!saved) {
                    apply(e.matches ? "dark" : "light", { persist: false });
                }
            });
        }
    }

    return { init, toggle, apply, current };
})();

window.Theme = Theme;
