import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { state } from '../state.js';
import { startSessionClock } from '../clock.js';

describe('startSessionClock', () => {
  let netSession;

  beforeEach(() => {
    vi.useFakeTimers();
    state.hero = 'off';
    state.sessionStart = null;
    document.body.innerHTML = '<div id="net-session"></div>';
    netSession = document.getElementById('net-session');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the elapsed session time while connected', () => {
    startSessionClock();

    state.hero = 'on';
    state.sessionStart = Date.now() - 5000;

    // Fake timers advance Date.now() too: 5 seconds elapsed + 1 second tick.
    vi.advanceTimersByTime(1000);

    expect(netSession.textContent).toBe('00:00:06');
  });

  it('formats hours, minutes, and seconds', () => {
    startSessionClock();

    state.hero = 'on';
    state.sessionStart = Date.now() - (3 * 3600 + 25 * 60 + 8) * 1000;

    vi.advanceTimersByTime(1000);

    expect(netSession.textContent).toBe('03:25:09');
  });

  it('resets to 00:00:00 when not connected', () => {
    startSessionClock();

    state.hero = 'off';
    state.sessionStart = Date.now() - 5000;
    netSession.textContent = '12:34:56';

    vi.advanceTimersByTime(1000);

    expect(netSession.textContent).toBe('00:00:00');
  });

  it('does not throw when the session element is not mounted', () => {
    netSession.remove();
    startSessionClock();

    expect(() => vi.advanceTimersByTime(1000)).not.toThrow();
  });
});
