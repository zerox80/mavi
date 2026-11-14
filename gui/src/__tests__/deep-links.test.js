import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getCurrent, onOpenUrl } from '@tauri-apps/plugin-deep-link';
import { openEnrollment } from '../enrollment.js';
import { showToast } from '../toast.js';
import { isEnrollmentLink, wireDesktopEnrollmentLinks } from '../deep-links.js';

vi.mock('@tauri-apps/plugin-deep-link', () => ({ getCurrent: vi.fn(), onOpenUrl: vi.fn() }));
vi.mock('../enrollment.js', () => ({ openEnrollment: vi.fn() }));
vi.mock('../toast.js', () => ({ showToast: vi.fn() }));

const link = `mavivpn://enroll?panel=https%3A%2F%2Fpanel.example.com&ticket=${'A'.repeat(43)}`;

describe('desktop enrollment links', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    openEnrollment.mockReturnValue(true);
    onOpenUrl.mockResolvedValue(() => {});
    getCurrent.mockResolvedValue(null);
  });

  it('opens the enrollment review entry on application startup', async () => {
    getCurrent.mockResolvedValue([link]);
    await wireDesktopEnrollmentLinks();
    expect(openEnrollment).toHaveBeenCalledExactlyOnceWith(link);
    expect(onOpenUrl.mock.invocationCallOrder[0]).toBeLessThan(
      getCurrent.mock.invocationCallOrder[0]
    );
  });

  it('delivers links to an existing window once without redeeming tickets', async () => {
    await wireDesktopEnrollmentLinks();
    const receive = onOpenUrl.mock.calls[0][0];
    receive([link]);
    receive([link]);
    expect(openEnrollment).toHaveBeenCalledExactlyOnceWith(link);
  });

  it('keeps an in-progress enrollment and permits retrying the second link', async () => {
    openEnrollment.mockReturnValueOnce(false).mockReturnValueOnce(true);
    await wireDesktopEnrollmentLinks();
    const receive = onOpenUrl.mock.calls[0][0];
    receive([link]);
    expect(showToast).toHaveBeenCalled();
    receive([link]);
    expect(openEnrollment).toHaveBeenCalledTimes(2);
  });

  it('ignores unrelated protocols, duplicate parameters and invalid panel addresses', () => {
    for (const candidate of [
      'https://example.com',
      link.replace('enroll?', 'other?'),
      `${link}&ticket=${'B'.repeat(43)}`,
      `${link}&extra=1`,
      `${link}#fragment`,
      link.replace('https%3A', 'http%3A'),
      link.replace('A'.repeat(43), 'short'),
    ])
      expect(isEnrollmentLink(candidate), candidate).toBe(false);
    expect(isEnrollmentLink(link)).toBe(true);
  });
});
