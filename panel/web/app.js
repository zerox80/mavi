'use strict';

let csrfToken = '';
let qrObjectUrl = null;

const element = (id) => document.getElementById(id);
const visible = (id, show) => { element(id).hidden = !show; };

function message(id, text, error = false) {
  const node = element(id);
  node.textContent = text;
  node.classList.toggle('error', error);
  node.hidden = !text;
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');
  if (options.body) headers.set('Content-Type', 'application/json');
  if (options.method && options.method !== 'GET') headers.set('x-mavi-csrf', csrfToken);
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(data?.error || `Request failed with status ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function loadServer() {
  try {
    const server = await request('/api/v1/server');
    element('server-name').textContent = server.name;
    element('server-endpoint').textContent = server.endpoint;
    element('server-transport').textContent = server.transport;
    element('server-mtu').textContent = String(server.vpn_mtu);
    element('server-realm').textContent = server.keycloak.realm;
    element('server-client').textContent = server.keycloak.client_id;
    element('server-keycloak').textContent = server.keycloak.url;
    element('server-pin').textContent = server.cert_pin;
    element('server-ech').textContent = server.ech_config || 'Not configured';
  } catch (error) {
    message('global-message', 'Server configuration is unavailable.', true);
  }
}

async function initialize() {
  await loadServer();
  try {
    const session = await request('/api/v1/session');
    csrfToken = session.csrf_token;
    element('account-name').textContent = `${session.display_name} (${session.roles.join(', ')})`;
    visible('account-area', true);
    visible('login-view', false);
    visible('dashboard-view', true);
    await loadDevices();
  } catch (error) {
    csrfToken = '';
    visible('account-area', false);
    visible('dashboard-view', false);
    visible('login-view', true);
    if (error.status !== 401) message('global-message', error.message, true);
  }
}

async function loadDevices() {
  try {
    const result = await request('/api/v1/devices');
    renderDevices(result.devices);
    message('device-message', '');
  } catch (error) {
    message('device-message', error.message, true);
  }
}

function renderDevices(devices) {
  const list = element('device-list');
  list.replaceChildren();
  visible('empty-devices', devices.length === 0);
  for (const device of devices) {
    const card = document.createElement('article');
    card.className = 'device-item';
    const content = document.createElement('div');
    const heading = document.createElement('h3');
    heading.textContent = device.label;
    const details = document.createElement('p');
    details.textContent = `${device.platform} · ${device.owner_username} · Added ${new Date(device.created_at).toLocaleString()}`;
    const status = document.createElement('span');
    const normalizedStatus = device.status === 'imported' ? 'imported' : 'pending';
    status.className = `status ${normalizedStatus}`;
    status.textContent = normalizedStatus;
    content.append(heading, details, status);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button danger small';
    remove.textContent = 'Delete';
    remove.addEventListener('click', () => deleteDevice(device));
    card.append(content, remove);
    list.append(card);
  }
}

async function deleteDevice(device) {
  const warning = `Delete ${device.label} from the panel? This does not revoke VPN access or disconnect VPN sessions.`;
  if (!window.confirm(warning)) return;
  try {
    await request(`/api/v1/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE' });
    message('device-message', 'Panel inventory deleted. VPN access was not revoked.');
    await loadDevices();
  } catch (error) {
    message('device-message', error.message, true);
  }
}

async function createDevice(event) {
  event.preventDefault();
  const button = element('create-button');
  button.disabled = true;
  message('device-message', '');
  try {
    const device = await request('/api/v1/devices', {
      method: 'POST',
      body: JSON.stringify({
        label: element('device-label').value,
        platform: element('device-platform').value,
      }),
    });
    showEnrollment(device);
    element('device-form').reset();
    message('device-message', 'Enrollment created. The link expires in 15 minutes.');
    await loadDevices();
  } catch (error) {
    message('device-message', error.message, true);
  } finally {
    button.disabled = false;
  }
}

function showEnrollment(device) {
  if (qrObjectUrl) URL.revokeObjectURL(qrObjectUrl);
  qrObjectUrl = URL.createObjectURL(new Blob([device.qr_svg], { type: 'image/svg+xml' }));
  element('qr-image').src = qrObjectUrl;
  element('enrollment-link').value = device.enrollment_url;
  const deepLink = new URL(device.deep_link);
  if (deepLink.protocol !== 'mavivpn:' || deepLink.host !== 'enroll') throw new Error('Invalid Mavi link');
  element('open-mavi').href = deepLink.href;
  visible('enrollment-result', true);
  element('enrollment-result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function copyEnrollmentLink() {
  const input = element('enrollment-link');
  try {
    await navigator.clipboard.writeText(input.value);
    element('copy-button').textContent = 'Copied';
  } catch {
    input.focus();
    input.select();
    message('device-message', 'Select and copy the displayed enrollment link.');
  }
}

async function logout() {
  try {
    await request('/auth/logout', { method: 'POST' });
  } catch (error) {
    message('global-message', error.message, true);
    return;
  }
  csrfToken = '';
  visible('account-area', false);
  visible('dashboard-view', false);
  visible('enrollment-result', false);
  visible('login-view', true);
}

element('device-form').addEventListener('submit', createDevice);
element('refresh-button').addEventListener('click', loadDevices);
element('copy-button').addEventListener('click', copyEnrollmentLink);
element('logout-button').addEventListener('click', logout);
initialize();
