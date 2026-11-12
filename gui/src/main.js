import { state, $ } from './state.js';
import { bootstrapTauri, invoke, listen } from './api.js';
import { wireThemeToggle, applyTheme, normalizePrefs } from './theme.js';
import { wireSidebarSearch, renderConnectionList, migrateLegacyConfig } from './connections.js';
import { wireModal, openModal } from './modal.js';
import {
  wireHero,
  refreshStatus,
  applyStatus,
  toggleConnection,
  applyHeroForSelection,
} from './vpn.js';
import { startSessionClock } from './clock.js';
import { showToast } from './toast.js';

// ============================================================================
// Init
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
  await bootstrapTauri();

  state.prefsWriteBlocked = true;

  wireSidebarSearch({ openModal, applyHeroForSelection });
  wireModal({ applyHeroForSelection });
  wireHero();
  wireThemeToggle();
  wireShortcuts();

  // Only enable writes after the saved profiles and secrets were read successfully.
  try {
    const prefs = await invoke('load_prefs');
    if (!prefs) throw new Error('No preferences returned by backend');
    state.prefs = normalizePrefs(prefs);
    state.prefsWriteBlocked = false;
  } catch (e) {
    console.warn('load_prefs failed:', e);
    showToast('Could not load saved connections. Restart the app to retry.', 'error');
  }

  // One-time migration from the old editable runtime config into saved connections.
  if (!state.prefsWriteBlocked) {
    try {
      const config = await invoke('load_config');
      await migrateLegacyConfig(config);
    } catch (e) {
      console.warn('legacy config migration skipped:', e);
    }
  }

  applyTheme(state.prefs.theme);
  renderConnectionList();

  startSessionClock();

  await refreshStatus();

  try {
    await listen('vpn-status-update', (e) => applyStatus(e.payload));
    await listen('tray-toggle', () => toggleConnection());
    // The Keycloak refresh token expired (per Keycloak's session policy): the
    // backend stopped the tunnel. Prompt the user — the next Connect re-opens
    // the browser login.
    await listen('kc-needs-login', () => {
      showToast('Your session expired — click Connect to log in again.', 'error');
      refreshStatus();
    });
  } catch (e) {
    console.warn('event wiring failed:', e);
  }
});

// ============================================================================
// Shortcuts
// ============================================================================

function wireShortcuts() {
  document.addEventListener('keydown', (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      const searchInput = $('search');
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
    }
    if (e.key === 'Escape') {
      const modal = $('modal-backdrop');
      if (modal && modal.classList.contains('visible')) {
        const cancelBtn = $('modal-cancel');
        if (cancelBtn) cancelBtn.click();
      }
    }
  });
}
