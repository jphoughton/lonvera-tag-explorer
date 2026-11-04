// Example module page used by the mock server (copy of the example in docs/projection/MODULE_ui_shell.md).
export async function render(el, ctx) {
  const { h, ui, charts } = ctx;
  const gridBox = h('div');
  const canvas = h('canvas', { 'aria-label': 'New-customer revenue' });
  el.replaceChildren(h('div', { class: 'pg-page' },
    h('div', { class: 'pg-title' }, h('h1', ctx.module.label), h('span', { class: 'sub' }, ctx.scenarioInfo.name)),
    h('div', { class: 'pg-card' }, h('div', { class: 'pg-chartbox' }, canvas)),
    gridBox));
  const load = () => ctx.api.table(ctx.module.dash);                // GET /api/projection/<module>/table?scenario=<slug>
  const draw = (p) => {
    charts.destroyAll();
    const nc = p.rows.find((r) => r.key === 'nc_rev');
    charts.comboChart(canvas, { months: p.months, status: p.status, series: [{ label: nc.label, values: nc.values, type: 'bar' }] });
  };
  ui.loading(gridBox);
  try {
    const payload = await load();
    draw(payload);
    const grid = ctx.grid(gridBox, {
      payload, title: ctx.module.label,
      onEdit: async (edits) => {                                    // edits = [{line_key, month, value, previous}]
        await ctx.api.saveSeries(edits);                            // batched PUT /api/projection/series
        const fresh = await load(); draw(fresh); return fresh;      // a returned payload re-renders the grid
      },
    });
    return () => grid.destroy();
  } catch (e) { ui.error(gridBox, e, ctx.reload); }
}
