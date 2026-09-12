/* SimpleQuant API client */

const API = {
    /**
     * Generic fetch wrapper with error handling.
     * @param {string} path - API path starting with /
     * @param {object} options - fetch options
     * @returns {Promise<any>} parsed JSON response
     */
    async request(path, options = {}) {
        const opts = {
            headers: { "Content-Type": "application/json" },
            ...options,
        };
        if (opts.body && typeof opts.body !== "string") {
            opts.body = JSON.stringify(opts.body);
        }
        const resp = await fetch(path, opts);
        if (!resp.ok) {
            let detail = `HTTP ${resp.status}`;
            try {
                const data = await resp.json();
                detail = data.detail || JSON.stringify(data);
            } catch (e) {
                // ignore
            }
            throw new Error(detail);
        }
        return resp.json();
    },

    // ── Factors ────────────────────────────────────────────
    async listFactors() {
        return this.request("/api/factors");
    },

    async computeFactor(payload) {
        return this.request("/api/factors/compute", { method: "POST", body: payload });
    },

    async factorCorrelation({ granularity = "instance", factorIds = [] } = {}) {
        return this.request("/api/factors/correlation", {
            method: "POST",
            body: { granularity, factor_ids: factorIds },
        });
    },

    // ── Universe ───────────────────────────────────────────
    async getUniverse() {
        return this.request("/api/universe");
    },

    async addUniverse(items) {
        return this.request("/api/universe", { method: "POST", body: { items } });
    },

    async removeUniverse(secCode) {
        // BUG-17：路径段必须编码，防止 sec_code 中的特殊字符改变请求语义。
        return this.request(`/api/universe/${encodeURIComponent(secCode)}`, { method: "DELETE" });
    },

    // ── Classification ─────────────────────────────────────
    async listRules(activeOnly = true) {
        return this.request(`/api/classifications/rules?active_only=${activeOnly}`);
    },

    async createRule(payload) {
        return this.request("/api/classifications/rules", { method: "POST", body: payload });
    },

    async updateRule(id, payload) {
        return this.request(`/api/classifications/rules/${id}`, { method: "PUT", body: payload });
    },

    async deleteRule(id) {
        return this.request(`/api/classifications/rules/${id}`, { method: "DELETE" });
    },

    async applyClassification() {
        return this.request("/api/classifications/apply", { method: "POST" });
    },

    async getClassifications() {
        return this.request("/api/classifications");
    },

    async getConstraints() {
        return this.request("/api/constraints");
    },

    async updateConstraints(payload) {
        return this.request("/api/constraints", { method: "PUT", body: { constraints: payload } });
    },

    // ── Strategies ─────────────────────────────────────────
    async listStrategies() {
        return this.request("/api/strategies");
    },

    async runStrategy(payload) {
        return this.request("/api/strategies/run", { method: "POST", body: payload });
    },

    async listRuns(limit = 20) {
        return this.request(`/api/strategies/runs?limit=${limit}`);
    },

    async getRun(id) {
        return this.request(`/api/strategies/runs/${id}`);
    },

    async exportRun(id) {
        return this.request(`/api/strategies/runs/${id}/export`);
    },

    /**
     * Poll until a task reaches a terminal state.
     * @param {function} getStatus - async () => task object
     * @param {function} onProgress - callback with task object
     * @param {number} intervalMs - poll interval
     * @param {function} [isDone] - (task) => boolean; defaults to the sync-task
     *   terminal states (completed/failed). Callers like strategy runs pass a
     *   custom predicate (success/failed).
     * @returns {Promise<object>} final task object
     */
    async pollTask(getStatus, onProgress, intervalMs = 2000, isDone) {
        const done = isDone || ((t) => t.status === "completed" || t.status === "failed");
        while (true) {
            const task = await getStatus();
            if (onProgress) onProgress(task);
            if (done(task)) {
                return task;
            }
            await new Promise((r) => setTimeout(r, intervalMs));
        }
    },

    // ── Data Sync ──────────────────────────────────────────
    async syncEtf(options = {}) {
        return this.request("/api/market/sync/etf", { method: "POST", body: options });
    },

    async getSyncStatus(taskId) {
        return this.request(`/api/market/sync/${taskId}`);
    },

    async pollSyncTask(taskId, onProgress, intervalMs = 2000) {
        return this.pollTask(() => this.getSyncStatus(taskId), onProgress, intervalMs);
    },

    // ── Macro Data ──────────────────────────────────────────
    async macroFields(frequency) {
        const q = frequency ? `?frequency=${frequency}` : "";
        return this.request(`/api/macro/fields${q}`);
    },

    async macroDaily(params = {}) {
        const qs = new URLSearchParams();
        if (params.start_date) qs.append("start_date", params.start_date);
        if (params.end_date) qs.append("end_date", params.end_date);
        if (params.fields) qs.append("fields", params.fields.join(","));
        const q = qs.toString();
        return this.request(`/api/macro/daily${q ? "?" + q : ""}`);
    },

    async macroMonthly(params = {}) {
        const qs = new URLSearchParams();
        if (params.start_month) qs.append("start_month", params.start_month);
        if (params.end_month) qs.append("end_month", params.end_month);
        if (params.fields) qs.append("fields", params.fields.join(","));
        const q = qs.toString();
        return this.request(`/api/macro/monthly${q ? "?" + q : ""}`);
    },

    async syncMacro(payload) {
        return this.request("/api/macro/sync", { method: "POST", body: payload });
    },

    async getMacroSyncStatus(taskId) {
        return this.request(`/api/macro/sync/${taskId}`);
    },

    async pollMacroSyncTask(taskId, onProgress, intervalMs = 2000) {
        return this.pollTask(() => this.getMacroSyncStatus(taskId), onProgress, intervalMs);
    },
};

window.API = API;