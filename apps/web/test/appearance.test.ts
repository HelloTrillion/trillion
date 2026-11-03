// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { createAppearance } from '../src/browser';
import { type Detail, perfModeOf, saveDetail, savedDetail } from '../src/prefs';

function host() {
  const el = document.createElement('div');
  document.body.append(el);
  return Object.assign(el, { open: false, showModal() {}, close() {} });
}

afterEach(() => {
  localStorage.clear();
  document.body.replaceChildren();
});

describe('how much detail the galaxy draws (GAL-6)', () => {
  it('is automatic until the user chooses, and is kept in this browser', () => {
    expect(savedDetail()).toBe('auto');
    saveDetail('smooth');
    expect(savedDetail()).toBe('smooth');
    saveDetail('full');
    expect(savedDetail()).toBe('full');
    localStorage.setItem('trillion-detail', 'nonsense');
    expect(savedDetail()).toBe('auto');
  });

  it('maps each choice to the galaxy performance mode', () => {
    expect(perfModeOf('auto')).toBe('auto');
    expect(perfModeOf('smooth')).toBe('on');
    expect(perfModeOf('full')).toBe('off');
  });

  it('is a choice under Appearance beside the theme', () => {
    const h = host();
    let detail: Detail = 'full';
    const chosen: Detail[] = [];
    const a = createAppearance(h, () => 'system', () => {}, {
      get: () => detail,
      set: (d) => {
        detail = d;
        chosen.push(d);
      },
    });
    a.open();
    const group = h.querySelector('[role="radiogroup"][aria-label="Performance"]');
    expect(group).not.toBeNull();
    const radios = [...group!.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
    expect(radios.map((r) => r.value)).toEqual(['auto', 'smooth', 'full']);
    expect(radios.find((r) => r.checked)?.value).toBe('full');
    expect(group!.textContent).toContain('Always smooth');
    expect(group!.textContent).toContain('Full detail');
    radios[1]!.checked = true;
    radios[1]!.dispatchEvent(new Event('change'));
    expect(chosen).toEqual(['smooth']);
    // The theme's choices are a separate group.
    expect(h.querySelector('[role="radiogroup"][aria-label="Theme"]')).not.toBeNull();
  });
});
