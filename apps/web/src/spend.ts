/**
 * Spend caps on the page: the limits in each key step, a block per service in
 * Settings → Usage, the card at a cap, and the line for a switch made there.
 *
 * The words are plain: "Warn me at" and "Stop at", never soft and hard caps. The card
 * is the app's, not a pending action. A choice is sent only by a click or a tap, as a
 * `spend.choice` message on this page's own socket (SPN-14): it is about money, and the
 * core takes it only from the conversation it showed the card to. Every piece of text
 * is set with textContent, never parsed as markup.
 */

import type { Api } from './settings';
import { type Leg, type ProviderUsage, type Unit, type Usage, amountText, capText, spendNote } from './usage';
import type { ServerMsg } from './view';

/** What a choice that runs on this computer says, here and in the core. */
export const FREE = 'Runs on this computer: no cost, no cap.';

export interface Caps {
  readonly soft: number | null;
  readonly hard: number | null;
  readonly warn_pct: number;
  readonly unit: Unit;
}

/** What a key step shows before the key is saved: the core's `cost`. */
export interface Cost {
  readonly provider_id: string | null;
  readonly name: string | null;
  readonly estimate: string;
  readonly pricing_url: string | null;
  readonly caps: Caps | null;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text: string, cls = ''): HTMLButtonElement {
  const b = el('button', cls, text);
  b.type = 'button';
  return b;
}

/** A link to a provider's prices, which says it opens a new tab. */
function pricesLink(text: string, href: string): HTMLAnchorElement {
  const a = el('a', 'spend-prices', text);
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.append(el('span', 'visually-hidden', ' (opens in a new tab)'));
  return a;
}

function status(cls = 'spend-status'): HTMLElement {
  const s = el('span', cls);
  s.setAttribute('role', 'status');
  return s;
}

let ids = 0;
function numberRow(label: string, prefix: string): { row: HTMLElement; input: HTMLInputElement; label: HTMLLabelElement } {
  const row = el('div', 'settings-row');
  const l = el('label', '', label);
  const input = el('input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.id = `${prefix}-${++ids}`;
  l.htmlFor = input.id;
  row.append(l, input);
  return { row, input, label: l };
}

// ── Amounts in fields ─────────────────────────────────────────────────────

const A_MONTH: Readonly<Record<Unit, string>> = {
  dollars: '$ a month',
  tokens: 'tokens a month',
  chars: 'characters a month',
  seconds: 'minutes a month',
};

/** An amount as a field shows it, grouped ("5,000,000"): hearing, kept in seconds, is typed in minutes. */
export function toField(n: number | null, unit: Unit): string {
  if (n === null) return '';
  const shown = unit === 'seconds' ? n / 60 : n;
  return shown.toLocaleString('en', { maximumFractionDigits: 2 });
}

/** A field's text as the core's amount, commas and spaces allowed: null when empty, undefined when it is not one. */
export function fromField(text: string, unit: Unit): number | null | undefined {
  const t = text.trim().replace(/^\$/, '').replace(/[,\s]/g, '');
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < 0) return undefined;
  return unit === 'seconds' ? Math.round(n * 60) : n;
}

// ── Words ─────────────────────────────────────────────────────────────────

const BELOW = "Stop at can't be lower than Warn me at.";
const NUMBERS = 'Amounts are numbers of zero or more, or empty for none.';
const NOT_ANSWERED = "Trillion didn't answer. Is it still running?";

const ERRORS: Readonly<Record<string, string>> = {
  hard_below_soft: BELOW,
  too_high: 'That is more than Trillion allows here.',
  bad_unit: 'That unit does not fit this service.',
  no_price: "Trillion doesn't know this service's price yet, so its limits count tokens. Add a price under More options.",
  never_capped: 'This runs on this computer, so there is nothing to limit.',
  unknown_provider: 'That service is not set up.',
  bad_price: 'That price is not one Trillion can use. Prices are dollars of zero or more.',
  bad_model: 'That model name cannot be priced.',
  too_many: 'There are too many prices saved. Remove some first.',
  zero_price: 'A price of zero would stop this model being counted. Leave it empty instead.',
};

const FIELD_WORDS: Readonly<Record<string, string>> = { soft: 'Warn me at', hard: 'Stop at', warn_pct: 'the early heads-up' };

/** The core's refusal of a limit or a price, in plain words. */
export async function spendProblem(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown; fields?: unknown };
    const code = typeof body.error === 'string' ? body.error : '';
    if (code === 'bad_request') {
      const named = (Array.isArray(body.fields) ? body.fields : []).map((f) => FIELD_WORDS[String(f)]).filter(Boolean);
      return named.length ? `Check ${named.join(' and ')}.` : NUMBERS;
    }
    return ERRORS[code] ?? 'That did not work. Try again.';
  } catch {
    return 'That did not work. Try again.';
  }
}

