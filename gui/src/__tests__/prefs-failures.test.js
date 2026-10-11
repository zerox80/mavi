import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { state } from '../state.js';
import { invoke } from '../api.js';
import { openModal, saveModal, deleteModal, wireModal } from '../modal.js';
import { showToast } from '../toast.js';
import { migrateLegacyConfig, selectConnection } from '../connections.js';

vi.mock('../api.js', () => ({ invoke: vi.fn() }));
vi.mock('../toast.js', () => ({ showToast: vi.fn() }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  document.body.innerHTML = [
    'm_label', 'm_endpoint', 'm_token', 'm_cert_pin', 'm_ech_config', 'm_cr_mode',
    'm_h3_framing', 'm_h2_framing', 'm_kc_auth', 'm_kc_url', 'm_kc_realm',
    'm_kc_client_id', 'm_vpn_mtu',
  ].map(id => `<input id="${id}">`).join('') + [
    'modal-backdrop', 'modal-title', 'm-kc-fields', 'm-token-field',
  ].map(id => `<div id="${id}"></div>`).join('') + [
    'modal-cancel', 'modal-save', 'modal-delete',
  ].map(id => `<button id="${id}"></button>`).join('');
  state.prefs = { theme: 'light', connections: [], active_id: null, legacy_config_migrated: false };
  wireModal({ applyHeroForSelection() {} });
  invoke.mockRejectedValue(new Error('keyring unavailable'));
});

afterEach(() => vi.useRealTimers());

function enterProfile() {
  document.getElementById('m_label').value = 'New profile';
  document.getElementById('m_endpoint').value = 'vpn.example.test:443';
  document.getElementById('m_token').value = 'secret';
  document.getElementById('m_cert_pin').value = 'a'.repeat(64);
}

it('retains unsaved inputs and allows retry without falsely adding a saved profile', async () => {
  openModal(null);
  enterProfile();
  await saveModal();
  expect(state.prefs.connections).toEqual([]);
  expect(state.prefs.active_id).toBeNull();
  expect(document.getElementById('modal-backdrop').classList.contains('visible')).toBe(true);
  expect(document.getElementById('m_token').value).toBe('secret');
  expect(showToast).toHaveBeenLastCalledWith(expect.stringContaining('not been saved'), 'error');
  expect(showToast).not.toHaveBeenCalledWith('Connection saved.', 'success');
  invoke.mockResolvedValueOnce(undefined);
  await saveModal();
  expect(state.prefs.connections).toHaveLength(1);
  expect(document.getElementById('modal-backdrop').classList.contains('visible')).toBe(false);
  expect(showToast).toHaveBeenLastCalledWith('Connection saved.', 'success');
});

it('keeps the original profile and selection after failed edits and deletes', async () => {
  const original = { id: 'saved', label: 'Original', endpoint: 'old.example.test:443',
    token: 'old', cert_pin: 'b'.repeat(64) };
  state.prefs.connections = [original];
  state.prefs.active_id = original.id;
  openModal(original.id);
  enterProfile();
  await saveModal();
  expect(state.prefs.connections).toEqual([original]);
  await deleteModal();
  expect(state.prefs.connections).toEqual([original]);
  expect(state.prefs.active_id).toBe(original.id);
  expect(document.getElementById('modal-backdrop').classList.contains('visible')).toBe(true);
});

it('does not submit duplicate saves while persistence is pending', async () => {
  let complete;
  invoke.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  openModal(null);
  enterProfile();
  const pending = saveModal();
  await saveModal();
  expect(invoke).toHaveBeenCalledTimes(1);
  expect(state.prefs.connections).toEqual([]);
  complete();
  await pending;
  expect(state.prefs.connections).toHaveLength(1);
});

it('rolls back selection and leaves failed legacy migration retryable', async () => {
  state.prefs.active_id = 'original';
  await selectConnection('other');
  expect(state.prefs.active_id).toBe('original');
  await expect(migrateLegacyConfig({ endpoint: 'legacy.example.test:443' })).rejects.toThrow('keyring unavailable');
  expect(state.prefs.legacy_config_migrated).toBe(false);
  expect(state.prefs.connections).toEqual([]);
});
