/* ECharts factory: unified Morandi palette, tooltip, axis styling,
   lazy init, resize handling and theme re-render. */

const Charts = (() => {
    // Morandi palette
    const palettes = {
        light: ["#7a8494", "#9a8a7a", "#7a9a8a", "#a09080", "#8a8a9a", "#8a9a7a"],
        dark: ["#a0a8b8", "#b8a898", "#90b0a0", "#c0b0a0", "#a8a8b8", "#a0b090"],
    };
    const semantic = {
        up: { light: "#6b8a6b", dark: "#7a9a7a" },
        down: { light: "#a06a6a", dark: "#b88080" },
        zero: { light: "#b0b0b0", dark: "#5a5a5c" },
    };

    const registry = new Map(); // el -> { factory, chart, observer }

    function cssVar(name, fallback = "") {
        const val = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return val || fallback;
    }

    function themeName() {
        return Theme.current();
    }

    function palette() {
        return palettes[themeName()] || palettes.light;
    }

    function semanticColor(key) {
        return semantic[key]?.[themeName()] || semantic[key]?.light;
    }

    function baseOption(overrides = {}) {
        const dark = themeName() === "dark";
        const tooltipBg = dark ? "rgba(28,28,30,0.95)" : "rgba(255,255,255,0.95)";
        const tooltipColor = dark ? "#c8c6c2" : "#2c2c2c";

        return {
            color: palette(),
            textStyle: { color: cssVar("--text-secondary", "#6e6e6e") },
            tooltip: {
                trigger: "axis",
                backgroundColor: tooltipBg,
                borderColor: cssVar("--border-subtle", "#e0ded9"),
                textStyle: { color: tooltipColor, fontSize: 12 },
                extraCssText:
                    "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.12);padding:8px 12px;",
                axisPointer: { type: "cross", crossStyle: { color: semanticColor("zero") } },
                ...(overrides.tooltip || {}),
            },
            grid: {
                left: 56,
                right: 24,
                top: 40,
                bottom: 48,
                ...(overrides.grid || {}),
            },
            xAxis: overrides.xAxis
                ? styleAxis(overrides.xAxis)
                : undefined,
            yAxis: overrides.yAxis
                ? styleAxis(overrides.yAxis)
                : undefined,
            legend: overrides.legend
                ? {
                      textStyle: { color: cssVar("--text-secondary", "#6e6e6e") },
                      ...overrides.legend,
                  }
                : undefined,
            series: overrides.series,
            dataZoom: overrides.dataZoom,
            visualMap: overrides.visualMap,
            ...(overrides.extra || {}),
        };
    }

    function styleAxis(axis) {
        const tickColor = cssVar("--chart-tick", "#9e9e9e");
        const lineColor = cssVar("--chart-line", "#d8d5cf");
        const isCategory = axis.type === "category";
        return {
            ...axis,
            axisLine: { lineStyle: { color: lineColor } },
            axisLabel: { color: tickColor, ...(axis.axisLabel || {}) },
            splitLine: isCategory
                ? undefined
                : { lineStyle: { color: lineColor }, ...(axis.splitLine || {}) },
            axisTick: isCategory ? { lineStyle: { color: lineColor } } : undefined,
        };
    }

    /**
     * Render a chart. `factory` may be an ECharts option object or a
     * function (themeName) => option, so it can adapt to theme changes.
     */
    function render(el, factory) {
        if (!el) return null;
        const existing = registry.get(el);
        if (existing?.chart) {
            existing.chart.dispose();
        }

        let intersection = null;
        let chart = null;

        function build() {
            const opt = typeof factory === "function" ? factory(themeName()) : factory;
            chart = echarts.init(el);
            chart.setOption(baseOption(opt));
            return chart;
        }

        function destroy() {
            if (chart) {
                chart.dispose();
                chart = null;
            }
        }

        // Lazy init: only create the chart when the container is visible
        if ("IntersectionObserver" in window) {
            intersection = new IntersectionObserver((entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting && !chart) {
                        build();
                        intersection?.disconnect();
                    }
                });
            }, { rootMargin: "100px" });
            intersection.observe(el);
        } else {
            build();
        }

        // Responsive resize
        let resizeObserver = null;
        if ("ResizeObserver" in window) {
            resizeObserver = new ResizeObserver(() => chart?.resize());
            resizeObserver.observe(el);
        } else {
            window.addEventListener("resize", () => chart?.resize());
        }

        registry.set(el, { factory, chart, intersection, resizeObserver, el });
        return chart;
    }

    function refresh() {
        registry.forEach((entry, el) => {
            const { factory, chart } = entry;
            if (chart) {
                chart.dispose();
            }
            const opt = typeof factory === "function" ? factory(themeName()) : factory;
            const newChart = echarts.init(el);
            newChart.setOption(baseOption(opt));
            entry.chart = newChart;
        });
    }

    function destroy(el) {
        const entry = registry.get(el);
        if (!entry) return;
        entry.chart?.dispose();
        entry.intersection?.disconnect();
        entry.resizeObserver?.disconnect();
        registry.delete(el);
    }

    window.addEventListener("themechange", () => refresh());

    return { render, refresh, destroy, palette, semanticColor, themeName, baseOption };
})();

window.Charts = Charts;