function capsProblem(soft: number | null | undefined, hard: number | null | undefined): string | null {
  if (soft === undefined || hard === undefined) return NUMBERS;
  if (soft !== null && hard !== null && hard < soft) return BELOW;
  return null;
}

/** Said plainly when nothing stops the spending. */
export function noCapLine(name: string, soft: number | null, hard: number | null): string {
  if (hard !== null) return '';
  return `No stop set: Trillion won't stop spending on ${name}.${soft === null ? ' No warning either.' : ''}`;
}

/** "Warns at $20, stops at $50 a month." */
export function limitsLine(soft: number | null, hard: number | null, unit: Unit): string {
  const parts = [soft === null ? '' : `warns at ${capText(soft, unit)}`, hard === null ? '' : `stops at ${capText(hard, unit)}`].filter(Boolean);
  if (!parts.length) return 'No limits set.';
  const said = parts.join(', ');
  return `${said[0]?.toUpperCase() ?? ''}${said.slice(1)} a month.`;
}

/**
 * The core's cost line, shortened to what a glance needs: "About $0.40 an hour of
 * talking." Any other line (no price known yet, say) is kept as the core said it.
 */
export function shortEstimate(estimate: string): string {
  const m = /^Roughly (\S+) an hour of (talk|speaking|listening)\b/.exec(estimate);
  if (!m) return estimate;
  const what = m[2] === 'talk' ? 'talking' : m[2];
  return `About ${m[1]} an hour of ${what}.`;
}

const capsPath = (pid: string): string => `/api/settings/spend/${encodeURIComponent(pid)}`;
const endPath = (pid: string): string => `/api/spend/switch/${encodeURIComponent(pid)}/end`;

/** Ask the core to end a switch now. Null when it did, else what to say. */
export async function endSwitch(api: Api, pid: string, name: string): Promise<string | null> {
  try {
    const r = await api('POST', endPath(pid), {});
    if (r.ok) return null;
    if (r.status === 409) return `${name} is still at its cap. Raise Stop at first.`;
    if (r.status === 404) return 'That switch has already ended.';
    return 'That did not work. Try again.';
  } catch {
    return NOT_ANSWERED;
  }
}

// ── The limits in a key step ──────────────────────────────────────────────

export interface CapStep {
  readonly element: HTMLElement;
  /** Show this provider's cost, prices page and the limits it will have. */
  show(cost: Cost | null): void;
  /** What is wrong with the limits typed, or null (always null while not shown). */
  problem(): string | null;
  /** The limits typed differ from those the core will keep with the key. */
  changed(): boolean;
  /**
   * Save the limits, after the key is saved: the core has by then kept the ones shown
   * (the provider's own, or its defaults), so only a change is sent. Null when done.
   */
  save(api: Api): Promise<string | null>;
}

export function createCapStep(prefix: string): CapStep {
  let cost: Cost | null = null;
  let shown = { soft: '', hard: '' };
  const element = el('div', 'spend-step');
  const line = el('p', 'settings-hint');
  const words = el('span');
  const change = button('Change', 'quiet spend-change');
  const dot = el('span', '', ' · ');
  let link: HTMLAnchorElement | null = null;
  line.append(words, change, dot);
  const soft = numberRow('', `${prefix}-soft`);
  const hard = numberRow('', `${prefix}-hard`);
  const plain = el('p', 'settings-hint');
  const fields = el('div', 'spend-caps');
  fields.append(soft.row, hard.row, plain);
  element.append(line, fields);

  const unit = (): Unit => cost?.caps?.unit ?? 'dollars';
  const capped = (): boolean => !!cost?.caps && cost.provider_id !== null;
  const read = (): { soft: number | null | undefined; hard: number | null | undefined } => ({
    soft: fromField(soft.input.value, unit()),
    hard: fromField(hard.input.value, unit()),
  });
  const changed = (): boolean => capped() && (soft.input.value.trim() !== shown.soft || hard.input.value.trim() !== shown.hard);

  function summary(): void {
    if (!cost) return;
    const est = shortEstimate(cost.estimate);
    if (!capped()) {
      words.textContent = est;
      return;
    }
    const r = read();
    const wrong = capsProblem(r.soft, r.hard);
    words.textContent = `${est} ${wrong ? '' : limitsLine(r.soft ?? null, r.hard ?? null, unit())} `.replace(/\s+$/, ' ');
    plain.textContent = wrong ?? noCapLine(cost.name ?? 'this service', r.soft ?? null, r.hard ?? null);
  }
  soft.input.addEventListener('input', summary);
  hard.input.addEventListener('input', summary);
  // Enter in a limit saves nothing by itself, and is no shortcut for the page either.
  fields.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    ev.stopPropagation();
  });
  change.addEventListener('click', () => {
    fields.hidden = false;
    change.hidden = true;
    soft.input.focus();
  });

  function problem(): string | null {
    if (element.hidden || !capped()) return null;
    const r = read();
    return capsProblem(r.soft, r.hard);
  }

  return {
    element,
    problem,
    changed,
    show(next) {
      if (next && cost && next.provider_id === cost.provider_id && changed()) {
        // The same provider again (a model list read once more): what was typed stays.
        cost = next;
        summary();
        return;
      }
      cost = next;
      element.hidden = next === null;
      link?.remove();
      link = next?.pricing_url ? pricesLink('Prices', next.pricing_url) : null;
      if (link) line.append(link);
      const c = next?.caps ?? null;
      const on = capped();
      change.hidden = !on;
      dot.hidden = !link || !on;
      fields.hidden = true;
      const w = A_MONTH[unit()];
      soft.label.textContent = `Warn me at (${w})`;
      hard.label.textContent = `Stop at (${w})`;
      shown = { soft: toField(c?.soft ?? null, unit()), hard: toField(c?.hard ?? null, unit()) };
      soft.input.value = shown.soft;
      hard.input.value = shown.hard;
      plain.textContent = '';
      summary();
    },
    async save(api) {
      const wrong = problem();
      if (wrong) return wrong;
      const pid = cost?.provider_id;
      if (!pid || !changed()) return null;
      const r = read();
      try {
        const response = await api('PUT', capsPath(pid), { soft: r.soft ?? null, hard: r.hard ?? null });
        if (!response.ok) return await spendProblem(response);
      } catch {
        return NOT_ANSWERED;
      }
      shown = { soft: soft.input.value.trim(), hard: hard.input.value.trim() };
      return null;
    },
  };
}

