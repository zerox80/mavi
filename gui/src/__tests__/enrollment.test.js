import { beforeEach, describe, expect, it, vi } from 'vitest';
import { invoke } from '../api.js';
import { normalizePrefs } from '../theme.js';
import { renderConnectionList } from '../connections.js';
import { showToast } from '../toast.js';
import { state } from '../state.js';
import {
  closeEnrollment,
  importEnrollment,
  openEnrollment,
  redeemEnrollment,
  shortenPin,
  wireEnrollment,
} from '../enrollment.js';

vi.mock('../api.js', () => ({ invoke: vi.fn() }));
vi.mock('../theme.js', () => ({ normalizePrefs: vi.fn((prefs) => prefs) }));
vi.mock('../connections.js', () => ({ renderConnectionList: vi.fn() }));
vi.mock('../toast.js', () => ({ showToast: vi.fn() }));

const PIN = 'ab'.repeat(32);

function profile(overrides = {}) {
  return {
    schema: 'mavi.connection.v1',
    device_id: 'panel-device-id',
    label: 'Enrolled laptop',
    endpoint: 'vpn.example.com:10443',
    cert_pin: PIN,
    transport: 'http3_censorship_resistant',
    ech_config: '00ff',
    vpn_mtu: 1280,
    keycloak: {
      url: 'https://auth.example.com',
      realm: 'mavi-vpn',
      client_id: 'mavi-client',
    },
    ...overrides,
  };
}

function savedPrefs() {
  return {
    theme: 'light',
    accent: '#2B44FF',
    connections: [{ id: 'new-local-id', label: 'Enrolled laptop' }],
    active_id: 'new-local-id',
    legacy_config_migrated: true,
  };
}

function setupDom() {
  document.body.innerHTML = `
    <button id="enroll-conn-btn"></button>
    <div id="enroll-backdrop">
      <div id="enroll-link-step"></div>
      <div id="enroll-confirm-step" class="hidden"></div>
      <input id="enroll-link" />
      <div id="enroll-label"></div>
      <div id="enroll-endpoint"></div>
      <div id="enroll-keycloak"></div>
      <div id="enroll-realm"></div>
      <div id="enroll-client"></div>
      <div id="enroll-pin"></div>
      <div id="enroll-error" class="hidden"></div>
      <button id="enroll-cancel"></button>
      <button id="enroll-redeem"></button>
      <button id="enroll-import" class="hidden"></button>
    </div>
  `;
}

describe('webpanel enrollment workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDom();
    state.prefsWriteBlocked = false;
    state.prefs = { connections: [], active_id: null };
    closeEnrollment();
    wireEnrollment({ applyHeroForSelection: vi.fn() });
  });

  it('redeems once, shows a shortened safe preview, and imports after confirmation', async () => {
    const redeemed = profile({ label: '<img src=x onerror=alert(1)> Enrolled' });
    invoke.mockResolvedValueOnce(redeemed).mockResolvedValueOnce(savedPrefs());
    openEnrollment();
    document.getElementById('enroll-link').value =
      'https://panel.example.com/enroll/one-time-ticket';

    await redeemEnrollment();

    expect(invoke).toHaveBeenNthCalledWith(1, 'redeem_enrollment', {
      link: 'https://panel.example.com/enroll/one-time-ticket',
    });
    expect(document.getElementById('enroll-link').value).toBe('');
    expect(document.getElementById('enroll-confirm-step').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('enroll-pin').textContent).toBe(shortenPin(PIN));
    expect(document.getElementById('enroll-pin').textContent).not.toContain(PIN);
    expect(document.querySelector('#enroll-label img')).toBeNull();
    expect(document.getElementById('enroll-label').textContent).toContain('<img');

    await importEnrollment();

    expect(invoke).toHaveBeenNthCalledWith(2, 'import_enrollment', { profile: redeemed });
    expect(normalizePrefs).toHaveBeenCalledWith(savedPrefs());
    expect(state.prefs.active_id).toBe('new-local-id');
    expect(renderConnectionList).toHaveBeenCalledOnce();
    expect(showToast).toHaveBeenCalledWith(
      'Enrollment saved. Connect to sign in with Keycloak.',
      'success'
    );
  });

  it('cancelling a redeemed preview neither imports nor reports success', async () => {
    invoke.mockResolvedValueOnce(profile());
    openEnrollment();
    document.getElementById('enroll-link').value = 'mavivpn://enroll?ticket=one-time';
    await redeemEnrollment();

    closeEnrollment();
    await importEnrollment();

    expect(invoke).toHaveBeenCalledOnce();
    expect(showToast).not.toHaveBeenCalled();
    expect(state.prefs.connections).toEqual([]);
  });

  it('shows redemption errors without consuming a second ticket on retry', async () => {
    invoke.mockRejectedValueOnce('Enrollment link is invalid, expired, or already used');
    openEnrollment();
    document.getElementById('enroll-link').value = 'invalid-link';

    await redeemEnrollment();

    expect(document.getElementById('enroll-error').textContent).toContain('invalid, expired');
    expect(document.getElementById('enroll-confirm-step').classList.contains('hidden')).toBe(true);
    expect(showToast).not.toHaveBeenCalled();
  });

  it('keeps the redeemed profile after a save failure and retries persistence only', async () => {
    invoke
      .mockResolvedValueOnce(profile())
      .mockRejectedValueOnce('Disk full')
      .mockResolvedValueOnce(savedPrefs());
    openEnrollment();
    document.getElementById('enroll-link').value = 'one-time-link';
    await redeemEnrollment();

    await importEnrollment();
    expect(document.getElementById('enroll-error').textContent).toContain('Disk full');
    expect(document.getElementById('enroll-error').textContent).toContain(
      'without using the ticket again'
    );
    expect(showToast).not.toHaveBeenCalled();

    await importEnrollment();
    expect(invoke.mock.calls.map(([command]) => command)).toEqual([
      'redeem_enrollment',
      'import_enrollment',
      'import_enrollment',
    ]);
    expect(showToast).toHaveBeenCalledOnce();
  });

  it('does not redeem while saved preferences are unavailable', async () => {
    state.prefsWriteBlocked = true;
    openEnrollment();
    document.getElementById('enroll-link').value = 'one-time-link';

    await redeemEnrollment();

    expect(invoke).not.toHaveBeenCalled();
    expect(document.getElementById('enroll-error').textContent).toContain('Restart the app');
  });
});
