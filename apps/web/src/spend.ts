/**
 * Spend caps on the page (BRN-7, BRN-8, ONB-9, ONB-10): the caps in each key step, a
 * block per provider in Settings → Usage, and the spend card at a hard cap.
 *
 * The card is the app's, not a pending action. A choice is sent only by a click or a
 * tap, as a `spend.choice` message on this page's own socket (SPN-14): it is about
 * money, and the core takes it only from the conversation it showed the card to.
 * Every piece of text is set with textContent, never parsed as markup.
 */

import type { Api } from './settings';
import { type Leg, type ProviderUsage, type Unit, amountText, capText, spendNote } from './usage';
import type { ServerMsg } from './view';

/** What a choice that runs on this computer says, here and in the core (ONB-10). */
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

function pricingLink(): HTMLAnchorElement {
  const a = el('a', '', 'Pricing page');
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
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

const FIELD_UNIT: Readonly<Record<Unit, string>> = { dollars: '$', tokens: 'tokens', chars: 'characters', seconds: 'minutes' };

/** A cap as a field shows it: hearing caps, kept in seconds, are typed in minutes. */
export function toField(n: number | null, unit: Unit): string {
  if (n === null) return '';
  const shown = unit === 'seconds' ? n / 60 : n;
  return String(Math.round(shown * 100) / 100);
}

/** A field's text as the core's amount: null when empty, undefined when it is not one. */
export function fromField(text: string, unit: Unit): number | null | undefined {
  const t = text.trim().replace(/^\$/, '').replace(/,/g, '');
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < 0) return undefined;
  return unit === 'seconds' ? Math.round(n * 60) : n;
}

// ── Errors in plain words ────────────────────────────────────────────────

const CAP_ERRORS: Readonly<Record<string, string>> = {
  hard_below_soft: 'The hard cap must be at least the soft cap.',
  too_high: 'That is higher than a cap can go.',
  bad_unit: 'That unit does not fit this provider.',
  no_price: 'Nothing prices this provider yet, so its caps count units. Add a price first.',
  never_capped: 'This runs on this computer and is never capped.',
  unknown_provider: 'That provider is not set up.',
  bad_price: 'That price is not one Trillion can use. Prices are dollars of zero or more.',
  bad_model: 'That model name cannot be priced.',
  too_many: 'There are too many prices saved. Remove some first.',
  zero_price: 'A price of zero would stop this model being counted. Leave it empty instead.',
};

const FIELD_WORDS: Readonly<Record<string, string>> = { soft: 'the soft cap', hard: 'the hard cap', warn_pct: 'the warning percentage' };

const NOT_ANSWERED = 'Trillion did not answer. Is it still running?';
const NUMBERS = 'Caps are numbers of zero or more, or empty for no cap.';

/** The core's refusal of a cap or a price, in plain words. */
export async function spendProblem(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown; fields?: unknown };
    const code = typeof body.error === 'string' ? body.error : '';
    if (code === 'bad_request') {
      const named = (Array.isArray(body.fields) ? body.fields : []).map((f) => FIELD_WORDS[String(f)]).filter(Boolean);
      return named.length ? `Check ${named.join(' and ')}.` : `${NUMBERS} The warning is 1 to 100 percent.`;
    }
    return CAP_ERRORS[code] ?? 'That did not work. Try again.';
  } catch {
    return 'That did not work. Try again.';
  }
}

/** What is wrong with these caps before anything is sent, or null. */
function capsProblem(soft: number | null | undefined, hard: number | null | undefined): string | null {
  if (soft === undefined || hard === undefined) return NUMBERS;
  if (soft !== null && hard !== null && hard < soft) return CAP_ERRORS['hard_below_soft'] ?? '';
  return null;
}

function noCapLine(name: string, soft: number | null, hard: number | null): string {
  if (hard !== null) return '';
  return `No hard cap: nothing stops spending on ${name}.${soft === null ? ' No soft cap either, so nothing warns you.' : ''}`;
}