// ── Settings → Usage: a block per service ────────────────────────────────

/** The user's own prices, `{provider_id: {model: rate}}`, as the core keeps them. */
export type Prices = Record<string, Record<string, Record<string, unknown>>>;

// What each leg gives her, and what does it for free. Fixed words of the app's.
const WHAT: Readonly<Record<Leg, string>> = { model: 'How she thinks', voice: 'How she speaks', hearing: 'How she hears' };
const FREE_INSTEAD: Readonly<Record<Leg, string>> = {
  model: 'a local model with Ollama',
  voice: 'this computer’s own voice',
  hearing: 'Whisper on this computer',
};

const WORK_WORDS: Readonly<Record<string, string>> = {
  routines: 'routines',
  asks: 'timed asks',
  watchers: 'watchers',
  channels: 'channel replies',
  titles: 'naming conversations',
  board: 'refreshing your board',
};

function list(words: readonly string[]): string {
  if (words.length < 2) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

/** The day a switch ends, when the core gave no line of its own: "Dec 1". */
export function dayText(ms: number): string {
  return new Date(ms).toLocaleString('en', { month: 'short', day: 'numeric' });
}

const PER_UNIT: Readonly<Record<Exclude<Leg, 'model'>, { unit: 'chars' | 'seconds'; label: string }>> = {
  voice: { unit: 'chars', label: 'Your rate ($ per thousand characters)' },
  hearing: { unit: 'seconds', label: 'Your rate ($ per minute)' },
};

export interface ProviderBlocks {
  readonly element: HTMLElement;
  /** Read prices and usage again and draw every block, with no old messages. */
  open(): Promise<Usage | null>;
}

const keyOf = (p: Pick<ProviderUsage, 'leg' | 'provider_id'>): string => `${p.leg}:${p.provider_id ?? ''}`;

/**
 * One block per service: what it costs, the month so far, its limits and, under More
 * options, a price. The blocks keep the one copy of the user's prices. A save redraws
 * only its own block, so what is typed in another is never lost. `onUsage` hears each
 * fresh read, for the rest of the section.
 */
export function createProviderBlocks(api: Api, load: () => Promise<Usage | null>, onUsage: (u: Usage | null) => void = () => undefined): ProviderBlocks {
  const element = el('div', 'spend-providers');
  const stale = el('p', 'settings-warn');
  stale.setAttribute('role', 'status');
  stale.hidden = true;
  const holder = el('div', 'spend-providers');
  element.append(stale, holder);
  // Per service: its section, what is drawn in it, and its status line. The status
  // line is never redrawn, so a screen reader hears each new message in it.
  const sections = new Map<string, { section: HTMLElement; body: HTMLElement; said: HTMLElement; tail: HTMLElement }>();
  let prices: Prices = {};
  let shown: readonly ProviderUsage[] = [];

  async function readPrices(): Promise<void> {
    try {
      const r = await api('GET', '/api/settings/spend/prices');
      if (!r.ok) return;
      const body = (await r.json()) as { prices?: unknown };
      if (body.prices && typeof body.prices === 'object') prices = body.prices as Prices;
    } catch {
      // The prices kept are the last read.
    }
  }

  function withPrice(pid: string, model: string, rate: Record<string, unknown> | null): Prices {
    const next: Prices = { ...prices };
    const models = { ...(next[pid] ?? {}) };
    if (rate) models[model] = rate;
    else delete models[model];
    if (Object.keys(models).length) next[pid] = models;
    else delete next[pid];
    return next;
  }

  /** Read usage again and draw only this block, with `note` in its status. */
  async function redraw(key: string, note: string): Promise<void> {
    const u = await load();
    onUsage(u);
    const fresh = u?.providers?.find((p) => keyOf(p) === key);
    const parts = sections.get(key);
    if (!u || !fresh) {
      if (parts) parts.said.textContent = `${note} The totals couldn't refresh.`;
      return;
    }
    const focused = parts?.section.contains(document.activeElement) ?? false;
    shown = shown.map((p) => (keyOf(p) === key ? fresh : p));
    draw(fresh, note);
    if (focused) sections.get(key)?.body.querySelector<HTMLButtonElement>('.spend-actions button')?.focus();
  }

  function draw(p: ProviderUsage, note = ''): HTMLElement {
    const key = keyOf(p);
    let parts = sections.get(key);
    if (!parts) {
      parts = { section: el('section', 'spend-provider'), body: el('div', 'spend-body'), said: status(), tail: el('div', 'spend-body') };
      parts.section.append(parts.body, parts.said, parts.tail);
      sections.set(key, parts);
      const said = parts.said;
      const section = parts.section;
      // An edit makes the last message old news, "Saved." above all.
      section.addEventListener('input', () => {
        said.textContent = '';
      });
      // Enter in a field is the block's Save, and is no shortcut for the page.
      section.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' || !(ev.target instanceof HTMLInputElement)) return;
        ev.preventDefault();
        ev.stopPropagation();
        section.querySelector<HTMLButtonElement>('.spend-actions button')?.click();
      });
    }
    const { section, said, tail } = parts;
    const body = parts.body;
    tail.replaceChildren();
    const head = el('div', 'spend-head');
    head.append(el('h3', '', p.name), el('span', 'spend-what', WHAT[p.leg]));
    body.replaceChildren(head);
    const pid = p.provider_id;
    if (pid === null || pid === 'local') {
      body.append(el('p', 'settings-hint', p.estimate || FREE));
      said.textContent = '';
      return section;
    }
    const unit = p.unit;
    body.append(el('p', 'spend-big', `${amountText(p.spent, unit)} this month`));
    body.append(
      el('p', 'settings-hint', `${amountText(p.split.conversation, unit)} talking with you · ${amountText(p.split.background, unit)} in the background`),
    );
    const state = spendNote(p);
    if (state) body.append(el('p', p.state === 'capped' ? 'settings-warn spend-capped' : 'settings-warn', state));
    if (p.unestimated) {
      const what = p.leg === 'model' ? "this model's price" : "this service's rate";
      const counted = `${amountText(p.unestimated.units, p.unestimated.unit)} aren't counted in dollars yet`;
      // With a price of the user's own already given, the price is known: never say otherwise.
      const priced = Object.keys(prices[pid] ?? {}).length > 0;
      body.append(
        el('p', 'settings-warn', priced ? `${counted}. Check the price under More options.` : `${counted}: Trillion doesn't know ${what}. Add it under More options.`),
      );
    }

    // The two limits, and the one Save.
    const w = A_MONTH[unit];
    const soft = numberRow(`Warn me at (${w})`, 'usage-soft');
    const hard = numberRow(`Stop at (${w})`, 'usage-hard');
    soft.input.value = toField(p.soft, unit);
    hard.input.value = toField(p.hard, unit);
    const plain = el('p', 'settings-hint', noCapLine(p.name, p.soft, p.hard));
    const live = (): void => {
      const s = fromField(soft.input.value, unit);
      const h = fromField(hard.input.value, unit);
      plain.textContent = capsProblem(s, h) ?? noCapLine(p.name, s ?? null, h ?? null);
    };
    soft.input.addEventListener('input', live);
    hard.input.addEventListener('input', live);
    body.append(soft.row, hard.row, plain);

    // Cost, the free choice and the prices page, in one line.
    const about = el('p', 'settings-hint', `${shortEstimate(p.estimate)} Free instead: ${FREE_INSTEAD[p.leg]}. `);
    if (p.pricing_url) about.append(pricesLink(`See ${p.name}'s prices`, p.pricing_url));
    tail.append(about);

    // More options: the early heads-up, and a price or a rate.
    const more = el('details', 'spend-more');
    more.open = !!p.unestimated || unit !== 'dollars';
    more.append(el('summary', '', 'More options'));
    const warn = numberRow('Early heads-up at (% of Warn me at)', 'usage-warn');
    warn.input.value = String(p.warn_pct);
    more.append(warn.row);
    let price: { read(): Record<string, unknown> | null | string; model: string; was: string } | null = null;
    if (p.leg === 'model') {
      const model = p.model ?? '*';
      const mine = prices[pid]?.[model];
      if (unit === 'tokens') more.append(el('p', 'settings-hint', "Until it has a price, this model's limits count tokens instead of dollars."));
      more.append(el('p', 'settings-hint', `Your price for ${p.model ?? 'this service'}, in $ per million tokens. Leave both empty to use Trillion's own price, if it has one.`));
      const input = numberRow('Input ($ per million tokens)', 'usage-in');
      const output = numberRow('Output ($ per million tokens)', 'usage-out');
      input.input.value = typeof mine?.['input'] === 'number' ? String(mine['input']) : '';
      output.input.value = typeof mine?.['output'] === 'number' ? String(mine['output']) : '';
      const was = `${input.input.value}|${output.input.value}`;
      more.append(input.row, output.row);
      price = {
        model,
        was,
        read: () => {
          if (`${input.input.value}|${output.input.value}` === was) return 'same';
          const i = fromField(input.input.value, 'dollars');
          const o = fromField(output.input.value, 'dollars');
          if (i === undefined || o === undefined || (i === null) !== (o === null)) return 'Give both prices, in $ per million tokens, or leave both empty.';
          return i === null ? null : { input: i, output: o };
        },
      };
    } else {
      const per = PER_UNIT[p.leg];
      const mine = prices[pid]?.['*'];
      const rate = numberRow(per.label, 'usage-rate');
      rate.input.value = typeof mine?.['per_unit'] === 'number' ? String(mine['per_unit']) : '';
      const was = rate.input.value;
      more.append(el('p', 'settings-hint', 'On a plan? Your own rate wins over the pay-as-you-go one. Leave it empty for the usual rate.'), rate.row);
      price = {
        model: '*',
        was,
        read: () => {
          if (rate.input.value === was) return 'same';
          const r = fromField(rate.input.value, 'dollars');
          if (r === undefined) return 'A rate is dollars of zero or more, or empty for the usual rate.';
          return r === null ? null : { unit: per.unit, per_unit: r };
        },
      };
    }
    // The core's estimate says what it assumes: kept, out of the way.
    if (p.estimate && shortEstimate(p.estimate) !== p.estimate) more.append(el('p', 'settings-hint', `How that is worked out: ${p.estimate}`));

    const actions = el('div', 'spend-actions');
    const save = button('Save');
    actions.append(save);
    save.addEventListener('click', () => {
      const s = fromField(soft.input.value, unit);
      const h = fromField(hard.input.value, unit);
      const pct = Number(warn.input.value.trim());
      const rate = price?.read() ?? 'same';
      const wrong =
        capsProblem(s, h) ??
        (Number.isInteger(pct) && pct >= 1 && pct <= 100 ? null : 'The early heads-up must be between 1 and 100 percent.') ??
        (typeof rate === 'string' && rate !== 'same' ? rate : null);
      if (wrong) {
        said.textContent = wrong;
        return;
      }
      save.disabled = true;
      said.textContent = 'Saving…';
      void (async () => {
        try {
          const response = await api('PUT', capsPath(pid), { soft: s ?? null, hard: h ?? null, warn_pct: pct });
          if (!response.ok) {
            said.textContent = await spendProblem(response);
            return;
          }
          if (rate !== 'same' && price) {
            const r = await api('PUT', '/api/settings/spend/prices', withPrice(pid, price.model, rate as Record<string, unknown> | null));
            if (!r.ok) {
              said.textContent = `The limits are saved, but not the price: ${await spendProblem(r)}`;
              return;
            }
            prices = ((await r.json()) as { prices?: Prices }).prices ?? prices;
          }
          await redraw(key, 'Saved.');
        } catch {
          said.textContent = NOT_ANSWERED;
        } finally {
          save.disabled = false;
        }
      })();
    });
    body.append(actions);
    tail.append(more);

    if (p.paused.length) {
      tail.append(el('p', 'settings-warn', `Paused until the 1st or until you raise Stop at: ${list(p.paused.map((x) => WORK_WORDS[x] ?? x))}.`));
    }
    if (p.switched) {
      const sw = el('p', 'settings-hint spend-switched');
      const words = p.switched.line ?? `Using ${p.switched.label} until ${dayText(p.switched.until_ms)}`;
      sw.append(`${words}. `);
      if (p.state !== 'capped') {
        const back = button('Switch back now', 'quiet');
        back.addEventListener('click', () => {
          back.disabled = true;
          said.textContent = 'Switching back…';
          void (async () => {
            const wrong = await endSwitch(api, pid, p.name);
            back.disabled = false;
            if (wrong) said.textContent = wrong;
            else await redraw(key, `Back on ${p.name}.`);
          })();
        });
        sw.append(back);
        if (note === 'Saved.') note = `Saved. Switch back to ${p.name} now?`;
      }
      tail.append(sw);
    }
    said.textContent = note;
    return section;
  }

  return {
    element,
    async open() {
      await readPrices();
      const u = await load();
      onUsage(u);
      if (!u) {
        // Keep what was read last, and say it is not fresh.
        stale.textContent = shown.length ? "Couldn't refresh: these are the last numbers read." : 'Usage could not be read right now.';
        stale.hidden = false;
        return u;
      }
      stale.textContent = '';
      stale.hidden = true;
      shown = u.providers ?? [];
      sections.clear();
      holder.replaceChildren(...shown.map((p) => draw(p)));
      return u;
    },
  };
}

