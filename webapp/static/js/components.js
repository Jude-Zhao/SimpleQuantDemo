/* Reusable UI components: Toast, Modal, Confirm dialog */

const Components = (() => {
    // ── Toast ───────────────────────────────────────────────
    const toastContainer = (() => {
        let el = document.querySelector(".toast-container");
        if (!el) {
            el = document.createElement("div");
            el.className = "toast-container";
            document.body.appendChild(el);
        }
        return el;
    })();

    function toast(message, type = "info", duration = 3000) {
        const el = document.createElement("div");
        el.className = `toast toast-${type}`;
        el.innerHTML = `<span>${Utils.escapeHtml(message)}</span>
            <button class="toast-close" aria-label="关闭">×</button>`;
        const close = () => {
            el.classList.add("hide");
            setTimeout(() => el.remove(), 250);
        };
        el.querySelector(".toast-close").addEventListener("click", close);
        toastContainer.appendChild(el);
        if (duration > 0) setTimeout(close, duration);
    }

    // ── Modal ───────────────────────────────────────────────
    function modal({ title = "", body = "", actions = [], onMount = null } = {}) {
        const overlay = document.createElement("div");
        overlay.className = "modal-overlay";
        const actionBtns = actions
            .map(
                (a, i) => `<button class="btn ${a.variant === "primary" ? "btn-primary" : ""} ${a.variant === "danger" ? "btn-danger" : ""}"
                    data-action="${i}">${Utils.escapeHtml(a.label)}</button>`
            )
            .join("");

        overlay.innerHTML = `
            <div class="modal" role="dialog" aria-modal="true">
                ${title ? `<h3>${Utils.escapeHtml(title)}</h3>` : ""}
                <div class="modal-body">${body}</div>
                <div class="btn-group" style="justify-content:flex-end; margin-top:16px;">${actionBtns}</div>
            </div>`;

        document.body.appendChild(overlay);

        function close() {
            overlay.remove();
            document.removeEventListener("keydown", escHandler);
        }

        function escHandler(e) {
            if (e.key === "Escape") close();
        }
        document.addEventListener("keydown", escHandler);

        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) close();
        });

        actions.forEach((a, i) => {
            overlay.querySelector(`[data-action="${i}"]`).addEventListener("click", () => {
                const result = a.onClick ? a.onClick(overlay, close) : undefined;
                // Auto-close unless the handler returns false
                if (result !== false) close();
            });
        });

        if (onMount) onMount(overlay);
        return { overlay, close };
    }

    function confirmDialog(message, { title = "确认操作", okText = "确定", danger = false } = {}) {
        return new Promise((resolve) => {
            const overlay = document.createElement("div");
            overlay.className = "modal-overlay";
            overlay.innerHTML = `
                <div class="modal">
                    <h3>${Utils.escapeHtml(title)}</h3>
                    <p>${Utils.escapeHtml(message)}</p>
                    <div class="btn-group" style="justify-content:flex-end; margin-top:16px;">
                        <button class="btn" data-confirm="no">取消</button>
                        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-confirm="yes">${Utils.escapeHtml(okText)}</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);
            const close = (result) => {
                overlay.remove();
                document.removeEventListener("keydown", esc);
                resolve(result);
            };
            const esc = (e) => { if (e.key === "Escape") close(false); };
            document.addEventListener("keydown", esc);
            overlay.addEventListener("click", (e) => {
                if (e.target === overlay) close(false);
            });
            overlay.querySelector('[data-confirm="no"]').addEventListener("click", () => close(false));
            overlay.querySelector('[data-confirm="yes"]').addEventListener("click", () => close(true));
        });
    }

    function skeleton(el, lines = 4) {
        el.innerHTML = Utils.skeleton(lines);
    }

    return { toast, modal, confirmDialog, skeleton };
})();

window.Components = Components;
