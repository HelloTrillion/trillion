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

export type Step =
  | 'user_name'
  | 'agent_name'
  | 'directness'
  | 'saving'
  // The capability ladder: something to think with, then a voice, then ears.
  | 'model'
  | 'voice'
  | 'listening'
  // The confirmation rule, shown rather than described.
  | 'demo'
  | 'done'
  | 'skipped';

export type Line =
  | { readonly who: 'her' | 'you'; readonly text: string }
  /** The example card. It looks like a real one and does nothing. */
  | { readonly who: 'card'; readonly text: string; readonly status: 'waiting' | 'done' | 'declined' };

/** What is already set up, so a step that is done is not asked about. */
export interface Ready {
  readonly model: string | null;
  readonly speech: boolean;
  readonly listening: boolean;
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
  /** Set when it ends with a model in place: she now says hello in her own words. */
  readonly greet?: boolean;
}

export const NAME_LIMIT = 40;
export const SKIP = '\u0000skip';
/** Open the settings for the current step. The page does that; nothing is said. */
export const OPEN = '\u0000open';
/** Leave the current step of the ladder for later. */
export const LATER = '\u0000later';
export const APPROVE = '\u0000approve';
export const DECLINE = '\u0000decline';
export const EXAMPLE = 'Create hello.txt in a folder you shared';
const KEEP = '\u0000keep';

const her = (text: string): Line => ({ who: 'her', text });
const you = (text: string): Line => ({ who: 'you', text });