// ── The spend card ───────────────────────────────────────────────────────

export interface CardOption {
  readonly choice: string;
  readonly amount?: number;
  readonly unit?: Unit;
  readonly label?: string;
  readonly until?: string;
  readonly until_ms?: number;
}

export interface SpendCard {
  readonly id: string;
  readonly provider_id: string;
  readonly name: string;
  readonly leg: Leg;
  readonly cap: number;
  readonly unit: Unit;
  readonly cap_text: string;
  readonly line: string;
  readonly status: 'open' | 'stopped' | 'raised' | 'switched';
  readonly options: readonly CardOption[];
}

/** Why the core turned a choice down, in a short line. */
export function refusal(code: string, card: Pick<SpendCard, 'cap_text'>): string {
  switch (code) {
    case 'bad_amount':
      return `Pick a cap above ${card.cap_text || 'the cap now'} (what you've spent so far).`;
    case 'wrong_session':
      return 'Answer this in the conversation where it appeared.';
    case 'closed':
      return "Already sorted: the cap isn't reached any more.";
    case 'not_a_tap':
      return 'Tap or click a button to choose.';
    case 'unknown_card':
      return 'This card is no longer open.';
    case 'unknown_choice':
      return 'That choice is no longer offered.';
    case 'target_capped':
      return 'That one has reached its own cap now. Choose another.';
    default:
      return 'Something went wrong. Try again.';
  }
}

