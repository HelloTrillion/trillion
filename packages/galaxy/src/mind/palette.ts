/**
 * The Living Mind's colours come from the design tokens (GAL-11): one per region,
 * the void behind it, and danger for a failure. No colour is written here.
 */

import { Color, DataTexture, LinearFilter, RGBAFormat, UnsignedByteType } from 'three';
import { REGION_IDS, type RegionId } from './contract';

export type PaletteKey = RegionId | 'void' | 'danger';

export type MindPalette = Readonly<Record<PaletteKey, string>>;

export const PALETTE_TOKENS: Readonly<Record<PaletteKey, string>> = {
  core: '--mind-core',
  memory: '--mind-memory',
  working: '--mind-working',
  agents: '--mind-agents',
  knowledge: '--mind-knowledge',
  rim: '--mind-rim',
  void: '--mind-void',
  danger: '--color-danger',
};

/** The palette as the page's tokens define it. A missing token reads as white. */
export function readPalette(root: Element = document.documentElement): MindPalette {
  const style = getComputedStyle(root);
  const out = {} as Record<PaletteKey, string>;
  for (const [key, token] of Object.entries(PALETTE_TOKENS) as [PaletteKey, string][]) {
    out[key] = style.getPropertyValue(token).trim() || 'white';
  }
  return out;
}

/** Each palette entry as a three colour. A value three can't read becomes white. */
export function paletteColors(p: MindPalette): Readonly<Record<PaletteKey, Color>> {
  const out = {} as Record<PaletteKey, Color>;
  for (const key of [...REGION_IDS, 'void', 'danger'] as PaletteKey[]) {
    const c = new Color(1, 1, 1);
    try {
      c.setStyle(p[key]);
    } catch {
      // Keep white.
    }
    out[key] = c;
  }
  return out;
}

/** A colour lifted toward white, for the hot parts of her star. */
export const toward = (c: Color, white: number): Color => c.clone().lerp(new Color(1, 1, 1), white);

let radial: DataTexture | null = null;

/**
 * A soft white disc fading to nothing, made in code (no image is fetched, ONB-8),
 * shared by the coronas, region glows, pulses and particles.
 */
export function radialTexture(): DataTexture {
  if (radial) return radial;
  const size = 128;
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.min(1, Math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2) / (size / 2));
      // The gradient's stops: 0.9 at the centre, 0.28 at 40%, nothing at the edge.
      const a = d < 0.4 ? 0.9 - (0.62 * d) / 0.4 : 0.28 * (1 - (d - 0.4) / 0.6);
      const i = (y * size + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = 255;
      data[i + 3] = Math.round(Math.max(0, a) * 255);
    }
  }
  radial = new DataTexture(data, size, size, RGBAFormat, UnsignedByteType);
  radial.magFilter = LinearFilter;
  radial.minFilter = LinearFilter;
  radial.needsUpdate = true;
  return radial;
}
