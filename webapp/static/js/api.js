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

    async factorCorrelation(factorNames) {
        return this.request("/api/factors/correlation", {
            method: "POST",
            body: { factor_names: factorNames },
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

    async getAvailableEtfs() {
        return this.request("/api/universe/available");
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

    // ── Market / Dashboard ─────────────────────────────────
    async getMarketPrice(codes, start, end) {
        const params = new URLSearchParams({ codes: codes.join(",") });
        if (start) params.append("start", start);
        if (end) params.append("end", end);
        return this.request(`/api/market/price?${params.toString()}`);
    },
};

window.API = API;