const STOP_WORDS: Readonly<Record<string, string>> = { stop: 'Not now', text: 'Text only', type: 'Type for now' };
const NOT_SENT = "Not sent: Trillion isn't connected. Try again.";
const NO_ANSWER = 'No answer yet. Try again.';

function cardOf(msg: ServerMsg): SpendCard | null {
  const id = msg['id'];
  if (typeof id !== 'string' || !id || !Array.isArray(msg['options'])) return null;
  const options = (msg['options'] as unknown[]).filter(
    (o): o is CardOption => !!o && typeof o === 'object' && typeof (o as { choice?: unknown }).choice === 'string',
  );
  const str = (k: string): string => (typeof msg[k] === 'string' ? (msg[k] as string) : '');
  const unit = str('unit') as Unit;
  return {
    id,
    provider_id: str('provider_id'),
    name: str('name') || 'This service',
    leg: (['model', 'voice', 'hearing'].includes(str('leg')) ? str('leg') : 'model') as Leg,
    cap: typeof msg['cap'] === 'number' ? msg['cap'] : 0,
    unit: unit in A_MONTH ? unit : 'dollars',
    cap_text: str('cap_text'),
    line: str('line'),
    status: (['open', 'stopped', 'raised', 'switched'].includes(str('status')) ? str('status') : 'open') as SpendCard['status'],
    options,
  };
}

