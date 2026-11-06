/**
 * A helper's picture (the your helpers spec, Its picture): an icon in a colour, drawn
 * round, so a helper reads at a glance. The ids are the core's (`helpers/look.py`); each
 * icon is a few stroke paths on a 24 by 24 grid, drawn here, and each colour is a theme
 * token (`--helper-<colour>` in app.css). Built as SVG elements: no markup strings.
 *
 * The picker is a grid of the icons and a row of the colours, from the picture in a
 * helper's details. A tap is saved at once by whoever opened it (`onPick`).
 */

export const ICONS = [
  'receipt',
  'envelope',
  'calendar',
  'chart',
  'cart',
  'folder',
  'people',
  'chat',
  'star',
  'heart',
  'bell',
  'globe',
  'book',
  'briefcase',
  'wrench',
  'leaf',
  'home',
  'plane',
  'music',
  'camera',
  'code',
  'shield',
  'lightbulb',
  'spark',
] as const;

export const COLORS = ['blue', 'green', 'amber', 'rose', 'violet', 'teal', 'orange', 'slate'] as const;

export type IconId = (typeof ICONS)[number];
export type ColorId = (typeof COLORS)[number];

export interface Look {
  readonly icon: IconId;
  readonly color: ColorId;
}

const RING = 'M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 0 0 0-17z';
const dot = (x: number, y: number): string => `M${x - 1} ${y}a1 1 0 1 0 2 0a1 1 0 0 0-2 0z`;

const PATHS: Readonly<Record<IconId, readonly string[]>> = {
  receipt: ['M6 3.5h12v17l-2-1.5l-2 1.5l-2-1.5l-2 1.5l-2-1.5l-2 1.5z', 'M9 8h6M9 11.5h6M9 15h3.5'],
  envelope: ['M3.5 6h17v12h-17z', 'M3.5 6.5l8.5 6.5l8.5-6.5'],
  calendar: ['M4 5.5h16v14H4z', 'M4 9.5h16', 'M8 3.5v4M16 3.5v4'],
  chart: ['M4 20h16', 'M7 16.5v-5M12 16.5V7M17 16.5v-8'],
  cart: ['M3.5 4.5h2.5l2 10.5h10l2-7.5H7', dot(10, 19.5), dot(17, 19.5)],
  folder: ['M3.5 6.5h6l2 2h9v10h-17z'],
  people: ['M9 11a3.5 3.5 0 1 0 0-7a3.5 3.5 0 0 0 0 7z', 'M2.5 20a6.5 6.5 0 0 1 13 0', 'M16 4.5a3.5 3.5 0 0 1 0 6.5', 'M18 14.2a6.5 6.5 0 0 1 3.5 5.8'],
  chat: ['M4.5 5.5h15v10h-8l-4 3.5v-3.5h-3z'],
  star: ['M12 4l2.4 5l5.4.6l-4 3.7l1.1 5.4L12 16l-4.9 2.7l1.1-5.4l-4-3.7l5.4-.6z'],
  heart: ['M12 19.5s-7.5-4.5-7.5-10a4 4 0 0 1 7.5-2a4 4 0 0 1 7.5 2c0 5.5-7.5 10-7.5 10z'],
  bell: ['M6.5 16.5v-5a5.5 5.5 0 0 1 11 0v5l1.5 1.5h-14z', 'M10 20.5h4'],
  globe: [RING, 'M3.5 12h17', 'M12 3.5c2.5 2.5 3.5 5.5 3.5 8.5s-1 6-3.5 8.5c-2.5-2.5-3.5-5.5-3.5-8.5s1-6 3.5-8.5z'],
  book: ['M12 6.5c-2-1.5-5-2-8-1.5v13c3-.5 6 0 8 1.5c2-1.5 5-2 8-1.5v-13c-3-.5-6 0-8 1.5z', 'M12 6.5v13'],
  briefcase: ['M3.5 8h17v11h-17z', 'M9 8V5.5h6V8', 'M3.5 13h17'],
  wrench: ['M14.5 4a4.5 4.5 0 0 0-4.2 6.1l-5.8 5.8a1.8 1.8 0 0 0 2.6 2.6l5.8-5.8A4.5 4.5 0 0 0 19 8.5l-2.8 2.8l-2.7-.7l-.7-2.7l2.8-2.8A4.5 4.5 0 0 0 14.5 4z'],
  leaf: ['M5 19c0-8 5-13 14-14c0 9-5 14-13 14z', 'M5 19l8-8'],
  home: ['M4 11l8-6.5l8 6.5', 'M6.5 9.5v9.5h11V9.5', 'M10 19v-5h4v5'],
  plane: ['M20.5 3.5l-17 7l7 2.5l2.5 7z', 'M20.5 3.5l-10 9.5'],
  music: ['M9 17.5V5.5l10-2v12', 'M9 17.5a2.5 2.5 0 1 1-5 0a2.5 2.5 0 0 1 5 0z', 'M19 15.5a2.5 2.5 0 1 1-5 0a2.5 2.5 0 0 1 5 0z'],
  camera: ['M3.5 7.5h4l1.5-2.5h6l1.5 2.5h4v11h-17z', 'M12 9.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 0 0 0-7z'],
  code: ['M8.5 7.5L4 12l4.5 4.5', 'M15.5 7.5L20 12l-4.5 4.5', 'M13.5 5l-3 14'],
  shield: ['M12 3.5l7.5 3v5.5c0 4.5-3.2 7.5-7.5 9c-4.3-1.5-7.5-4.5-7.5-9V6.5z'],
  lightbulb: ['M9 17.5h6', 'M10 20.5h4', 'M12 3.5a5.5 5.5 0 0 0-3.5 9.8c.6.5 1 1.2 1 2v.7h5v-.7c0-.8.4-1.5 1-2A5.5 5.5 0 0 0 12 3.5z'],
  spark: ['M12 3.5c.7 4.6 3.4 7.3 8 8c-4.6.7-7.3 3.4-8 8c-.7-4.6-3.4-7.3-8-8c4.6-.7 7.3-3.4 8-8z'],
};

