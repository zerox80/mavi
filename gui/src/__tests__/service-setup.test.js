import { beforeEach, expect, it, vi } from 'vitest';

vi.mock('../api.js', () => ({ invoke: vi.fn() }));
vi.mock('../toast.js', () => ({ showToast: vi.fn() }));

let setup;
let invoke;
let showToast;
let refresh;

beforeEach(async () => {
  vi.resetModules();
  ({ invoke } = await import('../api.js'));
  ({ showToast } = await import('../toast.js'));
  invoke.mockReset();
  showToast.mockReset();
  Object.defineProperty(navigator, 'platform', { configurable: true, value: 'Linux x86_64' });
  document.body.innerHTML =
    '<section id="service-setup" class="hidden"><button id="service-setup-btn"></button></section>';
  setup = await import('../service-setup.js');
  refresh = vi.fn();
  setup.wireServiceSetup({ refresh });
});

it('requests one system approval for inaccessible service access and refreshes after setup', async () => {
  let approve;
  invoke.mockReturnValue(
    new Promise((resolve) => {
      approve = resolve;
    })
  );
  const status = { service_available: false, last_error: 'IPC token: permission denied' };
  setup.updateServiceSetup(status);
  setup.updateServiceSetup(status);
  expect(invoke).toHaveBeenCalledExactlyOnceWith('setup_vpn_service');
  expect(document.getElementById('service-setup-btn').disabled).toBe(true);
  approve();
  await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  setup.updateServiceSetup({ service_available: true });
  expect(document.getElementById('service-setup').classList.contains('hidden')).toBe(true);
});

it('does not repeat a cancelled prompt on status polls and allows a manual retry', async () => {
  invoke.mockRejectedValueOnce('System authorization was cancelled');
  const status = { service_available: false, last_error: 'No such file or directory' };
  setup.updateServiceSetup(status);
  await vi.waitFor(() => expect(showToast).toHaveBeenCalled());
  setup.updateServiceSetup(status);
  expect(invoke).toHaveBeenCalledTimes(1);
  invoke.mockResolvedValueOnce(undefined);
  document.getElementById('service-setup-btn').click();
  await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(invoke).toHaveBeenCalledTimes(2);
});

it('does not request administrator approval for transient errors or another platform', () => {
  setup.updateServiceSetup({ service_available: false, last_error: 'Service is busy' });
  expect(invoke).not.toHaveBeenCalled();
  Object.defineProperty(navigator, 'platform', { configurable: true, value: 'Win32' });
  setup.updateServiceSetup({ service_available: false, last_error: 'permission denied' });
  expect(invoke).not.toHaveBeenCalled();
});