interface Shown {
  card: SpendCard;
  element: HTMLElement;
  /** A choice was sent and is not yet answered. */
  waiting: boolean;
  timer: ReturnType<typeof setTimeout> | null;
  /** Shown whole: open, or opened again with Change after "Not now". */
  whole: boolean;
  setWaiting(on: boolean): void;
  setNote(text: string): void;
  focus(): void;
}

export interface SpendCards {
  /** A message from the core: the cards' own, and `session`, which starts afresh. */
  handle(msg: ServerMsg): void;
  /** A card says this line already, whole or as its banner line: the banner need not. */
  has(line: string): boolean;
  reset(): void;
}

/**
 * The spend cards, drawn in `root` under the banner. A card put off with "Not now"
 * stays there as one line, with Change to choose again. `send` writes to this page's
 * own socket, is called only from a click or Enter in the raise field, and says
 * whether the message went. A choice not answered in `waitMs` can be tried again.
 */
export function createSpendCards(
  root: HTMLElement,
  send: (body: Record<string, unknown>) => boolean,
  waitMs = 10_000,
  resolved: (card: SpendCard, status: string) => void = () => undefined,
): SpendCards {
  const cards = new Map<string, Shown>();

  function whole(s: Shown): HTMLElement {
    const c = s.card;
    const box = el('div', 'card waiting spend-card');
    box.setAttribute('role', 'group');
    const title = el('p', 'card-title', "You've hit your monthly cap");
    title.id = `spend-${c.id}-title`;
    box.setAttribute('aria-labelledby', title.id);
    box.append(title, el('p', 'spend-line', c.line));
    const note = el('p', 'card-note');
    note.setAttribute('role', 'status');
    const controls: (HTMLButtonElement | HTMLInputElement)[] = [];
    let amount: HTMLInputElement | null = null;
    let raiseBtn: HTMLButtonElement | null = null;
    const raise = c.options.find((o) => o.choice === 'raise');
    const unit = raise?.unit ?? c.unit;
    const raiseWords = (): string => {
      const n = fromField(amount?.value ?? '', unit);
      return typeof n === 'number' && n > 0 ? `Raise to ${capText(n, unit)}` : 'Raise';
    };
    const choose = (o: CardOption): void => {
      if (s.waiting) return;
      const body: Record<string, unknown> = { type: 'spend.choice', id: c.id, choice: o.choice };
      if (o.choice === 'raise') {
        const n = fromField(amount?.value ?? '', unit);
        if (typeof n !== 'number' || n <= 0) {
          s.setNote('Type the new cap as a number.');
          amount?.focus();
          return;
        }
        body['amount'] = n;
      }
      s.setWaiting(true);
      s.setNote('Saving your choice…');
      if (!send(body)) {
        s.setWaiting(false);
        s.setNote(NOT_SENT);
        return;
      }
      s.timer = setTimeout(() => {
        if (!s.waiting) return;
        s.setWaiting(false);
        s.setNote(NO_ANSWER);
      }, waitMs);
    };
    if (raise) {
      const row = numberRow(`New cap (${A_MONTH[unit]})`, 'spend-raise');
      row.input.value = toField(raise.amount ?? null, unit);
      amount = row.input;
      controls.push(row.input);
      box.append(row.row);
    }
    const actions = el('div', 'card-actions');
    for (const o of c.options) {
      const b = button(o.choice === 'raise' ? raiseWords() : o.choice.startsWith('switch:') ? `Use ${o.label ?? 'another'} until ${o.until ?? 'the 1st'}` : (STOP_WORDS[o.choice] ?? o.choice), o.choice === 'raise' ? 'approve' : '');
      b.dataset['choice'] = o.choice;
      b.addEventListener('click', () => choose(o));
      if (o.choice === 'raise') raiseBtn = b;
      controls.push(b);
      actions.append(b);
    }
    if (amount && raise) {
      const input = amount;
      input.addEventListener('input', () => {
        if (raiseBtn) raiseBtn.textContent = raiseWords();
      });
      input.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter') return;
        ev.preventDefault();
        // Raise, and nothing else: no shortcut of the page's sees it.
        ev.stopPropagation();
        choose(raise);
      });
    }
    box.append(actions, note);
    s.setWaiting = (on) => {
      s.waiting = on;
      if (!on && s.timer) clearTimeout(s.timer);
      if (!on) s.timer = null;
      for (const x of controls) x.disabled = on;
    };
    s.setNote = (text) => {
      note.textContent = text;
    };
    s.focus = () => (amount ?? controls[0])?.focus();
    return box;
  }

  function stopped(s: Shown): HTMLElement {
    const row = el('div', 'banner spend-stopped');
    const cap = s.card.cap_text || capText(s.card.cap, s.card.unit);
    row.append(el('span', '', `${s.card.name} is stopped at its ${cap} cap. `));
    const change = button('Change', 'quiet');
    change.addEventListener('click', () => {
      s.whole = true;
      place(s);
      s.focus();
    });
    row.append(change);
    s.setWaiting = () => undefined;
    s.setNote = () => undefined;
    s.focus = () => change.focus();
    return row;
  }

  /** Put this card's element in place, whole or as its banner line. */
  function place(s: Shown): void {
    const had = s.element.contains(document.activeElement);
    const next = s.whole ? whole(s) : stopped(s);
    if (s.element.parentNode === root) root.replaceChild(next, s.element);
    else root.append(next);
    s.element = next;
    root.hidden = cards.size === 0;
    if (had) s.focus();
  }

  function remove(id: string): void {
    const s = cards.get(id);
    if (!s) return;
    if (s.timer) clearTimeout(s.timer);
    s.element.remove();
    cards.delete(id);
    root.hidden = cards.size === 0;
  }

  function reset(): void {
    for (const id of [...cards.keys()]) remove(id);
    root.replaceChildren();
    root.hidden = true;
  }

  reset();
  return {
    reset,
    has: (line) => !!line && [...cards.values()].some((s) => s.card.line === line),
    handle(msg) {
      if (msg.type === 'session') {
        // Another connection, maybe another conversation: the core sends its cards again.
        reset();
        return;
      }
      const id = typeof msg['id'] === 'string' ? msg['id'] : '';
      if (msg.type === 'spend.card') {
        const card = cardOf(msg);
        if (!card) return;
        if (card.status === 'raised' || card.status === 'switched') {
          remove(card.id);
          return;
        }
        const was = cards.get(card.id);
        if (was) {
          // The same card again: drawn afresh only if what it says changed.
          const same = JSON.stringify(was.card) === JSON.stringify(card);
          was.card = card;
          was.whole = card.status === 'open' || was.whole;
          if (!same) place(was);
          return;
        }
        const s: Shown = {
          card,
          element: el('div'),
          waiting: false,
          timer: null,
          whole: card.status === 'open',
          setWaiting: () => undefined,
          setNote: () => undefined,
          focus: () => undefined,
        };
        cards.set(card.id, s);
        place(s);
        return;
      }
      const s = cards.get(id);
      if (!s) return;
      if (msg.type === 'spend.resolved') {
        const now = msg['status'];
        resolved(s.card, String(now));
        if (now === 'raised' || now === 'switched') remove(id);
        else if (now === 'stopped') {
          s.setWaiting(false);
          s.card = { ...s.card, status: 'stopped' };
          s.whole = false;
          place(s);
        } else s.setWaiting(false);
      } else if (msg.type === 'spend.refused') {
        s.setWaiting(false);
        s.setNote(refusal(String(msg['code'] ?? ''), s.card));
      }
    },
  };
}