export const isIcon = (v: unknown): v is IconId => typeof v === 'string' && (ICONS as readonly string[]).includes(v);
export const isColor = (v: unknown): v is ColorId => typeof v === 'string' && (COLORS as readonly string[]).includes(v);

/** A picture from what the core sent, never blank: a spark in slate for anything not on the lists. */
export function lookOf(icon: unknown, color: unknown): Look {
  return { icon: isIcon(icon) ? icon : 'spark', color: isColor(color) ? color : 'slate' };
}

/** Its accessible name: "Invoices picture: receipt, green". */
export function pictureLabel(name: string, look: Look): string {
  return `${name} picture: ${look.icon}, ${look.color}`;
}

const SVG = 'http://www.w3.org/2000/svg';

/** One icon, alone: stroked in the current colour. */
export function glyph(id: IconId, size: number): SVGSVGElement {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  for (const d of PATHS[id]) {
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

export type PictureSize = 18 | 28 | 44;

/** Draw a picture into `node` (a span or a button), sized and coloured. */
export function drawPicture(node: HTMLElement, look: Look, size: PictureSize): void {
  node.classList.add('look');
  node.dataset['size'] = String(size);
  node.dataset['color'] = look.color;
  node.dataset['icon'] = look.icon;
  node.replaceChildren(glyph(look.icon, Math.round(size * 0.58)));
}

/**
 * A helper's round picture. Named, it is an image with its accessible name; without a
 * name it is decoration beside words that already say who.
 */
export function picture(look: Look, size: PictureSize, name?: string): HTMLSpanElement {
  const span = document.createElement('span');
  drawPicture(span, look, size);
  if (name) {
    span.setAttribute('role', 'img');
    span.setAttribute('aria-label', pictureLabel(name, look));
  } else span.setAttribute('aria-hidden', 'true');
  return span;
}

export interface PickerDeps {
  readonly name: string;
  readonly look: Look;
  /** A tap: save it. The picker shows it at once. */
  readonly onPick: (look: Look) => void;
  /** Escape, Done or a tap outside. Focus goes back to whatever opened it. */
  readonly onClose: () => void;
}

export interface Picker {
  readonly element: HTMLElement;
  /** Show this picture as chosen (a push from the core). */
  set(look: Look): void;
  /** Focus the chosen icon. */
  focus(): void;
}

const COLUMNS = 6;

/** The picker: 24 icons in a 6 by 4 grid, then the 8 colours. Arrows move, Escape closes. */
export function createPicker(d: PickerDeps): Picker {
  let look = d.look;
  const element = document.createElement('div');
  element.className = 'look-picker';
  element.setAttribute('role', 'dialog');
  element.setAttribute('aria-label', `Picture for ${d.name}`);

  const iconsBox = document.createElement('div');
  iconsBox.className = 'look-icons';
  iconsBox.setAttribute('role', 'group');
  iconsBox.setAttribute('aria-label', 'Icon');
  const colorsBox = document.createElement('div');
  colorsBox.className = 'look-colors';
  colorsBox.setAttribute('role', 'group');
  colorsBox.setAttribute('aria-label', 'Colour');

  const button = (label: string): HTMLButtonElement => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('aria-label', label);
    b.title = label;
    return b;
  };
  const iconButtons = ICONS.map((id) => {
    const b = button(id);
    b.className = 'look-icon';
    b.dataset['icon'] = id;
    b.append(glyph(id, 18));
    b.addEventListener('click', () => pick({ ...look, icon: id }));
    return b;
  });
  const colorButtons = COLORS.map((id) => {
    const b = button(id);
    b.className = 'look-color';
    b.dataset['color'] = id;
    b.addEventListener('click', () => pick({ ...look, color: id }));
    return b;
  });
  iconsBox.append(...iconButtons);
  colorsBox.append(...colorButtons);
  const done = document.createElement('button');
  done.type = 'button';
  done.className = 'quiet look-done';
  done.textContent = 'Done';
  done.addEventListener('click', () => d.onClose());
  element.append(iconsBox, colorsBox, done);

  /** One tab stop per group, on its chosen button; each shows which is chosen. */
  function draw(): void {
    iconButtons.forEach((b, i) => {
      const on = ICONS[i] === look.icon;
      b.setAttribute('aria-pressed', String(on));
      b.tabIndex = on ? 0 : -1;
      b.dataset['color'] = look.color;
    });
    colorButtons.forEach((b, i) => {
      const on = COLORS[i] === look.color;
      b.setAttribute('aria-pressed', String(on));
      b.tabIndex = on ? 0 : -1;
    });
  }

  function pick(next: Look): void {
    if (next.icon === look.icon && next.color === look.color) return;
    look = next;
    draw();
    d.onPick(next);
  }

  /** Arrows move within a group: across and down the icon grid, along the colours. */
  function move(list: readonly HTMLButtonElement[], from: number, key: string, columns: number): boolean {
    const step: Readonly<Record<string, number>> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns };
    let to: number;
    if (key === 'Home') to = 0;
    else if (key === 'End') to = list.length - 1;
    else if (key in step) to = from + (step[key] ?? 0);
    else return false;
    const target = list[Math.max(0, Math.min(list.length - 1, to))];
    if (!target) return false;
    for (const b of list) b.tabIndex = b === target ? 0 : -1;
    target.focus();
    return true;
  }

  element.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      ev.preventDefault();
      ev.stopPropagation();
      d.onClose();
      return;
    }
    const at = ev.target instanceof HTMLButtonElement ? ev.target : null;
    if (!at) return;
    const i = iconButtons.indexOf(at);
    const c = colorButtons.indexOf(at);
    const moved = i >= 0 ? move(iconButtons, i, ev.key, COLUMNS) : c >= 0 ? move(colorButtons, c, ev.key, 0) : false;
    if (moved) ev.preventDefault();
  });

  draw();
  return {
    element,
    set(next) {
      look = next;
      draw();
    },
    focus() {
      (iconButtons.find((b) => b.tabIndex === 0) ?? iconButtons[0])?.focus();
    },
  };
}
