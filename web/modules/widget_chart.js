/* Declarative `chart` component helpers, pure and DOM-free: the Chart.js config
   built from the declaration plus the target data, the accessible data table
   every chart carries, the finite-value coercion, and the dotted-path reader
   (`getPath`) the whole declarative renderer in widgets.js shares with them.
   Moved out of widgets.js unchanged (widgets lifecycle phase 3). */

import { escapeHtmlAttr as escapeHtml } from './utils.js';

export function getPath(root, path, fallback = '') {
    if (!path) return root ?? fallback;
    let current = root;
    for (const part of String(path).split('.').filter(Boolean)) {
        if (current == null || typeof current !== 'object') return fallback;
        current = current[part];
    }
    return current ?? fallback;
}

const CHART_PALETTE = [
    ['#e85d6f', 'rgba(232, 93, 111, 0.22)'],
    ['#60a5fa', 'rgba(96, 165, 250, 0.22)'],
    ['#34d399', 'rgba(52, 211, 153, 0.22)'],
    ['#fbbf24', 'rgba(251, 191, 36, 0.22)'],
];

export function finiteChartValue(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string' || !value.trim()) return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

export function chartConfig(component, data) {
    const type = ['line', 'bar'].includes(component.chart_type) ? component.chart_type : 'line';
    const labels = component.labels || getPath(data, component.labels_path || 'labels', []);
    const datasets = component.datasets || getPath(data, component.datasets_path || 'datasets', []);
    const unit = String(component.unit || '');
    return {
        type,
        data: {
            labels: Array.isArray(labels) ? labels.map((item) => String(item ?? '')) : [],
            datasets: Array.isArray(datasets) ? datasets.map((dataset, idx) => {
                const [borderColor, backgroundColor] = CHART_PALETTE[idx % CHART_PALETTE.length];
                return {
                    label: String(dataset?.label ?? 'Series'),
                    data: Array.isArray(dataset?.data) ? dataset.data.map(finiteChartValue) : [],
                    borderColor,
                    backgroundColor,
                    spanGaps: false,
                };
            }) : [],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            spanGaps: false,
            plugins: { legend: { display: true } },
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.06)' } },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.06)' },
                    title: { display: Boolean(unit), text: unit },
                },
            },
        },
    };
}

export function renderChartDataTable(config, label, expanded) {
    const labels = config.data.labels || [];
    const datasets = config.data.datasets || [];
    const rows = labels.map((item, idx) => `<tr><th scope="row">${escapeHtml(item)}</th>${datasets.map((dataset) => `<td data-label="${escapeHtml(dataset.label)}">${escapeHtml(dataset.data[idx] ?? '—')}</td>`).join('')}</tr>`).join('');
    return `<details class="widget-chart-data"${expanded ? ' open' : ''}><summary>View ${escapeHtml(label)} data</summary><div class="widget-table-wrap"><table class="widget-table"><thead><tr><th>Label</th>${datasets.map((dataset) => `<th>${escapeHtml(dataset.label)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div></details>`;
}
