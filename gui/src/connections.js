import { state, $ } from './state.js';
import { savePrefs } from './theme.js';
import { escapeHtml } from './utils.js';
import { showToast } from './toast.js';

let _openModal = null;
let _applyHero = null;

export function wireSidebarSearch({ openModal, applyHeroForSelection }) {
  _openModal = openModal;
  _applyHero = applyHeroForSelection;

  $('search').addEventListener('input', (e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderConnectionList();
  });
  $('add-conn-btn').addEventListener('click', () => _openModal(null));
}

export function renderConnectionList() {
  const list = $('connection-list');
  if (!list) return;
  const q = state.search;
  const filtered = state.prefs.connections.filter(
    (c) => !q || c.label.toLowerCase().includes(q) || c.endpoint.toLowerCase().includes(q)
  );

  if (state.prefs.connections.length === 0) {
    list.innerHTML =
      '<div class="conn-empty">No saved connections yet.<br/>Add one to get started.</div>';
    return;
  }

  list.innerHTML = '';
  const header = document.createElement('div');
  header.className = 'group-header';
  header.textContent = 'Connections';
  list.appendChild(header);

  if (filtered.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'conn-empty';
    empty.textContent = 'No matches.';
    list.appendChild(empty);
    return;
  }

  for (const c of filtered) {
    const row = document.createElement('div');
    row.className = 'conn-row';
    row.dataset.connectionId = c.id;
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.addEventListener('click', () => selectConnection(c.id));
    row.addEventListener('dblclick', () => _openModal(c.id));
    row.addEventListener('keydown', (e) => {
      if (e.target !== row || (e.key !== 'Enter' && e.key !== ' ')) return;
      e.preventDefault();
      selectConnection(c.id);
    });
    row.innerHTML = `
      <div class="main">
        <div class="label">${escapeHtml(c.label)}</div>
        <div class="endpoint">${escapeHtml(c.endpoint)}</div>
      </div>
      <span class="state-dot"></span>
      <button class="icon-btn row-edit" type="button" title="Edit connection" aria-label="Edit connection">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11.2 2.6l2.2 2.2-7.6 7.6-2.9.7.7-2.9 7.6-7.6z" />
        </svg>
      </button>
    `;
    const edit = row.querySelector('.row-edit');
    edit.addEventListener('click', (e) => {
      e.stopPropagation();
      _openModal(c.id);
    });
    edit.addEventListener('dblclick', (e) => e.stopPropagation());
    // Secondary: edit on right-click
    row.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      _openModal(c.id);
    });
    list.appendChild(row);
  }
  updateConnectionListState();
}

export function updateConnectionListState() {
  const rows = $('connection-list')?.querySelectorAll('.conn-row');
  if (!rows) return;

  for (const row of rows) {
    const id = row.dataset.connectionId;
    const isSession = id === state.sessionConnection?.id;
    row.classList.toggle('selected', id === state.prefs.active_id);
    row.classList.toggle('active', isSession && state.hero === 'on');
    row.classList.toggle('connecting', isSession && state.hero === 'connecting');
  }
}

export async function selectConnection(id) {
  const previous = state.prefs.active_id;
  state.prefs.active_id = id;
  try {
    await savePrefs();
  } catch {
    state.prefs.active_id = previous;
    showToast('Could not save the selected connection. Please try again.', 'error');
  }
  updateConnectionListState();
  if (_applyHero) _applyHero();
}

export function generateConnectionId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

export function validMtu(value) {
  const mtu = Number(value);
  return Number.isInteger(mtu) && mtu >= 1280 && mtu <= 1360 ? mtu : null;
}

export function connectionFromLegacyConfig(config, existing = null) {
  const kcAuth = !!config.kc_auth;
  return {
    id: existing?.id || generateConnectionId(),
    label: existing?.label || config.endpoint,
    endpoint: config.endpoint,
    token: kcAuth ? null : config.token || null,
    cert_pin: config.cert_pin || '',
    ech_config: config.ech_config || null,
    censorship_resistant: !!config.censorship_resistant,
    http3_framing: !!config.http3_framing,
    http2_framing: !!config.http2_framing,
    kc_auth: kcAuth || null,
    kc_url: kcAuth ? config.kc_url || null : null,
    kc_realm: kcAuth ? config.kc_realm || null : null,
    kc_client_id: kcAuth ? config.kc_client_id || null : null,
    vpn_mtu: validMtu(config.vpn_mtu),
  };
}

export async function migrateLegacyConfig(config) {
  if (state.prefs.legacy_config_migrated) return;

  const previous = { ...state.prefs, connections: [...state.prefs.connections] };

  state.prefs.legacy_config_migrated = true;
  if (config?.endpoint) {
    const idx = state.prefs.connections.findIndex((c) => c.endpoint === config.endpoint);
    if (idx >= 0) {
      state.prefs.connections[idx] = connectionFromLegacyConfig(
        config,
        state.prefs.connections[idx]
      );
      if (!state.prefs.active_id) state.prefs.active_id = state.prefs.connections[idx].id;
    } else {
      const conn = connectionFromLegacyConfig(config);
      state.prefs.connections.push(conn);
      state.prefs.active_id = conn.id;
    }
  }

  try {
    await savePrefs();
  } catch (error) {
    state.prefs = previous;
    throw error;
  }
}
