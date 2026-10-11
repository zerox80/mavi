export const state = {
  // UI state machine: 'off' | 'connecting' | 'disconnecting' | 'on'
  hero: 'off',
  disconnecting: false,
  connectAttempt: 0,
  connectRequestId: null,
  pendingConnect: false,
  disconnectPending: false,
  // From vpn_status
  serviceAvailable: false,
  running: false,
  activeEndpoint: null,
  sessionConnection: null, // Snapshot of the profile used for this session.
  vpnState: 'Stopped',
  lastError: null,
  // Session wall clock
  sessionStart: null,
  // Startup blocks persistence until load_prefs succeeds.
  prefsWriteBlocked: true,
  // Saved UI prefs
  prefs: {
    theme: 'light',
    accent: '#2B44FF',
    connections: [],
    active_id: null,
    legacy_config_migrated: false,
  },
  // Search filter
  search: '',
};

export const $ = (id) => document.getElementById(id);
