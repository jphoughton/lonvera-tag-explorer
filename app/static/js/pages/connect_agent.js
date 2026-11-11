// Connect an agent (/connect-agent): a signed-in user makes or resets their own key for the brain endpoint (/mcp) and copies the setup for Claude Code, Claude Desktop or Cursor.
// The key is shown once (the server keeps only a hash). Class names and test ids start with `cn-`.
import { h } from '../util.js';
import { api } from '../api.js';
import { showControls } from '../controls.js';

const PLACEHOLDER = 'YOUR_KEY';

function copyBlock(label, text, testid) {
  const pre = h('pre', { class: 'cn-code', 'data-testid': testid }, text);
  const btn = h('button', { type: 'button', class: 'btn', onclick: async () => {
    try { await navigator.clipboard.writeText(pre.textContent); btn.textContent = 'Copied'; } catch { btn.textContent = 'Select and copy'; }
    setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
  } }, 'Copy');
  return h('div', { class: 'cn-block' }, h('div', { class: 'cn-label' }, label, ' ', btn), pre);
}

function setup(url, key) {
  const k = key || PLACEHOLDER;
  const cc = `claude mcp add --transport http lonvera-brain ${url} --header "Authorization: Bearer ${k}"`;
  const desktop = JSON.stringify({ mcpServers: { 'lonvera-brain': { command: 'npx', args: ['-y', 'mcp-remote', url, '--header', 'Authorization:${LONVERA_BRAIN_AUTH}'], env: { LONVERA_BRAIN_AUTH: 'Bearer ' + k } } } }, null, 2);
  const cursor = JSON.stringify({ mcpServers: { 'lonvera-brain': { url, headers: { Authorization: 'Bearer ' + k } } } }, null, 2);
  return h('div', { class: 'cn-setup' },
    h('h2', null, 'Set up your agent'),
    h('p', { class: 'tl-prose' }, key ? 'These already contain your new key.' : 'Replace YOUR_KEY with your key. Make one above if you do not have it.'),
    copyBlock('Claude Code (run once in a terminal)', cc, 'cn-claude-code'),
    copyBlock('Claude Desktop (add to claude_desktop_config.json, then restart the app)', desktop, 'cn-claude-desktop'),
    copyBlock('Cursor (add to .cursor/mcp.json)', cursor, 'cn-cursor'),
    h('p', { class: 'tl-prose small' }, 'Working inside the repo? The file .mcp.json already points at this endpoint and reads your key from the environment variable LONVERA_BRAIN_KEY.'));
}

export async function connectAgentPage(ctx) {
  showControls(false);
  document.title = 'Connect an agent · Marketing metrics';
  const body = h('div', { class: 'cn-body', 'aria-live': 'polite' });
  ctx.mount(h('div', { class: 'page cn-page', 'data-testid': 'cn-page' },
    h('div', { class: 'pagehead' }, h('h1', null, 'Connect an agent'),
      h('div', { class: 'note-line' }, 'Let an AI assistant read the same data and definitions you see here. It can look things up and run read-only queries. It cannot change anything.')), body));

  const render = (st, fresh) => {
    const keyBox = fresh
      ? h('div', { class: 'cn-fresh', role: 'status', 'data-testid': 'cn-fresh' }, h('strong', null, 'Your new key. It is shown once, so copy it now.'), h('pre', { class: 'cn-code', 'data-testid': 'cn-key' }, fresh.key))
      : h('p', { class: 'tl-prose', 'data-testid': 'cn-status' }, st.has_key ? `You have a key (starts ${st.prefix}...${st.last_used_at ? ', last used ' + String(st.last_used_at).slice(0, 10) : ', not used yet'}). It cannot be shown again.` : 'You do not have a key yet.');
    const btn = h('button', { type: 'button', class: 'btn primary', 'data-testid': 'cn-make', onclick: async () => {
      if (st.has_key && !window.confirm('Make a new key? The old key stops working right away.')) return;
      btn.disabled = true;
      try { const r = await api('/api/brain/key', null, { method: 'POST', body: {} }); if (ctx.alive()) render({ ...st, has_key: true, prefix: r.prefix }, r); }
      catch (e) { btn.disabled = false; body.prepend(h('div', { class: 'errorbox', role: 'alert' }, e && e.message ? e.message : String(e))); }
    } }, st.has_key ? 'Reset my key' : 'Make my key');
    body.replaceChildren(
      h('h2', null, 'Endpoint'), copyBlock('Address', st.endpoint, 'cn-endpoint'),
      h('h2', null, 'Your key'), keyBox, h('div', null, btn),
      !st.sql_configured ? h('p', { class: 'tl-prose small' }, 'Queries are not switched on for this server yet, so the SQL tool will say so. Everything else works.') : null,
      setup(st.endpoint, fresh && fresh.key));
  };
  try {
    const st = await api('/api/brain/key', null, { signal: ctx.signal });
    if (ctx.alive()) render(st, null);
  } catch (e) {
    if (!ctx.alive() || (e && e.name === 'AbortError')) return;
    body.replaceChildren(h('div', { class: 'errorbox', role: 'alert', 'data-testid': 'cn-error' }, e && e.message ? e.message : String(e)));
  }
}
