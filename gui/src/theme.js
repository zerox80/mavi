import { state, $ } from './state.js';
import { invoke } from './api.js';
import { showToast } from './toast.js';

export function wireThemeToggle() {
  $('theme-toggle').addEventListener('click', async () => {
    const previous = state.prefs.theme;
    const next = previous === 'light' ? 'dark' : 'light';
    state.prefs.theme = next;
    applyTheme(next);
    try {
      await savePrefs();
    } catch {
      state.prefs.theme = previous;
      applyTheme(previous);
      showToast('Could not save the theme. Please try again.', 'error');
    }
  });
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const icon = $('theme-icon');
  if (!icon) return;
  if (theme === 'dark') {
    icon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />';
  } else {
    icon.innerHTML =
      '<circle cx="12" cy="12" r="4" />' +
      '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />';
  }
}

export async function savePrefs(prefs = state.prefs) {
  if (state.prefsWriteBlocked) {
    throw new Error('Saved connections could not be loaded. Restart the app to retry.');
  }
  await invoke('save_prefs', { prefs });
}

export function normalizePrefs(prefs = {}) {
  return {
    theme: prefs.theme || 'light',
    accent: prefs.accent || '#2B44FF',
    connections: Array.isArray(prefs.connections) ? prefs.connections : [],
    active_id: prefs.active_id ?? null,
    legacy_config_migrated: !!prefs.legacy_config_migrated,
  };
}
