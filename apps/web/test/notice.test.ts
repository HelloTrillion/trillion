// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { linkHost, renderScheduled, type Scheduled } from '../src/notice';
import { INITIAL, httpsLink, reduce, type View } from '../src/view';

const words = (text: string): HTMLElement[] => {
  const p = document.createElement('p');
  p.textContent = text;
  return [p];
};

const headsUp = (over: Partial<Scheduled> = {}): Scheduled => ({
  kind: 'scheduled',
  id: 'n1',
  what: 'heads_up',
  title: 'Meeting soon',
  text: 'Your 10:00 with Sam starts in five minutes.',
  dueMs: 0,
  due: '9:55 am',
  late: false,
  conversationId: null,
  link: 'https://meet.example.com/abc-defg-hij',
  detail: 'Planning with Sam at 10:00 am',
  quiet: null,
  spoken: true,
  ...over,
});

function tauri(invoke: (cmd: string, args?: unknown) => Promise<unknown>): void {
  (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke }, event: { listen: async () => () => {} } };
}

afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  vi.restoreAllMocks();
});

describe('a notice on the page', () => {
  it('shows a Join button with the host beside it, and the detail under her line', () => {
    const node = renderScheduled(headsUp(), words);
    expect(node.querySelector('p')?.textContent).toBe('Your 10:00 with Sam starts in five minutes.');
    const join = node.querySelector('button.notice-join-button') as HTMLButtonElement;
    expect(join.textContent).toBe('Join');
    expect(join.getAttribute('aria-label')).toBe('Join at meet.example.com');
    expect(node.querySelector('.notice-host')?.textContent).toBe('meet.example.com');
    expect(node.querySelector('.notice-detail')?.textContent).toBe('Planning with Sam at 10:00 am');
    expect(node.querySelector('.notice-held')).toBeNull();
  });

  it('opens Join in the user’s browser from the desktop app', () => {
    const invoke = vi.fn(async () => undefined);
    tauri(invoke);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    (renderScheduled(headsUp(), words).querySelector('button.notice-join-button') as HTMLButtonElement).click();
    expect(invoke).toHaveBeenCalledWith('open_external', { url: 'https://meet.example.com/abc-defg-hij' });
    expect(open).not.toHaveBeenCalled();
  });

  it('opens Join in a new tab with no way back to this page otherwise', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    (renderScheduled(headsUp(), words).querySelector('button.notice-join-button') as HTMLButtonElement).click();
    expect(open).toHaveBeenCalledWith('https://meet.example.com/abc-defg-hij', '_blank', 'noopener,noreferrer');
  });

  it('says why she held it', () => {
    const node = renderScheduled(headsUp({ spoken: false, quiet: 'quiet hours' }), words);
    expect(node.querySelector('.notice-held')?.textContent).toBe('Held: quiet hours');
  });

  it('adds nothing to one past its moment', () => {
    const node = renderScheduled(headsUp({ spoken: false, quiet: null, link: null, detail: null }), words);
    expect(node.className).toBe('msg assistant');
    expect(node.children).toHaveLength(1);
  });

  it('shows one with no line by its title and detail', () => {
    const node = renderScheduled(headsUp({ text: null, spoken: false, link: null }), words);
    expect(node.querySelector('.notice')?.textContent).toBe('Meeting soon');
    expect(node.querySelector('.notice-detail')?.textContent).toBe('Planning with Sam at 10:00 am');
  });

  it('keeps a missed reminder’s quiet line as it was', () => {
    const node = renderScheduled(headsUp({ what: 'missed', title: 'Stretch', text: null, link: null, detail: null }), words);
    expect(node.outerHTML).toBe('<p class="notice scheduled-quiet">Missed: Stretch (due 9:55 am)</p>');
  });

  it('never reads a title, detail or reason as markup', () => {
    const evil = '<img src=x onerror="alert(1)">';
    const node = renderScheduled(headsUp({ text: null, title: evil, detail: evil, quiet: evil }), words);
    expect(node.querySelector('img')).toBeNull();
    expect(node.textContent).toContain(evil);
    expect(node.querySelector('.notice-held')?.textContent).toBe(`Held: ${evil}`);
  });

  it('offers no Join for a link that is not https', () => {
    expect(httpsLink('javascript:alert(1)')).toBeNull();
    expect(httpsLink('http://meet.example.com/a')).toBeNull();
    expect(httpsLink('not a url')).toBeNull();
    expect(httpsLink('https://meet.example.com/a')).toBe('https://meet.example.com/a');
    expect(linkHost('https://meet.example.com:8443/a')).toBe('meet.example.com:8443');
  });

  it('takes a heads-up from the server with its link, detail and reason', () => {
    const ready: View = { ...INITIAL, link: 'ready' };
    const msg = {
      type: 'notice',
      code: 'schedule',
      id: 'n9',
      kind: 'heads_up',
      title: 'Meeting soon',
      text: '',
      due_ms: 1,
      due: '9:55 am',
      late: false,
      link: 'javascript:alert(1)',
      spoken: false,
      quiet: 'in a meeting',
      detail: 'Planning at 10:00 am',
    };
    const v = reduce(ready, msg);
    expect(v.entries[0]).toMatchObject({ what: 'heads_up', text: null, link: null, quiet: 'in a meeting', detail: 'Planning at 10:00 am', spoken: false });
    const w = reduce(ready, { ...msg, link: 'https://meet.example.com/x' });
    expect(w.entries[0]).toMatchObject({ link: 'https://meet.example.com/x' });
  });
});
