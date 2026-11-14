import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { daemonHintText, hideToast, showServiceOfflineHint, showToast } from '../toast.js';

function setPlatform(platform) {
  Object.defineProperty(navigator, 'platform', {
    configurable: true,
    value: platform,
  });
}

describe('toast helpers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div id="toast"></div>';
    setPlatform('Linux x86_64');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('shows, auto-hides, and manually hides matching toast kinds', () => {
    showToast('Saved', 'success', 100);

    const toast = document.getElementById('toast');
    expect(toast.className).toBe('visible success');
    expect(toast.textContent).toBe('Saved');
    expect(toast.dataset.kind).toBe('success');

    vi.advanceTimersByTime(100);

    expect(toast.className).toBe('');
    expect(toast.textContent).toBe('');
    expect(toast.dataset.kind).toBe('');
  });

  it('does not hide a toast when the kind does not match', () => {
    showToast('Saved', 'success', 0);

    hideToast('error');

    expect(document.getElementById('toast').textContent).toBe('Saved');
  });

  it('leaves the DOM alone when the toast element is missing', () => {
    document.body.innerHTML = '';

    expect(() => showToast('Missing')).not.toThrow();
    expect(() => hideToast()).not.toThrow();
    expect(() => showServiceOfflineHint()).not.toThrow();
  });

  it('shows Linux and Windows service hints without replacing hard errors', () => {
    showServiceOfflineHint();
    expect(document.getElementById('toast').textContent).toContain('Set up VPN service');
    expect(daemonHintText()).not.toContain('sudo');

    setPlatform('Win32');
    showServiceOfflineHint();
    expect(document.getElementById('toast').innerHTML).toContain('net start MaviVPNService');
    expect(daemonHintText()).toBe('Daemon offline. Run: net start MaviVPNService');

    showToast('Hard failure', 'error', 0);
    showServiceOfflineHint();

    expect(document.getElementById('toast').textContent).toBe('Hard failure');
  });

  it('shows daemon access errors as text instead of suggesting a service restart', () => {
    const error = "permission denied. Join the 'mavivpn' group, then log out and back in. <script>";
    showServiceOfflineHint(error);

    const toast = document.getElementById('toast');
    expect(toast.textContent).toContain('Approve the system prompt');
    expect(toast.textContent).not.toContain('systemctl start');
    expect(toast.querySelector('script')).toBeNull();
    expect(daemonHintText(error)).toContain('Set up VPN service');
  });
});
