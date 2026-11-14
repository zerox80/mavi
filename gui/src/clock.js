import { state, $ } from './state.js';

export function startSessionClock() {
  setInterval(() => {
    let s = '00:00:00';
    if (state.sessionStart && state.hero === 'on') {
      const diff = Math.floor((Date.now() - state.sessionStart) / 1000);
      const h = String(Math.floor(diff / 3600)).padStart(2, '0');
      const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
      const sec = String(diff % 60).padStart(2, '0');
      s = `${h}:${m}:${sec}`;
    }
    const netSession = $('net-session');
    if (netSession) netSession.textContent = s;
  }, 1000);
}
