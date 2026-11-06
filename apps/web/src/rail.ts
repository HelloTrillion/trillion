/**
 * The rail (the your helpers spec, The rail): a slim column of round pictures down the
 * sidebar's left edge. Her own picture first, then one per helper the user made, then +.
 * Pictures only: each name is on hover and in its accessible name.
 *
 * A tap opens that one's chat; hers goes back to her last conversation. The picture whose
 * chat is showing is ringed. A right-click, a long press, or Shift+F10 opens a small menu:
 * a helper's picture, Pause or Resume, its details in the panel, Delete. Hers opens the
 * panel, where her routines, timed asks and watchers are. Only one menu or popover is
 * open at a time, and Escape closes it, back on the picture that opened it.
 */

import {
  type Helper,
  type HelperList,
  deleteQuestion,
  helperAction,
  newHelperForm,
  openHelperChat,
  ownPath,
  ownToggleWords,
  statusLine,
} from './helpers';
import { icon } from './icons';
import { type Look, type Picker, createPicker, drawPicture, lookOf } from './look';
import type { Api } from './settings';

export interface RailDeps {
  readonly api: Api;
  /** Say something through the page's one polite region (`#announce`). */
  readonly announce: (text: string) => void;
  /** Open a conversation, the way History opens one. */
  readonly open: (conversationId: string) => void;
  /** Back to her: her last conversation that is not a helper's, or a new one. */
  readonly her: () => void;
  /** + New helper: ask her for one in these words. */
  readonly newHelper: (text: string) => void;
  /** Open the panel at one helper's details. */
  readonly details: (helperId: string) => void;
  /** Open the panel: her routines, timed asks and watchers. */
  readonly panel: () => void;
  /** A list a route answered with: drawn everywhere at once. */
  readonly update: (list: HelperList) => void;
  readonly now: () => number;
  readonly capName: () => string | null;
}

export interface Rail {
  readonly element: HTMLElement;
  /** A list from the core: the user's own helpers are drawn, in its order. */
  update(list: HelperList): void;
  /** The conversation showing: its picture is ringed. */
  select(conversationId: string | null): void;
  /** Her name, which the user can change. */
  name(agentName: string): void;
  /** Close a menu or popover that is open. */
  close(): void;
}

/** How long a press is held before it is a long press. */
export const LONG_PRESS_MS = 500;

/** A helper's picture's name: "Inbox Monitor, needs you". At rest, its name alone. */
export function railLabel(h: Helper, now: number, capName: string | null = null): string {
  if (h.state === 'needs_you') return `${h.name}, needs you`;
  if (h.state === 'working') return `${h.name}, working`;
  if (h.state === 'paused') return `${h.name}, paused`;
  if (h.state === 'capped') return `${h.name}, ${statusLine(h, now, capName)}`;
  return h.name;
}

