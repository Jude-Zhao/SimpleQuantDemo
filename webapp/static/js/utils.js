/* Utility helpers: throttle, debounce, formatting, DOM helpers */

const Utils = (() => {
    function throttle(fn, wait) {
        let last = 0;
        let timer = null;
        return function (...args) {
            const now = Date.now();
            if (now - last >= wait) {
                last = now;
                fn.apply(this, args);
            } else if (!timer) {
                timer = setTimeout(() => {
                    timer = null;
                    last = Date.now();
                    fn.apply(this, args);
                }, wait - (now - last));
            }
        };
    }

    function debounce(fn, wait) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), wait);
        };
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatPct(value, digits = 2) {
        if (value === null || value === undefined || isNaN(value)) return "-";
        return (value * 100).toFixed(digits) + "%";
    }

    function formatNum(value, digits = 2) {
        if (value === null || value === undefined || isNaN(value)) return "-";
        return Number(value).toFixed(digits);
    }

    function formatDate(isoStr) {
        if (!isoStr) return "-";
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return isoStr;
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    /** Animate a number from its current text to a target value (300ms). */
    function animateNumber(el, target, { digits = 0, suffix = "", duration = 300 } = {}) {
        const from = parseFloat((el.dataset.value || "0").replace(/,/g, "")) || 0;
        const start = performance.now();
        el.dataset.value = target;

        function step(now) {
            const t = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - t, 3);
            const val = from + (target - from) * eased;
            el.textContent = (digits > 0 ? val.toFixed(digits) : Math.round(val).toLocaleString()) + suffix;
            if (t < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    function kebabToTitle(str) {
        return String(str || "")
            .split("-")
            .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
            .join(" ");
    }

    /** Build a skeleton screen placeholder. */
    function skeleton(lines = 4) {
        let html = `<div class="skeleton">`;
        for (let i = 0; i < lines; i++) {
            const w = ["w-40", "w-60", "w-80", "w-100"][i % 4];
            html += `<div class="skeleton-line ${w}"></div>`;
        }
        html += `<div class="skeleton-block"></div></div>`;
        return html;
    }

    return {
        throttle,
        debounce,
        escapeHtml,
        formatPct,
        formatNum,
        formatDate,
        animateNumber,
        kebabToTitle,
        skeleton,
    };
})();

window.Utils = Utils;
