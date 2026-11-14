import { $ } from './state.js';
import { invoke } from './api.js';
import { showToast } from './toast.js';

let attempted = false;
let busy = false;
let refreshStatus;

export function wireServiceSetup({ refresh }) {
  refreshStatus = refresh;
  $('service-setup-btn')?.addEventListener('click', () => setupService());
}

export function updateServiceSetup(status) {
  const section = $('service-setup');
  if (!section || !navigator.platform.toLowerCase().includes('linux')) return;
  section.classList.toggle('hidden', !!status.service_available);
  const error = String(status.last_error || '').toLowerCase();
  if (
    !status.service_available &&
    !attempted &&
    refreshStatus &&
    (error.includes('permission denied') || error.includes('no such file or directory'))
  ) {
    void setupService();
  }
}

async function setupService() {
  if (busy) return;
  attempted = true;
  busy = true;
  const button = $('service-setup-btn');
  if (button) {
    button.disabled = true;
    button.textContent = 'Waiting for system approval…';
  }
  try {
    await invoke('setup_vpn_service');
    await refreshStatus?.();
  } catch (error) {
    showToast(String(error), 'error');
  } finally {
    busy = false;
    if (button) {
      button.disabled = false;
      button.textContent = 'Set up VPN service';
    }
  }
}
