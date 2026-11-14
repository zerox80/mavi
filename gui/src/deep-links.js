import { getCurrent, onOpenUrl } from '@tauri-apps/plugin-deep-link';
import { openEnrollment } from './enrollment.js';
import { showToast } from './toast.js';

export function isEnrollmentLink(value) {
  try {
    const url = new URL(value);
    const params = url.searchParams;
    return (
      url.protocol === 'mavivpn:' &&
      url.hostname === 'enroll' &&
      !url.port &&
      !url.username &&
      !url.password &&
      !url.hash &&
      (!url.pathname || url.pathname === '/') &&
      [...params.keys()].length === 2 &&
      params.getAll('ticket').length === 1 &&
      /^[A-Za-z0-9_-]{43}$/.test(params.get('ticket') || '') &&
      params.getAll('panel').length === 1 &&
      validPanel(params.get('panel'))
    );
  } catch {
    return false;
  }
}

function validPanel(value) {
  const url = new URL(value);
  return (
    url.protocol === 'https:' &&
    !!url.hostname &&
    !url.username &&
    !url.password &&
    !url.search &&
    !url.hash &&
    (url.pathname === '/' || !url.pathname)
  );
}

export async function wireDesktopEnrollmentLinks() {
  // Register before reading startup URLs so a second process cannot lose a link
  // while preferences load. Repeated delivery must not consume a ticket twice.
  const seen = new Set();
  const receive = (urls) => {
    for (const link of urls || []) {
      if (!isEnrollmentLink(link) || seen.has(link)) continue;
      if (openEnrollment(link)) seen.add(link);
      else showToast('Finish the current enrollment, then open this link again.', 'info');
    }
  };
  try {
    await onOpenUrl(receive);
    receive(await getCurrent());
  } catch {
    // A plain browser preview has no desktop plugin; manual enrollment remains available.
    console.warn('Desktop enrollment link integration is unavailable.');
  }
}