// ── The switch line under the banner ─────────────────────────────────────

export interface SwitchShown {
  readonly provider_id: string;
  readonly line: string;
}

export interface SwitchLines {
  /** Draw these switches; `about` says each provider's name and whether it is capped. */
  render(switches: readonly SwitchShown[], about: (pid: string) => { name: string; capped: boolean }): void;
  /** A cap was raised on this provider: if it has a switch, ask whether to go back. */
  raised(pid: string): void;
}

/** Each switch made at a cap, while it lasts, with "Switch back now" once it can. */
export function createSwitchLines(root: HTMLElement, api: Api): SwitchLines {
  const said = new Map<string, string>();
  const asking = new Set<string>();
  let last: { switches: readonly SwitchShown[]; about: (pid: string) => { name: string; capped: boolean } } = { switches: [], about: () => ({ name: '', capped: false }) };
  let key = '';

  function draw(): void {
    const { switches, about } = last;
    const facts = switches.map((s) => ({ ...s, ...about(s.provider_id), note: said.get(s.provider_id) ?? '', ask: asking.has(s.provider_id) }));
    const next = JSON.stringify(facts);
    if (next === key) return;
    key = next;
    const focused = (document.activeElement as HTMLElement | null)?.dataset?.['back'];
    root.replaceChildren(
      ...facts.map((f) => {
        const row = el('p', 'banner spend-switch');
        const words = /[.!?]$/.test(f.line) ? f.line : `${f.line}.`;
        row.append(el('span', '', `${words} ${f.ask && !f.capped ? `Switch back to ${f.name} now? ` : ''}`));
        if (!f.capped) {
          const back = button('Switch back now', 'quiet');
          back.dataset['back'] = f.provider_id;
          back.addEventListener('click', () => {
            back.disabled = true;
            void endSwitch(api, f.provider_id, f.name).then((wrong) => {
              back.disabled = false;
              asking.delete(f.provider_id);
              if (wrong) said.set(f.provider_id, wrong);
              else said.delete(f.provider_id);
              draw();
            });
          });
          row.append(back);
        }
        const note = status('spend-status');
        note.textContent = f.note;
        row.append(note);
        return row;
      }),
    );
    root.hidden = facts.length === 0;
    if (focused) root.querySelector<HTMLButtonElement>(`[data-back="${CSS.escape(focused)}"]`)?.focus();
  }

  root.hidden = true;
  return {
    render(switches, about) {
      for (const pid of [...said.keys()]) if (!switches.some((s) => s.provider_id === pid)) said.delete(pid);
      last = { switches, about };
      draw();
    },
    raised(pid) {
      if (!last.switches.some((s) => s.provider_id === pid)) return;
      asking.add(pid);
      key = '';
      draw();
    },
  };
}

/** A message that puts something about a spend limit on screen: the sheet must show it. */
export function spendReveals(msg: ServerMsg): boolean {
  return msg.type === 'spend.card' || (msg.type === 'error' && msg['code'] === 'capped');
}

/**
 * At her voice's cap the core may ask for its line to be said in the computer's own
 * voice (`say: "system"`). Never when muted. True when it was said.
 */
export function speakCapped(msg: ServerMsg, muted: boolean, synth: Pick<SpeechSynthesis, 'speak'> | undefined = globalThis.speechSynthesis): boolean {
  if (msg.type !== 'error' || msg['code'] !== 'capped' || msg['say'] !== 'system' || muted || !synth) return false;
  const line = typeof msg['sentence'] === 'string' ? msg['sentence'] : '';
  if (!line || typeof SpeechSynthesisUtterance === 'undefined') return false;
  synth.speak(new SpeechSynthesisUtterance(line));
  return true;
}
