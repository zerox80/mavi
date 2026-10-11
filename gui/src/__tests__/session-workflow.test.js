import { beforeEach, expect, it, vi } from 'vitest';
import { state } from '../state.js';
import { applyStatus, applyHeroForSelection, connect } from '../vpn.js';
import { selectConnection, wireSidebarSearch } from '../connections.js';
import { invoke } from '../api.js';

vi.mock('../api.js', () => ({ invoke: vi.fn() }));
const connected = { service_available: true, running: true, state: 'Connected',
  endpoint: 'a.example:443', assigned_ip: '10.8.0.2' };

beforeEach(() => {
  vi.resetAllMocks();
  state.prefsWriteBlocked = false;
  document.body.innerHTML = ['connect-btn', 'ip-readout', 'hero-title',
    'hero-subtitle', 'hero-node-id', 'hero-lat', 'title-state-label',
    'hero-status', 'core-label', 'net-node', 'net-endpoint', 'net-ip',
    'net-service', 'net-protocol', 'net-transport', 'connection-list', 'search',
    'add-conn-btn', 'toast'].map(id => `<div id="${id}"></div>`).join('');
  Object.assign(state, { hero: 'off', running: false, serviceAvailable: true,
    vpnState: 'Stopped', disconnecting: false, connectAttempt: 0,
    pendingConnect: false, disconnectPending: false, connectRequestId: null,
    activeEndpoint: null, sessionConnection: null, search: '' });
  state.prefs.active_id = 'a';
  state.prefs.connections = [
    { id: 'a', label: 'Server A', endpoint: 'a.example:443', token: 'secret', cert_pin: 'pin' },
    { id: 'b', label: 'Server B', endpoint: 'b.example:443', http2_framing: true },
  ];
  wireSidebarSearch({ openModal() {}, applyHeroForSelection });
  invoke.mockImplementation(async command => command === 'vpn_status' ? connected : undefined);
});

it('keeps the active server and transport when another profile is selected', async () => {
  await connect();
  await selectConnection('b');
  applyStatus(connected);
  expect(document.getElementById('hero-title').textContent).toBe('Server A');
  expect(document.getElementById('net-endpoint').textContent).toBe('a.example:443');
  expect(document.getElementById('net-transport').textContent).toBe('RAW QUIC');
  const rows = document.querySelectorAll('.conn-row');
  expect(rows[0].classList.contains('active')).toBe(true);
  expect(rows[1].classList.contains('selected')).toBe(true);
  expect(rows[1].classList.contains('active')).toBe(false);
  expect(invoke.mock.calls.filter(([cmd]) => cmd === 'vpn_connect')).toHaveLength(1);
});

it('retains a session snapshot when its saved profile is edited or deleted', async () => {
  await connect();
  state.prefs.connections[0].endpoint = 'changed.example:443';
  state.prefs.connections[0].http2_framing = true;
  state.prefs.connections = [];
  applyStatus(connected);
  expect(document.getElementById('hero-title').textContent).toBe('Server A');
  expect(document.getElementById('net-transport').textContent).toBe('RAW QUIC');
  expect(document.getElementById('connect-btn').disabled).toBe(false);
});

it('shows selection again after stopping and does not reuse stale session data', async () => {
  await connect();
  await selectConnection('b');
  applyStatus({ service_available: true, running: false, state: 'Stopped' });
  expect(state.sessionConnection).toBeNull();
  expect(document.getElementById('hero-title').textContent).toBe('Server B');
  expect(document.getElementById('net-transport').textContent).toBe('HTTP/2 CONNECT-IP');
});

it('does not invent a profile or transport for an already running service session', () => {
  state.prefs.active_id = 'b';
  applyStatus(connected);
  expect(document.getElementById('hero-title').textContent).toBe('a.example:443');
  expect(document.getElementById('net-transport').textContent).toBe('—');
  expect(document.querySelector('.conn-row.active')).toBeNull();
});

it('discards a local snapshot if another client changes the service endpoint', async () => {
  await connect();
  applyStatus({ ...connected, endpoint: 'external.example:443' });
  expect(document.getElementById('hero-title').textContent).toBe('external.example:443');
  expect(document.getElementById('net-transport').textContent).toBe('—');
});

it('keeps the pending login attached to its original profile', async () => {
  let finish;
  invoke.mockImplementation(async command => {
    if (command === 'vpn_connect') return new Promise(resolve => { finish = resolve; });
    if (command === 'vpn_status') return connected;
  });
  const pending = connect();
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
  await selectConnection('b');
  applyStatus({ service_available: true, state: 'Stopped' });
  expect(document.getElementById('hero-title').textContent).toBe('Server A');
  expect(document.querySelector('.conn-row.connecting').textContent).toContain('Server A');
  finish();
  await pending;
});
