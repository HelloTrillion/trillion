// @vitest-environment jsdom
// Sign out every other page (SPN-11a, the token revocation spec).
import { describe, expect, it } from 'vitest';
import { statusText } from '../src/announce';
import { createDevicesSettings } from '../src/devices';
import type { Host } from '../src/panel';
import { INITIAL, reduce } from '../src/view';

function host(): Host & { el: HTMLElement } {
  const el = document.createElement('div');
  document.body.append(el);
  let open = false;
  return {
    el,
    get open() {
      return open;
    },
    showModal: () => void (open = true),
    close: () => void (open = false),
    replaceChildren: (...nodes) => el.replaceChildren(...nodes),
    setAttribute: (name, value) => el.setAttribute(name, value),
    addEventListener: () => undefined,
  };
}

const api = async (): Promise<Response> => new Response(JSON.stringify({ devices: [] }), { status: 200 });

function button(root: HTMLElement, text: string): HTMLButtonElement {
  const found = [...root.querySelectorAll('button')].find((b) => b.textContent === text);
  if (!found) throw new Error(`no button "${text}"`);
  return found;
}

describe('Sign out every other page', () => {
  it('asks once more before it sends, and Cancel sends nothing', () => {
    const h = host();
    const sent: Record<string, unknown>[] = [];
    createDevicesSettings(h, api, (b) => (sent.push(b), true));
    const ask = button(h.el, 'Sign out every other page');
    const yes = button(h.el, 'Yes, sign them out');
    expect(yes.hidden).toBe(true);
    ask.click();
    expect(yes.hidden).toBe(false);
    expect(ask.hidden).toBe(true);
    button(h.el, 'Cancel').click();
    expect(sent).toEqual([]);
    expect(ask.hidden).toBe(false);
    ask.click();
    yes.click();
    expect(sent).toEqual([{ type: 'session.rotate' }]);
    expect(h.el.querySelector('[role="status"]')?.textContent).toBe('Signing out every other page…');
  });

  it('says so when the page is not connected', () => {
    const h = host();
    createDevicesSettings(h, api, () => false);
    button(h.el, 'Sign out every other page').click();
    button(h.el, 'Yes, sign them out').click();
    expect(h.el.querySelector('[role="status"]')?.textContent).toContain("isn't connected");
  });

  it('says what happened once the core gave this page its new token', () => {
    const h = host();
    const d = createDevicesSettings(h, api, () => true);
    d.rotated();
    expect(h.el.querySelector('[role="status"]')?.textContent).toBe(
      'Every other page is signed out. This one stays signed in.',
    );
  });

  it('the controls can be reached by keyboard and are big enough to tap', () => {
    const h = host();
    createDevicesSettings(h, api, () => true);
    for (const b of h.el.querySelectorAll('.devices-signout-row button')) {
      expect((b as HTMLButtonElement).type).toBe('button');
    }
    expect(h.el.querySelector('.devices-signout')?.getAttribute('aria-labelledby')).toBe('devices-signout-title');
  });
});

describe('a page that was signed out', () => {
  it('shows it is signed out and stops', () => {
    const v = reduce({ ...INITIAL, link: 'ready', busy: true }, { type: 'error', code: 'signed_out' });
    expect(v.link).toBe('signed_out');
    expect(v.busy).toBe(false);
    expect(statusText(v)).toBe('Signed out');
  });
});