const capsPath = (pid: string): string => `/api/settings/spend/${encodeURIComponent(pid)}`;

// ── The caps in a key step (ONB-9, ONB-10) ───────────────────────────────

export interface CapStep {
  readonly element: HTMLElement;
  /** Show this provider's cost line, pricing page and the caps it will have. */
  show(cost: Cost | null): void;
  /** What is wrong with the caps typed, or null. */
  problem(): string | null;
  /** The caps typed differ from those the core will keep with the key. */
  changed(): boolean;
  /**
   * Save the caps, after the key is saved: the core has by then kept the caps shown
   * (the provider's own, or its defaults), so only a change is sent. Null when done.
   */
  save(api: Api): Promise<string | null>;
}

export function createCapStep(prefix: string): CapStep {
  let cost: Cost | null = null;
  let shown = { soft: '', hard: '' };
  const element = el('div', 'spend-step');
  const line = el('p', 'settings-hint');
  const link = pricingLink();
  const soft = numberRow('', `${prefix}-soft`);
  const hard = numberRow('', `${prefix}-hard`);
  const hint = el('p', 'settings-hint');
  const caps = el('div', 'spend-caps');
  caps.append(soft.row, hard.row, hint);
  element.append(line, link, caps);

  const unit = (): Unit => cost?.caps?.unit ?? 'dollars';
  const read = (): { soft: number | null | undefined; hard: number | null | undefined } => ({
    soft: fromField(soft.input.value, unit()),
    hard: fromField(hard.input.value, unit()),
  });

  function note(): void {
    const r = read();
    hint.textContent =
      capsProblem(r.soft, r.hard) ??
      (noCapLine(cost?.name ?? 'this provider', r.soft ?? null, r.hard ?? null) ||
        'A warning at the soft cap, and a stop at the hard cap. Each month starts again on the 1st.');
  }
  soft.input.addEventListener('input', note);
  hard.input.addEventListener('input', note);

  function problem(): string | null {
    if (caps.hidden) return null;
    const r = read();
    return capsProblem(r.soft, r.hard);
  }
  const changed = (): boolean => !caps.hidden && (soft.input.value.trim() !== shown.soft || hard.input.value.trim() !== shown.hard);

  return {
    element,
    problem,
    changed,
    show(next) {
      if (next && cost && next.provider_id === cost.provider_id && changed()) {
        // The same provider again (a model list read once more): what was typed stays.
        cost = next;
        line.textContent = next.estimate;
        return;
      }
      cost = next;
      element.hidden = next === null;
      line.textContent = next?.estimate ?? '';
      link.hidden = !next?.pricing_url;
      if (next?.pricing_url) link.href = next.pricing_url;
      const c = next?.caps ?? null;
      caps.hidden = c === null || next?.provider_id === null;
      const words = FIELD_UNIT[unit()];
      soft.label.textContent = `Soft cap a month (${words})`;
      hard.label.textContent = `Hard cap a month (${words})`;
      shown = { soft: toField(c?.soft ?? null, unit()), hard: toField(c?.hard ?? null, unit()) };
      soft.input.value = shown.soft;
      hard.input.value = shown.hard;
      note();
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

// ── Settings → Usage: a block per provider ───────────────────────────────

/** The user's own prices, `{provider_id: {model: rate}}`, as the core keeps them. */
export type Prices = Record<string, Record<string, Record<string, unknown>>>;

// What each leg gives her, and what does it for free (ONB-9). Fixed words of the app's.
const UNLOCKS: Readonly<Record<Leg, string>> = { model: 'how she thinks', voice: 'her voice', hearing: 'how she hears you' };
const FREE_OPTION: Readonly<Record<Leg, string>> = {
  model: 'a local model with Ollama',
  voice: 'this computer’s own voice',
  hearing: 'Whisper on this computer',
};

const WORK_WORDS: Readonly<Record<string, string>> = {
  routines: 'routines',
  asks: 'timed asks',
  watchers: 'watchers',
  channels: 'channel replies',
  titles: 'conversation titles',
  board: 'board refreshes',
};

function list(words: readonly string[]): string {
  if (words.length < 2) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

/** The day a switch ends, as the banner says it: "Dec 1". */
export function dayText(ms: number): string {
  return new Date(ms).toLocaleString('en', { month: 'short', day: 'numeric' });
}

/** "Anthropic: how she thinks. Roughly ... Free option: a local model with Ollama." */
export function costLine(p: Pick<ProviderUsage, 'provider_id' | 'leg' | 'name' | 'estimate'>): string {
  if (p.provider_id === null || p.provider_id === 'local') return p.estimate || FREE;
  return `${p.name}: ${UNLOCKS[p.leg]}. ${p.estimate} Free option: ${FREE_OPTION[p.leg]}.`;
}

const PER_UNIT: Readonly<Record<Exclude<Leg, 'model'>, { unit: 'chars' | 'seconds'; label: string }>> = {
  voice: { unit: 'chars', label: 'Your rate, $ per thousand characters' },
  hearing: { unit: 'seconds', label: 'Your rate, $ per minute' },
};

const PRICE_HINT = 'Leave both empty to use the built-in price, if there is one.';

export interface ProviderBlocks {
  readonly element: HTMLElement;
  /** Draw every provider again, keeping each one's last message. */
  render(providers: readonly ProviderUsage[], prices: Prices): void;
}

/**
 * One block per provider: what it costs, the month so far, its caps and a price.
 * `reload` reads usage again once a price changes what is counted.
 */
export function createProviderBlocks(api: Api, reload: () => Promise<void>): ProviderBlocks {
  const element = el('div', 'spend-providers');
  const notes = new Map<string, string>();
  let prices: Prices = {};

  async function savePrices(next: Prices): Promise<string | null> {
    try {
      const response = await api('PUT', '/api/settings/spend/prices', next);
      if (!response.ok) return await spendProblem(response);
      prices = ((await response.json()) as { prices?: Prices }).prices ?? next;
      return null;
    } catch {
      return NOT_ANSWERED;
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

  // Each provider keeps its element across a redraw, so its place on the page holds.
  const sections = new Map<string, HTMLElement>();

  function block(p: ProviderUsage): HTMLElement {
    const key = `${p.leg}:${p.provider_id ?? ''}`;
    const section = sections.get(key) ?? el('section', 'spend-provider');
    sections.set(key, section);
    section.replaceChildren(el('h3', '', p.name), el('p', 'settings-hint', costLine(p)));
    const pid = p.provider_id;
    if (pid === null || pid === 'local') return section;

    const status = el('p', 'settings-status', notes.get(key) ?? '');
    status.setAttribute('role', 'status');
    const say = (text: string): void => {
      notes.set(key, text);
      status.textContent = text;
    };
    const unit = p.unit;
    section.append(
      el(
        'p',
        '',
        `This month: ${amountText(p.spent, unit)} (conversation ${amountText(p.split.conversation, unit)}, background ${amountText(p.split.background, unit)}).`,
      ),
    );
    const state = spendNote(p);
    if (state) section.append(el('p', p.state === 'capped' ? 'settings-warn spend-capped' : 'settings-warn', state));
    if (p.pricing_url) {
      const link = pricingLink();
      link.href = p.pricing_url;
      section.append(link);
    }

    // Caps.
    const words = FIELD_UNIT[unit];
    const soft = numberRow(`Soft cap a month (${words})`, 'usage-soft');
    const hard = numberRow(`Hard cap a month (${words})`, 'usage-hard');
    const warn = numberRow('Warn at (% of the soft cap)', 'usage-warn');
    soft.input.value = toField(p.soft, unit);
    hard.input.value = toField(p.hard, unit);
    warn.input.value = String(p.warn_pct);
    const plain = el('p', 'settings-hint', noCapLine(p.name, p.soft, p.hard));
    const saveCaps = el('button', '', 'Save caps');
    saveCaps.type = 'button';
    saveCaps.addEventListener('click', () => {
      const s = fromField(soft.input.value, unit);
      const h = fromField(hard.input.value, unit);
      const w = Number(warn.input.value.trim());
      const wrong = capsProblem(s, h) ?? (Number.isInteger(w) && w >= 1 && w <= 100 ? null : 'The warning must be between 1 and 100 percent.');
      if (wrong) {
        say(wrong);
        return;
      }
      saveCaps.disabled = true;
      say('Saving…');
      void (async () => {
        try {
          const response = await api('PUT', capsPath(pid), { soft: s ?? null, hard: h ?? null, warn_pct: w });
          if (!response.ok) {
            say(await spendProblem(response));
            return;
          }
          notes.set(key, 'Caps saved.');
          await reload();
        } catch {
          say(NOT_ANSWERED);
        } finally {
          saveCaps.disabled = false;
        }
      })();
    });
    section.append(soft.row, hard.row, warn.row, plain, saveCaps);

    // A price for a model nothing prices; a rate override for a voice or for hearing.
    if (p.unestimated) {
      section.append(el('p', 'settings-warn', `${amountText(p.unestimated.units, p.unestimated.unit)} not counted: add a ${p.leg === 'model' ? 'price' : 'rate'}.`));
    }
    if (p.leg === 'model') {
      const model = p.model ?? '*';
      const mine = prices[pid]?.[model];
      if (mine || p.unit !== 'dollars' || p.unestimated) {
        const input = numberRow('Input, $ per million tokens', 'usage-in');
        const output = numberRow('Output, $ per million tokens', 'usage-out');
        input.input.value = typeof mine?.['input'] === 'number' ? String(mine['input']) : '';
        output.input.value = typeof mine?.['output'] === 'number' ? String(mine['output']) : '';
        const savePrice = el('button', '', 'Save price');
        savePrice.type = 'button';
        savePrice.addEventListener('click', () => {
          const i = fromField(input.input.value, 'dollars');
          const o = fromField(output.input.value, 'dollars');
          if (i === undefined || o === undefined || (i === null) !== (o === null)) {
            say('Give both prices, in dollars per million tokens, or leave both empty.');
            return;
          }
          void (async () => {
            savePrice.disabled = true;
            const wrong = await savePrices(withPrice(pid, model, i === null ? null : { input: i, output: o }));
            savePrice.disabled = false;
            if (wrong) {
              say(wrong);
              return;
            }
            notes.set(key, 'Price saved.');
            await reload();
          })();
        });
        section.append(
          el('p', 'settings-hint', `Your price for ${p.model ?? 'this provider'}. ${PRICE_HINT}`),
          input.row,
          output.row,
          savePrice,
        );
      }
    } else {
      const per = PER_UNIT[p.leg];
      const mine = prices[pid]?.['*'];
      const rate = numberRow(per.label, 'usage-rate');
      rate.input.value = typeof mine?.['per_unit'] === 'number' ? String(mine['per_unit']) : '';
      const saveRate = el('button', '', 'Save rate');
      saveRate.type = 'button';
      saveRate.addEventListener('click', () => {
        const r = fromField(rate.input.value, 'dollars');
        if (r === undefined) {
          say('A rate is a number of dollars of zero or more, or empty for the usual rate.');
          return;
        }
        void (async () => {
          saveRate.disabled = true;
          const wrong = await savePrices(withPrice(pid, '*', r === null ? null : { unit: per.unit, per_unit: r }));
          saveRate.disabled = false;
          if (wrong) {
            say(wrong);
            return;
          }
          notes.set(key, 'Rate saved.');
          await reload();
        })();
      });
      section.append(el('p', 'settings-hint', 'On a plan? Your own rate wins over the pay-as-you-go one. Leave it empty for the usual rate.'), rate.row, saveRate);
    }

    if (p.paused.length) {
      section.append(el('p', 'settings-warn', `Paused until the cap lifts: ${list(p.paused.map((w) => WORK_WORDS[w] ?? w))}.`));
    }
    if (p.switched) {
      // The core has no way to end a switch early yet: it ends on the 1st.
      section.append(el('p', 'settings-hint', `Using ${p.switched.label} in your conversations until ${dayText(p.switched.until_ms)}.`));
    }
    section.append(status);
    return section;
  }

  return {
    element,
    render(providers, next) {
      prices = next;
      element.replaceChildren(...providers.map(block));
    },
  };
}

// ── The spend card (BRN-8) ───────────────────────────────────────────────

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
  readonly leg: Leg;
  readonly cap: number;
  readonly unit: Unit;
  readonly cap_text: string;
  readonly line: string;
  readonly status: 'open' | 'stopped' | 'raised' | 'switched';
  readonly options: readonly CardOption[];
}

/** Why the core turned a choice down, in a short line. */
export const REFUSALS: Readonly<Record<string, string>> = {
  unknown_card: 'This card is no longer open.',
  wrong_session: 'This card belongs to another conversation. Answer it there.',
  not_a_tap: 'Only a tap or a click can answer this.',
  closed: 'This card is closed: the cap is no longer reached.',
  unknown_choice: 'That choice is no longer offered.',
  target_capped: 'That one has reached its own cap now. Choose another.',
  bad_amount: 'The new cap must be above the cap now and what is spent so far.',
};

const STOP_WORDS: Readonly<Record<string, string>> = { stop: 'Stop for now', text: 'Text only', type: 'Type for now' };

const NAMES: Readonly<Record<string, string>> = { anthropic: 'Anthropic', openai: 'OpenAI', elevenlabs: 'ElevenLabs', deepgram: 'Deepgram' };
const HOSTS: Readonly<Record<string, string>> = { 'openrouter.ai': 'OpenRouter', 'api.deepseek.com': 'DeepSeek', 'api.openai.com': 'OpenAI' };

/** A provider as the user knows it, as the core names it. */
export function friendly(pid: string): string {
  if (NAMES[pid]) return NAMES[pid];
  const [kind, ...rest] = pid.split(':');
  const host = rest.join(':');
  if (!host) return pid;
  const name = HOSTS[host] ?? host;
  return kind === 'openai-voice' ? `${name} (voice)` : name;
}

function optionLabel(o: CardOption): string {
  if (o.choice === 'raise') return 'Raise the cap';
  if (o.choice.startsWith('switch:')) return `Use ${o.label ?? 'another'} until ${o.until ?? 'the 1st'}`;
  return STOP_WORDS[o.choice] ?? o.choice;
}

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
    leg: (['model', 'voice', 'hearing'].includes(str('leg')) ? str('leg') : 'model') as Leg,
    cap: typeof msg['cap'] === 'number' ? msg['cap'] : 0,
    unit: unit in FIELD_UNIT ? unit : 'dollars',
    cap_text: str('cap_text'),
    line: str('line'),
    status: (['open', 'stopped', 'raised', 'switched'].includes(str('status')) ? str('status') : 'open') as SpendCard['status'],
    options,
  };
}

interface Shown {
  card: SpendCard;
  /** A choice was sent and is not yet answered. */
  waiting: boolean;
  /** Opened again from the banner after "stop for now". */
  expanded: boolean;
  note: string;
  amount: string | null;
}

export interface SpendCards {
  /** A message from the core: the cards' own, and `session`, which starts afresh. */
  handle(msg: ServerMsg): void;
  reset(): void;
}

/**
 * The spend cards, drawn in `root` under the banner. A card put off with "stop for
 * now" stays as a line there, with a button to choose again. `send` writes to this
 * page's own socket, and is called only from a click.
 */
export function createSpendCards(root: HTMLElement, send: (body: Record<string, unknown>) => void): SpendCards {
  const live = el('div', 'spend-live');
  live.setAttribute('aria-live', 'polite');
  root.replaceChildren(live);
  const cards = new Map<string, Shown>();

  function choose(s: Shown, o: CardOption, amountInput: HTMLInputElement | null): void {
    if (s.waiting) return;
    const body: Record<string, unknown> = { type: 'spend.choice', id: s.card.id, choice: o.choice };
    if (o.choice === 'raise') {
      const unit = o.unit ?? s.card.unit;
      const amount = fromField(amountInput?.value ?? '', unit);
      if (amount === undefined || amount === null || amount <= 0) {
        s.note = 'Type the new cap as a number.';
        draw();
        return;
      }
      body['amount'] = amount;
    }
    s.waiting = true;
    s.note = 'One moment…';
    draw();
    send(body);
  }

  function full(s: Shown): HTMLElement {
    const box = el('div', 'card waiting spend-card');
    box.setAttribute('role', 'group');
    const title = el('p', 'card-title', 'Spending cap');
    title.id = `spend-${s.card.id}-title`;
    box.setAttribute('aria-labelledby', title.id);
    box.append(title, el('p', 'spend-line', s.card.line));
    let amountInput: HTMLInputElement | null = null;
    const raise = s.card.options.find((o) => o.choice === 'raise');
    if (raise) {
      const unit = raise.unit ?? s.card.unit;
      const row = numberRow(`New hard cap (${FIELD_UNIT[unit]})`, 'spend-raise');
      row.input.value = s.amount ?? toField(raise.amount ?? null, unit);
      row.input.disabled = s.waiting;
      row.input.addEventListener('input', () => {
        s.amount = row.input.value;
      });
      amountInput = row.input;
      box.append(row.row);
    }
    const actions = el('div', 'card-actions');
    for (const o of s.card.options) {
      const b = el('button', o.choice === 'raise' ? 'approve' : '', optionLabel(o));
      b.type = 'button';
      b.disabled = s.waiting;
      b.addEventListener('click', () => choose(s, o, amountInput));
      actions.append(b);
    }
    box.append(actions);
    if (s.note) box.append(el('p', 'card-note', s.note));
    return box;
  }

  function stopped(s: Shown): HTMLElement {
    const row = el('div', 'banner spend-stopped');
    const name = s.card.provider_id ? friendly(s.card.provider_id) : 'This provider';
    const cap = s.card.cap_text || capText(s.card.cap, s.card.unit);
    row.append(el('span', '', `${name} is stopped at its ${cap} cap. `));
    const again = el('button', 'quiet', 'Choose again');
    again.type = 'button';
    again.addEventListener('click', () => {
      s.expanded = true;
      s.note = '';
      draw();
      live.querySelector<HTMLButtonElement>(`[aria-labelledby="spend-${CSS.escape(s.card.id)}-title"] button`)?.focus();
    });
    row.append(again);
    return row;
  }

  function draw(): void {
    const nodes: HTMLElement[] = [];
    for (const s of cards.values()) nodes.push(s.card.status === 'stopped' && !s.expanded ? stopped(s) : full(s));
    live.replaceChildren(...nodes);
    root.hidden = nodes.length === 0;
  }

  function reset(): void {
    cards.clear();
    draw();
  }

  draw();
  return {
    reset,
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
        if (card.status === 'raised' || card.status === 'switched') cards.delete(card.id);
        else cards.set(card.id, { card, waiting: false, expanded: false, note: '', amount: null });
        draw();
        return;
      }
      const s = cards.get(id);
      if (!s) return;
      if (msg.type === 'spend.resolved') {
        const status = msg['status'];
        if (status === 'stopped') {
          s.card = { ...s.card, status: 'stopped' };
          s.waiting = false;
          s.expanded = false;
          s.note = '';
        } else if (status === 'raised' || status === 'switched') {
          cards.delete(id);
        } else if (status === 'open') {
          s.waiting = false;
        }
        draw();
      } else if (msg.type === 'spend.refused') {
        s.waiting = false;
        s.note = REFUSALS[String(msg['code'] ?? '')] ?? 'That did not go through. Try again.';
        draw();
      }
    },
  };
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
