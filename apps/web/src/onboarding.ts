/**
 * The first conversation: who the user is and what they call her (ONB-3).
 *
 * A pure state machine, with no DOM and no network, so every step can be tested.
 * Her lines here are authored, not composed: on a first run there may be no model
 * to compose anything, and the names are written by the app, never by her (PER-2).
 * She says so herself at the end.
 */

export type Directness = 'blunt' | 'balanced' | 'gentle';

export interface Profile {
  readonly userName: string;
  readonly agentName: string;
  readonly timezone: string;
  readonly directness: Directness;
}

export type Step = 'user_name' | 'agent_name' | 'directness' | 'saving' | 'done' | 'skipped';

export interface Line {
  readonly who: 'her' | 'you';
  readonly text: string;
}

/** A reply the user can give with one click instead of typing it. */
export interface Choice {
  readonly label: string;
  readonly value: string;
}

export interface Onboarding {
  readonly step: Step;
  readonly draft: Profile;
  /** The name she answers to while this runs: the one she had when it began. */
  readonly was: string;
  /** True when the user came back to change something, false on a first run. */
  readonly returning: boolean;
  readonly lines: readonly Line[];
}

export const NAME_LIMIT = 40;
export const SKIP = '\u0000skip';
const KEEP = '\u0000keep';

const her = (text: string): Line => ({ who: 'her', text });
const you = (text: string): Line => ({ who: 'you', text });

