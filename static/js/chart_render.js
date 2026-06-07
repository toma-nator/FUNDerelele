/* Shared Chart.js renderer used by both the Charts tab and the Dashboard
   chart-widgets, so chart styling/formatting lives in one place.
   Exposes window.PT.money and window.PT.buildChartConfig(payload). */
(function () {
  const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });
  const TICK = '#5a7a96', GRID = 'rgba(120,160,200,.10)', TITLE = '#9fc0dc';

  function hexA(hex, a) {
    if (!hex || hex[0] !== '#') return hex;
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function buildChartConfig(d) {
    const common = {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        title: { display: !!d.title, text: d.title, color: TITLE, font: { size: 12.5, weight: '600' }, padding: { bottom: 10 } },
        legend: { display: false, labels: { color: TICK, boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (c) => {
              const p = c.parsed;
              const horiz = c.chart.options.indexAxis === 'y';
              const v = (typeof p === 'number') ? p : (horiz ? p.x : p.y);
              return (c.dataset.label ? c.dataset.label + ': ' : '') + money.format(v);
            }
          }
        }
      }
    };

    if (d.type === 'pie' || d.type === 'doughnut') {
      return {
        type: d.type,
        data: { labels: d.labels, datasets: [{ data: d.datasets[0].data, backgroundColor: d.datasets[0].colors, borderColor: '#0c1929', borderWidth: 1 }] },
        options: {
          ...common,
          plugins: {
            ...common.plugins,
            legend: { display: true, position: 'right', labels: { color: TICK, boxWidth: 12, font: { size: 11 } } },
            tooltip: { callbacks: { label: (c) => c.label + ': ' + money.format(c.parsed) } }
          }
        }
      };
    }

    const horiz = (d.type === 'hbar' || d.type === 'divergingBar');
    const stacked = (d.type === 'stackedBar');
    const cjtype = (d.type === 'line') ? 'line' : 'bar';
    const multi = d.datasets.length > 1;

    const datasets = d.datasets.map(s => {
      if (cjtype === 'line') {
        return { label: s.label || '', data: s.data, borderColor: s.color, backgroundColor: hexA(s.color, .14),
                 fill: !!s.fill, tension: .25, pointRadius: 0, borderWidth: 2 };
      }
      return { label: s.label || '', data: s.data, backgroundColor: s.colors || s.color, borderWidth: 0, borderRadius: 3 };
    });

    const moneyAxis = { ticks: { color: TICK, callback: (v) => money.format(v) }, grid: { color: GRID } };
    const catAxis = { ticks: { color: TICK, maxRotation: 0, autoSkip: true }, grid: { display: false } };
    const scales = horiz
      ? { x: { ...moneyAxis }, y: { ...catAxis, grid: { display: false } } }
      : { x: { ...catAxis, stacked }, y: { ...moneyAxis, stacked } };

    return {
      type: cjtype,
      data: { labels: d.labels, datasets },
      options: {
        ...common, indexAxis: horiz ? 'y' : 'x',
        plugins: { ...common.plugins, legend: { display: multi, labels: { color: TICK, boxWidth: 12, font: { size: 11 } } } },
        scales
      }
    };
  }

  window.PT = window.PT || {};
  window.PT.money = money;
  window.PT.buildChartConfig = buildChartConfig;
})();
