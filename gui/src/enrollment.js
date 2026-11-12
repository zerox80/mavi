import { state, $ } from './state.js';
import { invoke } from './api.js';
import { normalizePrefs } from './theme.js';
import { renderConnectionList } from './connections.js';
import { showToast } from './toast.js';

let _applyHero = null;
let _pendingProfile = null;
let _busy = false;

export function shortenPin(pin) {
  return String(pin || '')
    .split(',')
    .map((part) => {
      const value = part.trim();
      return value.length > 20 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
    })
    .join(', ');
}

function errorText(error) {
  if (typeof error === 'string') return error;
  if (typeof error?.message === 'string') return error.message;
  return 'Unknown enrollment error.';
}

function showError(message) {
  const element = $('enroll-error');
  element.textContent = message;
  element.classList.toggle('hidden', !message);
}

function updateView() {
  const reviewing = !!_pendingProfile;
  $('enroll-link-step').classList.toggle('hidden', reviewing);
  $('enroll-confirm-step').classList.toggle('hidden', !reviewing);
  $('enroll-redeem').classList.toggle('hidden', reviewing);
  $('enroll-import').classList.toggle('hidden', !reviewing);
  $('enroll-cancel').disabled = _busy;
  $('enroll-redeem').disabled = _busy;
  $('enroll-import').disabled = _busy;
  $('enroll-redeem').textContent = _busy && !reviewing ? 'REDEEMING…' : 'REVIEW';
  $('enroll-import').textContent = _busy && reviewing ? 'SAVING…' : 'SAVE CONNECTION';
}

export function openEnrollment() {
  if (_busy) return;
  _pendingProfile = null;
  $('enroll-link').value = '';
  showError('');
  updateView();
  $('enroll-backdrop').classList.add('visible');
  setTimeout(() => $('enroll-link').focus(), 0);
}

export function closeEnrollment() {
  if (_busy) return;
  $('enroll-backdrop').classList.remove('visible');
  $('enroll-link').value = '';
  _pendingProfile = null;
  showError('');
  updateView();
}

function showPreview(profile) {
  $('enroll-label').textContent = profile.label.trim();
  $('enroll-endpoint').textContent = profile.endpoint;
  $('enroll-keycloak').textContent = profile.keycloak.url;
  $('enroll-realm').textContent = profile.keycloak.realm;
  $('enroll-client').textContent = profile.keycloak.client_id;
  $('enroll-pin').textContent = shortenPin(profile.cert_pin);
}

export async function redeemEnrollment() {
  if (_busy || _pendingProfile) return;
  if (state.prefsWriteBlocked) {
    showError('Saved connections are not available. Restart the app before enrolling.');
    return;
  }
  const link = $('enroll-link').value;
  if (!link.trim()) {
    showError('Enter an enrollment link.');
    return;
  }

  _busy = true;
  showError('');
  updateView();
  try {
    const profile = await invoke('redeem_enrollment', { link });
    _pendingProfile = profile;
    $('enroll-link').value = '';
    showPreview(profile);
  } catch (error) {
    showError(errorText(error));
  } finally {
    _busy = false;
    updateView();
  }
}

export async function importEnrollment() {
  if (_busy || !_pendingProfile) return;
  _busy = true;
  showError('');
  updateView();
  let imported = false;
  try {
    // Saving uses the already redeemed profile. A failed disk/keyring write can
    // be retried here without consuming the one-time ticket again.
    const prefs = await invoke('import_enrollment', { profile: _pendingProfile });
    state.prefs = normalizePrefs(prefs);
    imported = true;
  } catch (error) {
    showError(
      `Could not save the enrolled profile: ${errorText(error)} You can retry without using the ticket again.`
    );
  } finally {
    _busy = false;
    updateView();
  }
  if (!imported) return;

  closeEnrollment();
  renderConnectionList();
  if (_applyHero) _applyHero();
  showToast('Enrollment saved. Connect to sign in with Keycloak.', 'success');
}

export function wireEnrollment({ applyHeroForSelection }) {
  _applyHero = applyHeroForSelection;
  const button = $('enroll-conn-btn');
  if (!button) return;
  button.addEventListener('click', openEnrollment);
  $('enroll-cancel').addEventListener('click', closeEnrollment);
  $('enroll-redeem').addEventListener('click', redeemEnrollment);
  $('enroll-import').addEventListener('click', importEnrollment);
  $('enroll-backdrop').addEventListener('click', (event) => {
    if (event.target.id === 'enroll-backdrop') closeEnrollment();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('enroll-backdrop')?.classList.contains('visible')) {
      closeEnrollment();
    }
  });
}