const INTRO = /^(?:hi[,!. ]+|hey[,!. ]+|hello[,!. ]+)?(?:i'?m|i am|my name is|my name'?s|name'?s|it'?s|this is|(?:you can |just )?call me)\s+/i;
const RENAME = /^(?:i(?:'ll| will) call you|(?:let'?s )?call you|let'?s go with|how about|you(?:'re| are)|your name is|go with)\s+/i;
const KEEP_IT = /^(?:keep(?: it)?|same|stay|no change|default|(?:that|it|this)(?:'s| is) (?:fine|good)|fine|yes|yep|ok(?:ay)?)[.! ]*$/i;

/** "I'm Sam." and "call me Sam" both mean Sam. */
export function nameFrom(text: string, lead: RegExp = INTRO): string {
  return text
    .trim()
    .replace(lead, '')
    .replace(/[.!,;:\s]+$/, '')
    .replace(/\s+/g, ' ');
}

/** Why a name cannot be used, in her words, or null if it can. The core checks again. */
export function nameProblem(name: string): string | null {
  if (!name) return 'I need something to go on. Any name will do.';
  if ([...name].length > NAME_LIMIT) return 'Forty characters is the limit. Got a shorter one?';
  if (/[{}<>]/.test(name) || /^["'#-]/.test(name)) return 'Plain characters, please. No brackets, braces or leading punctuation.';
  return null;
}

export function directnessFrom(text: string): Directness | null {
  const t = text.toLowerCase();
  if (/gentl|soft|kind|easy|nice/.test(t)) return 'gentle';
  if (/balanc|middle|between|normal|default|as you are|either|whatever/.test(t)) return 'balanced';
  if (/blunt|straight|direct|harsh|honest|brutal/.test(t)) return 'blunt';
  return null;
}

/** Start. `current` is what the core has now; `userName` is empty on a first run. */
export function begin(current: Profile): Onboarding {
  const returning = current.userName !== '';
  const opening = returning
    ? `Changing names, then. You're ${current.userName} at the moment. Still that, or something new?`
    : `I'm ${current.agentName}. Before anything else: what should I call you?`;
  return { step: 'user_name', draft: current, was: current.agentName, returning, lines: [her(opening)] };
}

export function choices(o: Onboarding): readonly Choice[] {
  const skip: readonly Choice[] = o.returning ? [] : [{ label: 'Skip for now', value: SKIP }];
  switch (o.step) {
    case 'user_name':
      return o.returning ? [{ label: `Still ${o.draft.userName}`, value: KEEP }] : skip;
    case 'agent_name':
      return [{ label: `Keep ${o.draft.agentName}`, value: KEEP }, ...skip];
    case 'directness':
      return [
        { label: 'Blunt', value: 'blunt' },
        { label: 'Balanced', value: 'balanced' },
        { label: 'Gentler', value: 'gentle' },
        ...skip,
      ];
    default:
      return [];
  }
}

export function placeholder(o: Onboarding): string {
  switch (o.step) {
    case 'user_name':
      return 'Your name';
    case 'agent_name':
      return `A name for her, or keep ${o.draft.agentName}`;
    case 'directness':
      return 'Blunt, balanced or gentler';
    default:
      return '';
  }
}

export const active = (o: Onboarding | null): o is Onboarding =>
  o !== null && (o.step === 'user_name' || o.step === 'agent_name' || o.step === 'directness');

function add(o: Onboarding, patch: Partial<Onboarding>, ...lines: Line[]): Onboarding {
  return { ...o, ...patch, lines: [...o.lines, ...lines] };
}

/** What the user typed or clicked at the current step. */
export function answer(o: Onboarding, text: string): Onboarding {
  if (!active(o)) return o;
  if (text === SKIP) {
    return add(
      o,
      { step: 'skipped' },
      you('Skip for now'),
      her(`Fine. I stay ${o.was} and you stay "you" until you say otherwise. The Names button is at the top.`),
    );
  }
  const kept = text === KEEP;
  switch (o.step) {
    case 'user_name': {
      const name = kept ? o.draft.userName : nameFrom(text);
      const said = you(kept ? `Still ${name}` : text.trim());
      const problem = nameProblem(name);
      if (problem) return add(o, {}, said, her(problem));
      const ask = o.returning
        ? `${name}. And I'm ${o.draft.agentName}. Keep that, or rename me?`
        : `${name}. Noted. Your turn: "${o.draft.agentName}" is the name I came with. Keep it, or call me something else?`;
      return add(o, { step: 'agent_name', draft: { ...o.draft, userName: name } }, said, her(ask));
    }
    case 'agent_name': {
      const keep = kept || KEEP_IT.test(text.trim());
      const name = keep ? o.draft.agentName : nameFrom(text, RENAME);
      const said = you(kept ? `Keep ${name}` : text.trim());
      const problem = nameProblem(name);
      if (problem) return add(o, {}, said, her(problem));
      const taken = name === o.draft.agentName ? `${name} it stays.` : `${name}. I can work with that.`;
      const ask = o.returning
        ? `${taken} Tone is set to ${o.draft.directness}. Blunt, balanced, or gentler?`
        : `${taken} Last one. I'm dry by default and I aim it at ideas, never at you. Want me blunt, balanced, or gentler?`;
      return add(o, { step: 'directness', draft: { ...o.draft, agentName: name } }, said, her(ask));
    }
    case 'directness': {
      const picked = directnessFrom(text);
      const label = picked === 'gentle' ? 'Gentler' : picked === 'blunt' ? 'Blunt' : 'Balanced';
      const typed = text.trim();
      const said = you(typed === picked ? label : typed);
      if (!picked) return add(o, {}, said, her('Blunt, balanced, or gentler. Pick one.'));
      return add(o, { step: 'saving', draft: { ...o.draft, directness: picked } }, said);
    }
  }
}

/** The core wrote the files. `hasModel` decides what she says comes next. */
export function saved(o: Onboarding, hasModel: boolean): Onboarding {
  const { userName, agentName, timezone } = o.draft;
  const where = timezone ? `, and I have you in ${timezone.replace(/_/g, ' ')}` : '';
  const next = hasModel
    ? "That was the last scripted line. Say something and I'll answer for real."
    : "One thing left: I need a model to think with. Pick one and I'll stop reading from a script.";
  return add(
    o,
    { step: 'done' },
    her(`Done. I'm ${agentName}, you're ${userName}${where}. Change any of it from Names, at the top.`),
    her(next),
  );
}

const SAVE_PROBLEMS: Readonly<Record<string, string>> = {
  name_empty: 'one of the names came through empty',
  name_too_long: 'one of the names is over forty characters',
  name_not_allowed: "one of the names has a character I can't store",
  timezone_not_allowed: "your timezone didn't look like one",
  not_saved: "the data folder couldn't be written",
};

/** The core refused or could not be reached. Nothing changed; she says why and asks again. */
export function failed(o: Onboarding, code: string): Onboarding {
  const why = SAVE_PROBLEMS[code] ?? "the app didn't answer";
  if (code.startsWith('name_')) {
    return add(o, { step: 'user_name' }, her(`That didn't save: ${why}. Nothing changed. Let's go again: what should I call you?`));
  }
  return add(o, { step: 'directness' }, her(`That didn't save: ${why}. Nothing changed. Pick the tone again and I'll retry.`));
}
