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

        // BUG-15：close 幂等；弹窗级 pending 防重入（保存期间 Escape/遮罩
        // 不关闭，避免丢失表单输入）。
        let closed = false;
        let pending = false;

        // modal 局部遍历操作按钮（不新建全局工具）。
        function setButtonsDisabled(disabled) {
            overlay.querySelectorAll("button[data-action]").forEach((btn) => {
                btn.disabled = disabled;
            });
        }

        function close() {
            if (closed) return;
            closed = true;
            overlay.remove();
            document.removeEventListener("keydown", escHandler);
        }

        function escHandler(e) {
            if (e.key !== "Escape") return;
            if (pending) return; // pending 时不关闭，避免丢表单
            close();
        }
        document.addEventListener("keydown", escHandler);

        overlay.addEventListener("click", (e) => {
            if (e.target === overlay && !pending) close();
        });

        actions.forEach((a, i) => {
            overlay.querySelector(`[data-action="${i}"]`).addEventListener("click", async () => {
                if (pending) return; // 防重入：未决 Promise 期间双击只触发一次
                pending = true;
                setButtonsDisabled(true);
                try {
                    // 支持同步/异步回调：等待结果完成后再决定是否关闭。
                    const result = a.onClick ? await a.onClick(overlay, close) : undefined;
                    if (result !== false) close();
                } catch (error) {
                    // 回调失败：用现有 toast 呈现错误，保留 overlay（输入不丢）。
                    toast(`操作失败: ${error && error.message ? error.message : String(error)}`, "error");
                } finally {
                    pending = false;
                    setButtonsDisabled(false);
                }
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

    /** Badge markup for a strategy run status (used by run-history tables). */
    function runStatusBadge(status) {
        const badgeMap = {
            success: "badge-success",
            failed: "badge-danger",
            running: "badge-info",
            pending: "badge-muted",
        };
        return `<span class="badge ${badgeMap[status] || "badge-muted"}">${Utils.escapeHtml(status)}</span>`;
    }

    /**
     * Drive the shared macro-sync UI (confirm → disable button → progress →
     * result) for both the macro page and the settings page.
     * @param {object} o - { frequency, btn, wrap, fill, statusText, percentText, resultText }
     */
    async function runMacroSync({ frequency, btn, wrap, fill, statusText, percentText, resultText }) {
        const freqLabel = frequency === "daily" ? "日频" : "月频";
        const ok = await confirmDialog(
            `确定要全量同步${freqLabel}宏观数据吗？<br><br>将删除旧数据并重新拉取（约需 1-3 分钟）。`,
            { okText: "开始同步", okClass: "btn-primary" }
        );
        if (!ok) return;

        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<svg class="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg> 同步中...`;
        wrap.style.display = "block";
        resultText.style.display = "none";
        fill.style.width = "0%";
        percentText.textContent = "0%";
        statusText.textContent = "准备中...";

        try {
            const task = await API.syncMacro({ frequency });
            const final = await API.pollMacroSyncTask(task.task_id, (t) => {
                statusText.textContent = t.message;
                // Macro sync has no discrete progress; use 90% cap while running.
                const pct = t.status === "running" ? 90 : t.status === "completed" ? 100 : 0;
                fill.style.width = pct + "%";
                percentText.textContent = pct + "%";
            }, 3000);

            if (final.status === "completed") {
                const r = final.result || {};
                fill.style.width = "100%";
                percentText.textContent = "100%";
                statusText.textContent = final.message;
                resultText.style.display = "block";
                resultText.textContent = `✅ 同步完成，共 ${r.rows || 0} 条数据`;
                return true;
            }
            resultText.style.display = "block";
            resultText.textContent = `❌ 同步失败：${final.error || "未知错误"}`;
            return false;
        } catch (e) {
            resultText.style.display = "block";
            resultText.textContent = `❌ 同步失败：${e.message}`;
            return false;
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }

    return { toast, modal, confirmDialog, skeleton, runStatusBadge, runMacroSync };
})();

window.Components = Components;