// Words people put in front of an answer ("ok, ...", "hmm, ...") before the part that matters.
const FILLER = String.raw`(?:(?:hi|hey|hello|ok(?:ay)?|hmm+|um+|uh+|well|so|just|please)[,!. ]+)*`;
const leadIn = (phrases: string): RegExp => new RegExp(String.raw`^${FILLER}(?:${phrases})\s+`, 'i');
const INTRO = leadIn(String.raw`i'?m|i am|my name is|my name'?s|name'?s|it'?s|this is|i go by|(?:you can |just )?call me`);
const RENAME = leadIn(
  [
    String.raw`i(?:'ll| will) (?:call|name) you`,
    String.raw`(?:let'?s )?(?:call|name) you`,
    String.raw`(?:call|name) yourself`,
    String.raw`rename yourself(?: to)?`,
    String.raw`(?:you can )?be called`,
    String.raw`you can be`,
    String.raw`your (?:new )?name(?:'s| is)`,
    String.raw`change (?:your name )?to`,
    String.raw`let'?s go with|how about|you(?:'re| are)|go with`,
  ].join('|'),
);
const KEEP_IT = /^(?:keep(?: it)?|same|stay|no change|default|(?:that|it|this)(?:'s| is) (?:fine|good)|fine|yes|yep|ok(?:ay)?)[.! ]*$/i;

/** "I'm Sam." and "call me Sam" both mean Sam. */
export function nameFrom(text: string, lead: RegExp = INTRO): string {
  const said = text.trim();
  const name = said.replace(lead, '');
  // "Sam, please" and "call me Sam please" end in a filler; a bare "Sam Please" may be a name.
  const please = name === said ? /\s*,\s*please[.!\s]*$/i : /[\s,]+please[.!\s]*$/i;
  return name
    .replace(please, '')
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
    case 'model':
      return [
        { label: 'Choose a model', value: OPEN },
        { label: 'Skip for now', value: LATER },
      ];
    case 'voice':
      return [
        { label: 'Choose a voice', value: OPEN },
        { label: 'Stay in text', value: LATER },
      ];
    case 'listening':
      return [
        { label: 'Choose how she hears', value: OPEN },
        { label: 'Keep typing', value: LATER },
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
    case 'model':
    case 'voice':
    case 'listening':
      return 'Or type "skip"';
    case 'demo':
      return 'Approve or decline the card, or just say yes or no';
    default:
      return '';
  }
}

const ASKING: readonly Step[] = ['user_name', 'agent_name', 'directness', 'model', 'voice', 'listening', 'demo'];

/** True while she is waiting on an answer, so what is typed goes to her script. */
export const active = (o: Onboarding | null): boolean => o !== null && ASKING.includes(o.step);

function add(o: Onboarding, patch: Partial<Onboarding>, ...lines: Line[]): Onboarding {
  return { ...o, ...patch, lines: [...o.lines, ...lines] };
}

/** What the user typed or clicked at the current step. */
export function answer(o: Onboarding, text: string): Onboarding {
  if (!active(o)) return o;
  if (o.step === 'model' || o.step === 'voice' || o.step === 'listening' || o.step === 'demo') return o;
  if (text === SKIP) {
    return add(
      o,
      { step: 'skipped' },
      you('Skip for now'),
      her(`Fine. I stay ${o.was} and you stay "you" until you say otherwise. Names are in Settings, at the top.`),
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
    default:
      return o;
  }
}

/** The core wrote the files. On a first run the ladder comes next; a rename just ends. */
export function saved(o: Onboarding, ready: Ready): Onboarding {
  const { userName, agentName, timezone } = o.draft;
  const where = timezone ? `, and I have you in ${timezone.replace(/_/g, ' ')}` : '';
  const done = add(o, {}, her(`Done. I'm ${agentName}, you're ${userName}${where}. Change any of it in Settings, at the top.`));
  return o.returning ? { ...done, step: 'done' } : climb(done, ready, 'model');
}

const LADDER = ['model', 'voice', 'listening', 'demo'] as const;
type Rung = (typeof LADDER)[number];

const ASK: Readonly<Record<Rung, string>> = {
  model:
    "Now the part where I get something to think with. Free: a model running on this computer through Ollama or LM Studio, and nothing leaves the machine. Paid: your own Anthropic or OpenAI key, and they bill you for what you use. Until one is chosen, everything I say is a script.",
  voice:
    "Next, a voice. Free: this computer's own, which works at once and sounds like a sat nav. Paid: ElevenLabs with your own key, which sounds like a person and is billed by them. Or I stay in text.",
  listening:
    'Then ears. Free: Whisper on this computer, a one-time download, and your words show up after you stop talking. Paid: Deepgram with your own key, which hears you as you speak. Or you keep typing.',
  demo: "One rule before we start, and it isn't mine to break: anything that changes something shows you a card first. Like this one.",
};

const has = (ready: Ready, rung: Rung): boolean =>
  rung === 'model' ? ready.model !== null : rung === 'voice' ? ready.speech : rung === 'listening' ? ready.listening : false;

/** Move to the first rung from `from` that is not already set up, and ask about it. */
function climb(o: Onboarding, ready: Ready, from: Rung): Onboarding {
  const rung = LADDER.slice(LADDER.indexOf(from)).find((r) => !has(ready, r)) ?? 'demo';
  if (rung === 'demo') {
    return add(o, { step: 'demo' }, her(ASK.demo), { who: 'card', text: EXAMPLE, status: 'waiting' });
  }
  return add(o, { step: rung }, her(ASK[rung]));
}

const after = (rung: Rung): Rung => LADDER[Math.min(LADDER.indexOf(rung) + 1, LADDER.length - 1)] ?? 'demo';

function finish(o: Onboarding, ready: Ready): Onboarding {
  return ready.model !== null
    ? add(o, { step: 'done', greet: true }, her('That was the last scripted line.'))
    : add(o, { step: 'done' }, her("Pick a model when you're ready (in Settings) and I'll stop reading from a script."));
}

/** A click or a typed answer on the ladder or the example card. */
export function decide(o: Onboarding, text: string, ready: Ready): Onboarding {
  const t = text.trim().toLowerCase();
  switch (o.step) {
    case 'model':
      if (text !== LATER && !/^(skip|later|no|not now)\b/.test(t)) return o;
      // Without a model there is nobody to speak or listen for. The rest can wait.
      return finish(add(o, {}, you('Skip for now'), her('Fine. The rest can wait too.')), ready);
    case 'voice':
      if (text !== LATER && !/^(skip|later|no|text|stay)\b/.test(t)) return o;
      return climb(add(o, {}, you('Stay in text'), her('Text it is.')), ready, 'listening');
    case 'listening':
      if (text !== LATER && !/^(skip|later|no|typ|keep)/.test(t)) return o;
      return climb(add(o, {}, you('Keep typing'), her('Typing it is.')), ready, 'demo');
    case 'demo': {
      const yes = text === APPROVE || /^(y|yes|yep|ok|okay|approve|sure|do it)\b/.test(t);
      const no = text === DECLINE || /^(n|no|nope|decline|cancel|don't)\b/.test(t);
      if (!yes && !no) return add(o, {}, you(text.trim()), her('Yes or no will do. Or tap the card.'));
      const lines = o.lines.map((l): Line => (l.who === 'card' ? { ...l, status: yes ? 'done' : 'declined' } : l));
      const said = text === APPROVE ? 'Approve' : text === DECLINE ? 'Decline' : text.trim();
      const verdict = yes
        ? "That's the whole ceremony. Nothing happened, by the way: that one was a drill. A real card says exactly what it will do, and it does only that."
        : "And that's how you say no. Nothing happens, and I don't sulk. That one was a drill either way.";
      return finish(add({ ...o, lines }, {}, you(said), her(verdict)), ready);
    }
    default:
      return o;
  }
}

/** Something was set up in a dialog. If it was what she was asking about, move on. */
export function configured(o: Onboarding, ready: Ready): Onboarding {
  if (o.step === 'model' && ready.model !== null) {
    return climb(add(o, {}, her(`${ready.model}. That'll do.`)), ready, after('model'));
  }
  if (o.step === 'voice' && ready.speech) {
    return climb(add(o, {}, her("Good. You'll hear me from the first real answer.")), ready, after('voice'));
  }
  if (o.step === 'listening' && ready.listening) {
    return climb(add(o, {}, her('Good. The Speak button is next to Send.')), ready, after('listening'));
  }
  return o;
}

const SAVE_PROBLEMS: Readonly<Record<string, string>> = {
  name_empty: 'one of the names came through empty',
  name_too_long: 'one of the names is over forty characters',
  name_not_allowed: "one of the names has a character I can't store",
  timezone_not_allowed: "your timezone didn't look like one",
  timezone_unknown: "your timezone isn't one I know (it should look like America/Toronto)",
  not_saved: "the data folder couldn't be written",
};

/** The core refused or could not be reached. Nothing changed; she says why and asks again. */
export function failed(o: Onboarding, code: string, suggestion = ''): Onboarding {
  const why = SAVE_PROBLEMS[code] ?? "the app didn't answer";
  if (code.startsWith('timezone_') && o.draft.timezone) {
    // The page read the timezone from the browser; the user never typed it and has no
    // step to change it. So it is not asked about: the save goes again without it, or
    // with the nearest real one, and she says which.
    const was = o.draft.timezone.replace(/_/g, ' ');
    const line = suggestion
      ? `Your browser says ${was}, which isn't a timezone I know, so I've used ${suggestion.replace(/_/g, ' ')} instead.`
      : `Your browser says ${was}, which isn't a timezone I know, so I've left the timezone out.`;
    return add(o, { step: 'saving', draft: { ...o.draft, timezone: suggestion } }, her(line));
  }
  if (code.startsWith('name_')) {
    return add(o, { step: 'user_name' }, her(`That didn't save: ${why}. Nothing changed. Let's go again: what should I call you?`));
  }
  return add(o, { step: 'directness' }, her(`That didn't save: ${why}. Nothing changed. Pick the tone again and I'll retry.`));
}