/** Her picture's name: hers, and how many of her routines and watchers need you. */
export function herLabel(agentName: string, needsYou: number): string {
  if (needsYou <= 0) return agentName;
  return `${agentName}, ${needsYou} ${needsYou === 1 ? 'needs' : 'need'} you in Routines and watchers`;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(cls: string, text?: string): HTMLButtonElement {
  const b = el('button', cls, text);
  b.type = 'button';
  return b;
}

interface Item {
  readonly button: HTMLButtonElement;
  readonly pic: HTMLElement;
  readonly dot: HTMLElement;
  look: string;
  helper: Helper;
}

export function createRail(element: HTMLElement, d: RailDeps): Rail {
  element.classList.add('rail');
  if (!element.hasAttribute('aria-label')) element.setAttribute('aria-label', 'Helpers');

  // Her picture: a small orb, as she is on the page.
  const herBtn = button('rail-pic rail-her');
  const herPic = el('span', 'rail-orb');
  herPic.setAttribute('aria-hidden', 'true');
  const herDot = el('span', 'rail-dot');
  herDot.setAttribute('aria-hidden', 'true');
  herBtn.append(herPic, herDot);

  const list = el('div', 'rail-list');
  const addBtn = button('rail-pic rail-add');
  addBtn.append(icon('plus', 16));
  addBtn.setAttribute('aria-label', 'New helper');
  addBtn.title = 'New helper';
  addBtn.setAttribute('aria-haspopup', 'dialog');
  addBtn.setAttribute('aria-expanded', 'false');
  element.replaceChildren(herBtn, list, addBtn);

  const items = new Map<string, Item>();
  let current: HelperList = { helpers: [], needs_you: 0 };
  let showing: string | null = null;
  let agentName = 'Trillion';
  // Whichever button takes Tab into the rail: one tab stop, arrows move along it.
  let stop: HTMLButtonElement = herBtn;

  // ── Drawing ──
  function drawItem(it: Item): void {
    const h = it.helper;
    it.button.dataset['state'] = h.state;
    it.dot.dataset['state'] = h.state;
    const look = lookOf(h.icon, h.color);
    const key = `${look.icon}|${look.color}`;
    if (it.look !== key) {
      it.look = key;
      drawPicture(it.pic, look, 28);
    }
    const label = railLabel(h, d.now(), d.capName());
    if (it.button.getAttribute('aria-label') !== label) {
      it.button.setAttribute('aria-label', label);
      it.button.title = label;
    }
  }

  function makeItem(h: Helper): Item {
    const b = button('rail-pic');
    b.dataset['id'] = h.id;
    const pic = el('span');
    pic.setAttribute('aria-hidden', 'true');
    const dot = el('span', 'rail-dot');
    dot.setAttribute('aria-hidden', 'true');
    b.append(pic, dot);
    const it: Item = { button: b, pic, dot, look: '', helper: h };
    b.addEventListener('click', () => {
      if (swallowClick()) return;
      closePop(false);
      void openHelperChat(d.api, d.open, it.helper.id, it.helper.conversation_id).then((wrong) => {
        if (wrong) d.announce(wrong);
      });
    });
    menuTriggers(b, () => helperMenu(it));
    return it;
  }

  function drawHer(): void {
    const needs = current.helpers.filter((h) => h.kind !== 'own' && h.state === 'needs_you').length;
    const label = herLabel(agentName, needs);
    if (herBtn.getAttribute('aria-label') !== label) {
      herBtn.setAttribute('aria-label', label);
      herBtn.title = label;
    }
    herDot.dataset['state'] = needs > 0 ? 'needs_you' : 'idle';
  }

  /** Ring the picture whose chat is showing; hers when it is no helper's. */
  function drawSelected(): void {
    let any = false;
    for (const it of items.values()) {
      const on = showing !== null && it.helper.conversation_id === showing;
      any ||= on;
      setCurrent(it.button, on);
    }
    setCurrent(herBtn, !any);
  }

  function setCurrent(b: HTMLButtonElement, on: boolean): void {
    if (on) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  }

  function buttons(): HTMLButtonElement[] {
    return [herBtn, ...[...items.values()].map((it) => it.button), addBtn];
  }

  function drawStops(): void {
    if (!stop.isConnected || !element.contains(stop)) stop = herBtn;
    for (const b of buttons()) b.tabIndex = b === stop ? 0 : -1;
  }

  function update(next: HelperList): void {
    current = next;
    const own = next.helpers.filter((h) => h.kind === 'own');
    const seen = new Set<string>();
    for (const h of own) {
      seen.add(h.id);
      let it = items.get(h.id);
      if (!it) {
        it = makeItem(h);
        items.set(h.id, it);
      }
      it.helper = h;
      drawItem(it);
    }
    for (const [id, it] of items) {
      if (seen.has(id)) continue;
      // Gone: a menu open on it goes too, and focus on it moves to her.
      if (pop?.anchor === it.button) closePop(false);
      const had = document.activeElement === it.button;
      it.button.remove();
      items.delete(id);
      if (had) herBtn.focus();
    }
    // In place: a picture already where it belongs is never moved.
    own.forEach((h, i) => {
      const b = items.get(h.id)?.button;
      const there = list.children[i] ?? null;
      if (b && there !== b) list.insertBefore(b, there);
    });
    drawHer();
    drawSelected();
    drawStops();
  }

  // ── Arrows along the rail ──
  element.addEventListener('focusin', (ev) => {
    if (ev.target instanceof HTMLButtonElement && buttons().includes(ev.target)) {
      stop = ev.target;
      drawStops();
    }
  });
  element.addEventListener('keydown', (ev) => {
    const all = buttons();
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    let to: number;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowRight') to = Math.min(all.length - 1, at + 1);
    else if (ev.key === 'ArrowUp' || ev.key === 'ArrowLeft') to = Math.max(0, at - 1);
    else if (ev.key === 'Home') to = 0;
    else if (ev.key === 'End') to = all.length - 1;
    else return;
    ev.preventDefault();
    all[to]?.focus();
  });

  // ── Opening a menu: right-click, long press, Shift+F10 or the menu key ──
  let longPressed = false;
  /** A long press opened a menu: the click that ends it opens nothing. */
  function swallowClick(): boolean {
    const was = longPressed;
    longPressed = false;
    return was;
  }

  function menuTriggers(b: HTMLButtonElement, open: () => void): void {
    b.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();
      if (pop?.anchor !== b) open();
    });
    b.addEventListener('keydown', (ev) => {
      if (ev.key === 'ContextMenu' || (ev.key === 'F10' && ev.shiftKey)) {
        ev.preventDefault();
        open();
      }
    });
    let timer: ReturnType<typeof setTimeout> | null = null;
    let from = { x: 0, y: 0 };
    const cancel = (): void => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    };
    b.addEventListener('pointerdown', (ev) => {
      longPressed = false;
      if (ev.pointerType === 'mouse') return;
      from = { x: ev.clientX, y: ev.clientY };
      cancel();
      timer = setTimeout(() => {
        timer = null;
        longPressed = true;
        if (pop?.anchor !== b) open();
      }, LONG_PRESS_MS);
    });
    b.addEventListener('pointermove', (ev) => {
      if (timer !== null && Math.hypot(ev.clientX - from.x, ev.clientY - from.y) > 10) cancel();
    });
    b.addEventListener('pointerup', cancel);
    b.addEventListener('pointercancel', cancel);
  }

  // ── One popover at a time, beside its picture ──
  let pop: { readonly anchor: HTMLButtonElement; readonly box: HTMLElement; readonly done: () => void } | null = null;

  function closePop(refocus: boolean): void {
    if (!pop) return;
    const { anchor, done } = pop;
    pop = null;
    done();
    if (refocus && anchor.isConnected) anchor.focus();
  }

  /**
   * Show `box` beside `anchor`: to its right, or below it when there is no room (the
   * phone's strip). On the page itself, so the sidebar's glass does not clip it.
   */
  function openPop(anchor: HTMLButtonElement, box: HTMLElement, expanded = false): void {
    closePop(false);
    box.classList.add('rail-pop');
    document.body.append(box);
    const a = anchor.getBoundingClientRect();
    const w = box.offsetWidth;
    const h = box.offsetHeight;
    const right = a.right + 8 + w <= innerWidth - 8;
    let left = right ? a.right + 8 : a.left;
    let top = right ? a.top : a.bottom + 8;
    left = Math.max(8, Math.min(left, innerWidth - w - 8));
    top = Math.max(8, Math.min(top, innerHeight - h - 8));
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    if (expanded) anchor.setAttribute('aria-expanded', 'true');
    const outside = (ev: Event): void => {
      if (ev.target instanceof Node && !box.contains(ev.target) && !anchor.contains(ev.target)) closePop(false);
    };
    // Placed at its picture, it would drift off it as the rail scrolls: it closes instead.
    const scrolled = (): void => closePop(false);
    const escape = (ev: KeyboardEvent): void => {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      closePop(true);
    };
    document.addEventListener('pointerdown', outside, true);
    element.addEventListener('scroll', scrolled, { passive: true });
    box.addEventListener('keydown', escape);
    pop = {
      anchor,
      box,
      done: () => {
        document.removeEventListener('pointerdown', outside, true);
        element.removeEventListener('scroll', scrolled);
        box.removeEventListener('keydown', escape);
        if (expanded) anchor.setAttribute('aria-expanded', 'false');
        box.remove();
      },
    };
  }

  /** A menu of plain items: arrows move, Escape or Tab close it, back on its picture. */
  function openMenu(anchor: HTMLButtonElement, label: string, entries: readonly (readonly [string, () => void])[]): void {
    const box = el('div', 'rail-menu');
    box.setAttribute('role', 'menu');
    box.setAttribute('aria-label', label);
    const menuItems = entries.map(([text, run]) => {
      const b = button('rail-menu-item', text);
      b.setAttribute('role', 'menuitem');
      b.tabIndex = -1;
      b.addEventListener('click', run);
      return b;
    });
    box.append(...menuItems);
    box.addEventListener('keydown', (ev) => {
      const at = menuItems.indexOf(document.activeElement as HTMLButtonElement);
      const n = menuItems.length;
      if (ev.key === 'ArrowDown') menuItems[(at + 1) % n]?.focus();
      else if (ev.key === 'ArrowUp') menuItems[(at + n - 1) % n]?.focus();
      else if (ev.key === 'Home') menuItems[0]?.focus();
      else if (ev.key === 'End') menuItems[n - 1]?.focus();
      else if (ev.key === 'Tab') closePop(true);
      else return;
      ev.preventDefault();
    });
    openPop(anchor, box);
    menuItems[0]?.focus();
  }

  function herMenu(): void {
    openMenu(herBtn, agentName, [
      [
        'Routines and watchers',
        () => {
          closePop(false);
          d.panel();
        },
      ],
    ]);
  }

  function helperMenu(it: Item): void {
    const h = it.helper;
    const toggle = ownToggleWords(h);
    openMenu(it.button, h.name, [
      ['Change picture', () => openPicker(it)],
      [
        toggle,
        () => {
          closePop(true);
          void act('POST', ownPath(h.id, toggle === 'Resume' ? '/resume' : '/pause'));
        },
      ],
      [
        'Details',
        () => {
          closePop(false);
          d.details(h.id);
        },
      ],
      ['Delete', () => askDelete(it)],
    ]);
  }

  /** A route for one of the user's own: its list drawn at once, or what went wrong said. */
  async function act(method: 'POST' | 'PUT' | 'DELETE', path: string, body: unknown = {}): Promise<boolean> {
    const done = await helperAction(d.api, method, path, body);
    if (!done.ok) {
      d.announce(done.wrong);
      return false;
    }
    if (done.list) d.update(done.list);
    return true;
  }

  /** The same plain confirm as the panel's: Delete it, or Keep it (focused first). */
  function askDelete(it: Item): void {
    const h = it.helper;
    const box = el('div', 'rail-confirm');
    box.setAttribute('role', 'alertdialog');
    const words = el('p', '', deleteQuestion(h.name));
    words.id = 'rail-confirm-text';
    box.setAttribute('aria-labelledby', words.id);
    const yes = button('hp-delete', 'Delete it');
    const no = button('quiet', 'Keep it');
    const row = el('div', 'rail-confirm-row');
    row.append(yes, no);
    box.append(words, row);
    yes.addEventListener('click', () => {
      closePop(false);
      herBtn.focus();
      void act('DELETE', ownPath(h.id));
    });
    no.addEventListener('click', () => closePop(true));
    openPop(it.button, box);
    no.focus();
  }

  // ── Its picture: the panel's picker, saved at each tap ──
  let taps = 0;
  function openPicker(it: Item): void {
    const h = it.helper;
    const saved = (): Look => lookOf(it.helper.icon, it.helper.color);
    const box = el('div');
    const picker: Picker = createPicker({
      name: h.name,
      look: saved(),
      onPick: (look) => {
        const n = (taps += 1);
        void helperAction(d.api, 'PUT', ownPath(h.id, '/look'), { icon: look.icon, color: look.color }).then((done) => {
          // Only the last tap's answer settles it: an earlier one is overtaken.
          if (n !== taps) return;
          if (done.ok) {
            if (done.list) d.update(done.list);
            return;
          }
          d.announce(done.wrong);
          picker.set(saved());
        });
      },
      onClose: () => closePop(true),
    });
    box.append(picker.element);
    openPop(it.button, box);
    picker.focus();
  }

  // ── + New helper: the panel's one field, beside the + ──
  function openNew(): void {
    const box = el('div');
    const { form, field } = newHelperForm(
      'rail-new',
      (text) => {
        closePop(false);
        d.newHelper(text);
      },
      () => closePop(true),
    );
    box.append(form);
    openPop(addBtn, box, true);
    field.focus();
  }

  herBtn.addEventListener('click', () => {
    if (swallowClick()) return;
    closePop(false);
    d.her();
  });
  menuTriggers(herBtn, herMenu);
  addBtn.addEventListener('click', () => {
    if (pop?.anchor === addBtn) closePop(true);
    else openNew();
  });

  drawHer();
  drawSelected();
  drawStops();

  return {
    element,
    update,
    select(conversationId) {
      if (conversationId === showing) return;
      showing = conversationId;
      drawSelected();
    },
    name(next) {
      if (next === agentName) return;
      agentName = next;
      drawHer();
    },
    close: () => closePop(false),
  };
}
