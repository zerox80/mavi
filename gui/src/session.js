import { state } from './state.js';

export function selectedConnection() {
  return state.prefs.connections.find((c) => c.id === state.prefs.active_id) || null;
}

export function rememberSession(conn) {
  // Keep only display data; later profile edits must not change a live session.
  const { id, label, endpoint, http2_framing, http3_framing, censorship_resistant } = conn;
  state.sessionConnection = {
    id, label, endpoint, http2_framing, http3_framing, censorship_resistant,
  };
}

export function displayedConnection() {
  if (state.hero === 'off') return selectedConnection();
  if (state.sessionConnection) return state.sessionConnection;
  // A service session discovered after opening the GUI has no known profile
  // or transport. Do not infer them from an editable saved connection.
  return {
    id: '',
    label: state.activeEndpoint || 'Active VPN session',
    endpoint: state.activeEndpoint || '—',
    transportUnknown: true,
  };
}
