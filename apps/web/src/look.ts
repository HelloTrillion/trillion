/**
 * A helper's picture (the your helpers spec, Its picture): a small orb like hers, lit
 * from the top left, in the helper's own colour, so a helper reads at a glance and the
 * user can colour-code them. The colour ids are the core's (`helpers/look.py`); each is
 * a theme token (`--helper-<colour>` in app.css). Built as elements: no markup strings.
 *
 * A helper also keeps the icon the core gave it. It is not drawn; it is sent back as it
 * was when the colour changes, since the core still stores one.
 *
 * The picker is a row of the colours, each an orb, from the picture in a helper's
 * details or its menu in the rail. A tap is saved at once by whoever opened it (`onPick`).
 */

/** In the order the picker shows them: round the colour wheel, then slate. */
export const COLORS = [
  'red',
  'orange',
  'amber',
  'lime',
  'green',
  'teal',
  'blue',
  'violet',
  'fuchsia',
  'rose',
  'slate',
] as const;

export type ColorId = (typeof COLORS)[number];

export interface Look {
  /** The core's stored icon, sent back unchanged; never drawn. */
  readonly icon: string;
  readonly color: ColorId;
}

export const isColor = (v: unknown): v is ColorId => typeof v === 'string' && (COLORS as readonly string[]).includes(v);

const ICON_ID = /^[a-z]{1,24}$/;

/** A picture from what the core sent, never blank: slate for a colour not on the list,
 * and the core's own fallback icon for one that is not a plain id. */
export function lookOf(icon: unknown, color: unknown): Look {
  return { icon: typeof icon === 'string' && ICON_ID.test(icon) ? icon : 'spark', color: isColor(color) ? color : 'slate' };
}

/** Its accessible name: "Invoices, green". */
export function pictureLabel(name: string, look: Look): string {
  return `${name}, ${look.color}`;
}

export type PictureSize = 18 | 28 | 44;

/** Draw a picture into `node` (a span or a button): an orb, sized and coloured. */
export function drawPicture(node: HTMLElement, look: Look, size: PictureSize): void {
  node.classList.add('look');
  node.dataset['size'] = String(size);
  node.dataset['color'] = look.color;
  node.replaceChildren();
}

/**
 * A helper's orb. Named, it is an image with its accessible name; without a name it is
 * decoration beside words that already say who.
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
  /** Show this colour as chosen (a push from the core). */
  set(look: Look): void;
  /** Focus the chosen colour. */
  focus(): void;
}

const COLUMNS = 6;

/** The picker: the colours, each an orb, six to a row. Arrows move, Escape closes. */
export function createPicker(d: PickerDeps): Picker {
  let look = d.look;
  const element = document.createElement('div');
  element.className = 'look-picker';
  element.setAttribute('role', 'dialog');
  element.setAttribute('aria-label', `Colour for ${d.name}`);

  const colorsBox = document.createElement('div');
  colorsBox.className = 'look-colors';
  colorsBox.setAttribute('role', 'group');
  colorsBox.setAttribute('aria-label', 'Colour');

  const colorButtons = COLORS.map((id) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('aria-label', id);
    b.title = id;
    b.className = 'look-color';
    b.dataset['color'] = id;
    b.addEventListener('click', () => pick({ ...look, color: id }));
    return b;
  });
  colorsBox.append(...colorButtons);
  const done = document.createElement('button');
  done.type = 'button';
  done.className = 'quiet look-done';
  done.textContent = 'Done';
  done.addEventListener('click', () => d.onClose());
  element.append(colorsBox, done);

  /** One tab stop, on the chosen colour; each shows whether it is chosen. */
  function draw(): void {
    colorButtons.forEach((b, i) => {
      const on = COLORS[i] === look.color;
      b.setAttribute('aria-pressed', String(on));
      b.tabIndex = on ? 0 : -1;
    });
  }

  function pick(next: Look): void {
    if (next.color === look.color) return;
    look = next;
    draw();
    d.onPick(next);
  }

  /** Arrows move across and down the colours; Home and End go to the ends. */
  function move(from: number, key: string): boolean {
    const step: Readonly<Record<string, number>> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: COLUMNS, ArrowUp: -COLUMNS };
    let to: number;
    if (key === 'Home') to = 0;
    else if (key === 'End') to = colorButtons.length - 1;
    else if (key in step) to = from + (step[key] ?? 0);
    else return false;
    const target = colorButtons[Math.max(0, Math.min(colorButtons.length - 1, to))];
    if (!target) return false;
    for (const b of colorButtons) b.tabIndex = b === target ? 0 : -1;
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
    const at = ev.target instanceof HTMLButtonElement ? colorButtons.indexOf(ev.target) : -1;
    if (at >= 0 && move(at, ev.key)) ev.preventDefault();
  });

  draw();
  return {
    element,
    set(next) {
      look = next;
      draw();
    },
    focus() {
      (colorButtons.find((b) => b.tabIndex === 0) ?? colorButtons[0])?.focus();
    },
  };
}
