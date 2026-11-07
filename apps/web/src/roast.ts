/**
 * Roast my week: her roast, as a card worth sharing.
 *
 * She reads the true facts of the user's week (`week_roast`) and roasts it. Under that
 * reply sits a Share button; it draws the roast on a card with the orb, in the dark
 * look whatever the theme, so every card looks the same wherever it is posted. The card
 * is made here, on this machine: nothing is uploaded, and only the user decides where
 * it goes.
 */

import './roast.css';
import type { Entry } from './view';

export const ROAST_TOOL = 'week_roast';
export const CARD_W = 1080;
export const CARD_H = 1350;
const PAD = 96;
const TEXT_TOP = 560;
const TEXT_BOTTOM = 1130;

/** Whether the reply now starting answers a roast: her turn called `week_roast`, and it worked. */
export function isRoastTurn(entries: readonly Entry[]): boolean {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i]!;
    if (e.kind === 'user') return false;
    if (e.kind === 'tool' && e.name === ROAST_TOOL) return e.ok && e.outcome !== null && e.outcome !== 'failed';
  }
  return false;
}

/** Her words as the card shows them: no markdown marks, one paragraph per line. */
export function plain(text: string): string[] {
  return text
    .replace(/\*\*|__|`/g, '')
    .replace(/(^|\s)[*_](\S[^*_]*\S|\S)[*_](?=\s|$|[.,!?;:])/g, '$1$2')
    .split(/\n+/)
    .map((p) => p.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '').trim())
    .filter(Boolean);
}

/** Words into lines no wider than `width`, as `measure` says. Paragraphs stay apart. */
export function wrap(paragraphs: readonly string[], width: number, measure: (s: string) => number): string[][] {
  return paragraphs.map((p) => {
    const lines: string[] = [];
    let line = '';
    for (const word of p.split(/\s+/)) {
      const next = line ? `${line} ${word}` : word;
      if (line && measure(next) > width) {
        lines.push(line);
        line = word;
      } else line = next;
    }
    if (line) lines.push(line);
    return lines;
  });
}

/** "Oct 31 – Nov 6": the seven days up to `now`, in the user's own calendar. */
export function weekRange(now: Date, locale?: string): string {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6);
  const f = new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric' });
  return `${f.format(start)} – ${f.format(now)}`;
}

interface Palette {
  bg0: string;
  bg1: string;
  text0: string;
  text1: string;
  text2: string;
  accent: string;
  amber: string;
  glow: string;
  font: string;
}

/** The dark theme's tokens, read from the stylesheet so no colour is written here (GAL-11). */
function palette(): Palette {
  const probe = document.createElement('div');
  probe.setAttribute('data-theme', 'dark');
  probe.hidden = true;
  document.body.append(probe);
  const s = getComputedStyle(probe);
  const v = (name: string): string => s.getPropertyValue(name).trim();
  const p = {
    bg0: v('--color-bg-0'),
    bg1: v('--color-bg-1'),
    text0: v('--color-text-0'),
    text1: v('--color-text-1'),
    text2: v('--color-text-2'),
    accent: v('--color-accent'),
    amber: v('--color-amber'),
    glow: v('--mind-working'),
    font: v('--font-ui') || 'sans-serif',
  };
  probe.remove();
  return p;
}

function orb(g: CanvasRenderingContext2D, p: Palette, cx: number, cy: number, r: number): void {
  // The glow behind it, then the body, lit from above like hers.
  const halo = g.createRadialGradient(cx, cy, r * 0.4, cx, cy, r * 2.6);
  halo.addColorStop(0, p.accent);
  halo.addColorStop(1, p.bg0);
  g.globalAlpha = 0.35;
  g.fillStyle = halo;
  g.fillRect(0, 0, CARD_W, cy + r * 2.6);
  g.globalAlpha = 1;
  const body = g.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
  body.addColorStop(0, p.glow);
  body.addColorStop(0.55, p.accent);
  body.addColorStop(1, p.bg1);
  g.fillStyle = body;
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fill();
  // Her three rings, each at its own tilt.
  g.strokeStyle = p.glow;
  g.lineWidth = 2;
  [
    [1.55, 0.28, -0.2],
    [1.65, 0.2, 0.15],
    [1.75, 0.34, 0.05],
  ].forEach(([scale, squash, tilt], i) => {
    g.globalAlpha = 0.5 - i * 0.12;
    g.beginPath();
    g.ellipse(cx, cy, r * scale!, r * scale! * squash!, tilt!, 0, Math.PI * 2);
    g.stroke();
  });
  g.globalAlpha = 1;
}

/** Draw the card on `canvas`: the orb, her roast as large as it fits, her name, the week. */
export function drawCard(canvas: HTMLCanvasElement, text: string, agentName: string, range: string): void {
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const g = canvas.getContext('2d');
  if (!g) return;
  const p = palette();
  g.fillStyle = p.bg0;
  g.fillRect(0, 0, CARD_W, CARD_H);
  orb(g, p, CARD_W / 2, 250, 120);

  g.textAlign = 'left';
  g.textBaseline = 'alphabetic';
  g.fillStyle = p.amber;
  g.font = `600 30px ${p.font}`;
  g.letterSpacing = '6px';
  g.fillText('MY WEEK, ROASTED', PAD, TEXT_TOP - 40);
  g.letterSpacing = '0px';

  // The largest size at which the whole roast fits between the eyebrow and the byline.
  const paragraphs = plain(text);
  let size = 52;
  let lines: string[][] = [];
  for (; size >= 26; size -= 2) {
    g.font = `500 ${size}px ${p.font}`;
    lines = wrap(paragraphs, CARD_W - PAD * 2, (s) => g.measureText(s).width);
    const rows = lines.reduce((n, l) => n + l.length, 0);
    if (rows * size * 1.32 + (lines.length - 1) * size * 0.6 <= TEXT_BOTTOM - TEXT_TOP) break;
  }
  g.fillStyle = p.text0;
  let y = TEXT_TOP + size;
  for (const para of lines) {
    for (const line of para) {
      g.fillText(line, PAD, y);
      y += size * 1.32;
    }
    y += size * 0.6;
  }

  g.fillStyle = p.text1;
  g.font = `500 34px ${p.font}`;
  g.fillText(`— ${agentName}`, PAD, Math.min(y + 20, TEXT_BOTTOM + 60));

  g.fillStyle = p.text2;
  g.font = `400 28px ${p.font}`;
  g.fillText(range, PAD, CARD_H - 72);
  g.textAlign = 'right';
  g.fillText('Made with Trillion', CARD_W - PAD, CARD_H - 72);
}

function button(label: string, cls = ''): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = cls;
  b.textContent = label;
  return b;
}

/** The card in a dialog of its own, with Copy image, Save and Close. */
export function openRoastCard(text: string, agentName: string, now: Date = new Date()): HTMLDialogElement {
  const dialog = document.createElement('dialog');
  dialog.className = 'roast-dialog';
  dialog.setAttribute('aria-label', 'Share your roast');
  const canvas = document.createElement('canvas');
  drawCard(canvas, text, agentName, weekRange(now));
  const img = document.createElement('img');
  img.className = 'roast-card';
  img.alt = `Your week, roasted by ${agentName}: ${plain(text).join(' ')}`;
  img.src = canvas.toDataURL('image/png');

  const status = document.createElement('p');
  status.className = 'roast-status';
  status.setAttribute('role', 'status');
  const copy = button('Copy image', 'primary');
  const save = button('Save');
  const close = button('Close');
  const actions = document.createElement('div');
  actions.className = 'roast-actions';
  actions.append(copy, save, close);

  copy.addEventListener('click', () => {
    canvas.toBlob(async (blob) => {
      try {
        if (!blob || typeof ClipboardItem === 'undefined') throw new Error('unsupported');
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        status.textContent = 'Copied. Paste it anywhere.';
      } catch {
        status.textContent = "This browser won't copy images. Use Save instead.";
      }
    }, 'image/png');
  });
  save.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = img.src;
    a.download = 'my-week-roasted.png';
    a.click();
    status.textContent = 'Saved as my-week-roasted.png.';
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => dialog.remove());

  dialog.append(img, actions, status);
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}
