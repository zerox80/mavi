'use strict';

const byId = (id) => document.getElementById(id);
const pathMatch = window.location.pathname.match(/^\/enroll\/([A-Za-z0-9_-]{43})\/?$/);
const ticket = pathMatch?.[1] || '';
const panel = window.location.origin;
const enrollmentUrl = ticket ? `${panel}/enroll/${ticket}` : '';
let redeemed = false;

function showMessage(text, error = false) {
  const node = byId('enrollment-message');
  node.textContent = text;
  node.classList.toggle('error', error);
  node.hidden = !text;
}

function validTicket() {
  if (ticket) return true;
  showMessage('This enrollment URL is invalid. Request a new link from the device owner.', true);
  for (const id of ['redeem-button', 'download-button', 'copy-enrollment']) byId(id).disabled = true;
  byId('open-in-mavi').removeAttribute('href');
  return false;
}

async function redeem(download) {
  if (!validTicket() || redeemed) return;
  redeemed = true;
  const buttons = [byId('redeem-button'), byId('download-button')];
  buttons.forEach((button) => { button.disabled = true; });
  showMessage('Redeeming the single-use ticket…');
  try {
    const response = await fetch('/api/v1/enrollment/redeem', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket }),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok) throw new Error(result?.error || `Enrollment failed with status ${response.status}`);
    if (!result?.profile || result.profile.schema !== 'mavi.connection.v1') throw new Error('The server returned an invalid profile.');
    const json = `${JSON.stringify(result.profile, null, 2)}\n`;
    if (download) {
      const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `mavi-${result.profile.device_id}.json`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      showMessage('Profile downloaded. This ticket has been consumed.');
    } else {
      byId('profile-output').textContent = json;
      byId('profile-output').hidden = false;
      showMessage('Profile imported below. This ticket has been consumed.');
    }
  } catch (error) {
    showMessage(error.message, true);
  }
}

async function copyLink() {
  if (!validTicket()) return;
  try {
    await navigator.clipboard.writeText(enrollmentUrl);
    byId('copy-enrollment').textContent = 'Copied';
  } catch {
    byId('manual-link').focus();
    byId('manual-link').select();
    showMessage('Select and copy the displayed enrollment link.');
  }
}

if (ticket) {
  byId('manual-link').value = enrollmentUrl;
  const deepLink = new URL('mavivpn://enroll');
  deepLink.searchParams.set('ticket', ticket);
  deepLink.searchParams.set('panel', panel);
  byId('open-in-mavi').href = deepLink.href;
}

byId('redeem-button').addEventListener('click', () => redeem(false));
byId('download-button').addEventListener('click', () => redeem(true));
byId('copy-enrollment').addEventListener('click', copyLink);
validTicket();
