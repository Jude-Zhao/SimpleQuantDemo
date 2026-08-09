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

    // ── Health ─────────────────────────────────────────────
    async health() {
        return this.request("/api/health");
    },

    // ── Factors ────────────────────────────────────────────
    async listFactors() {
        return this.request("/api/factors");
    },

    async computeFactor(payload) {
        return this.request("/api/factors/compute", { method: "POST", body: payload });
    },

    async factorCorrelation({ granularity = "instance", factorIds = [], factorNames = [] } = {}) {
        return this.request("/api/factors/correlation", {
            method: "POST",
            body: { granularity, factor_ids: factorIds, factor_names: factorNames },
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
        return this.request(`/api/universe/${secCode}`, { method: "DELETE" });
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

    // ── Data Sync ──────────────────────────────────────────
    async syncEtf(options = {}) {
        return this.request("/api/market/sync/etf", { method: "POST", body: options });
    },

    async getSyncStatus(taskId) {
        return this.request(`/api/market/sync/${taskId}`);
    },

    /**
     * Poll a sync task until completion or failure.
     * @param {string} taskId
     * @param {function} onProgress - callback with task object
     * @param {number} intervalMs - poll interval
     * @returns {Promise<object>} final task object
     */
    async pollSyncTask(taskId, onProgress, intervalMs = 2000) {
        while (true) {
            const task = await this.getSyncStatus(taskId);
            if (onProgress) onProgress(task);
            if (task.status === "completed" || task.status === "failed") {
                return task;
            }
            await new Promise((r) => setTimeout(r, intervalMs));
        }
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
        while (true) {
            const task = await this.getMacroSyncStatus(taskId);
            if (onProgress) onProgress(task);
            if (task.status === "completed" || task.status === "failed") {
                return task;
            }
            await new Promise((r) => setTimeout(r, intervalMs));
        }
    },
};

window.API = API;