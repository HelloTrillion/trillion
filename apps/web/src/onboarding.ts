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
  // Her new name read out of a sentence, said back before it is used.
  | 'agent_check'
  | 'directness'
  | 'saving'
  // The capability ladder: something to think with, then a voice, then ears.
  | 'model'
  | 'voice'
  | 'listening'
  // A folder for her file tools, shared by the user's own tap, and the page sharing it.
  | 'folder'
  | 'sharing'
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
  /** A folder is shared with everyone, so her file tools reach something. Null while the
   * shares are not read yet: then she does not ask, rather than ask someone who has one. */
  readonly folder: boolean | null;
  /** The folders she may offer to share, from the places the core lists. */
  readonly places?: readonly Offer[];
}

/** A folder offered for sharing: one of the user's own places, never a home or a drive. */
export interface Offer {
  readonly label: string;
  readonly path: string;
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
  /** At `agent_check`: the name she read out of what was said, not yet used. */
  readonly heard?: string | undefined;
  /** At `folder`: the folders offered. At `sharing`: the one tapped. */
  readonly places?: readonly Offer[] | undefined;
  readonly sharing?: Offer | undefined;
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
const CONFIRM = '\u0000confirm';
const RETRY = '\u0000retry';
/** A tap on one offered folder. Typed text never starts with it, so only a tap shares. */
const SHARE = '\u0000share:';

const her = (text: string): Line => ({ who: 'her', text });
const you = (text: string): Line => ({ who: 'you', text });

// An apostrophe, straight or curly, as phones type it.
const A = `['’]`;
// Words people put in front of an answer ("ok, ...", "hmm, ...") before the part that matters.
const FILLER = String.raw`(?:(?:hi|hey|hello|ok(?:ay)?|hmm+|um+|uh+|erm*|well|so|just|please|there)[,!. ]+)*`;
// The same words before a bare name, only with punctuation after them, so a name like
// "So Yeon", "Um Ji-won" or "Er Wei" stays whole. A greeting goes either way: "Hey" is
// not a name people give.
const BARE_FILLER = /^(?:(?:hi|hey|hello)(?: there)?(?:[,!.]\s*|\s+)|(?:ok(?:ay)?|well|so|just|please|there|hmm+|um+|uh+|erm*)[,!.]\s*)+/i;
// What may follow a lead-in before the name: a space, or a comma, colon or full stop.
const leadIn = (phrases: string): RegExp => new RegExp(String.raw`^${FILLER}(?:${phrases})(?:\s*[,:.!]\s*|\s+)`, 'i');
const INTRO = leadIn(
  [
    String.raw`i${A}?m called|i am called`,
    String.raw`i${A}?m|i am|my name is|my name${A}?s|(?:the )?name${A}?s|it${A}?s|this is|i go by`,
    String.raw`i(?:${A}d like| would like| want) to be called`,
    String.raw`(?:people|everyone|friends) calls? me`,
    String.raw`(?:you can |just |please )?call me`,
  ].join('|'),
);
const RENAME = leadIn(
  [
    String.raw`(?:i|we)(?:${A}ll| will| want to| wanna|${A}d like to| would like to) (?:call|name) you`,
    String.raw`(?:can|could|may) i (?:call|name) you`,
    String.raw`(?:let${A}?s |let me )?(?:call|name) you`,
    String.raw`(?:call|name) yourself`,
    String.raw`rename (?:yourself|you)(?: to)?`,
    String.raw`(?:you can |you could |you should )?be called`,
    String.raw`you can be`,
    String.raw`your (?:new )?name(?:${A}s| is| will be| can be| should be)`,
    String.raw`change (?:your name )?to`,
    // Her question ends "or call me something else?", and people answer in its words.
    String.raw`call me`,
    String.raw`(?:i${A}d like |i want )?(?:something else|a new name|a different name)`,
    String.raw`let${A}?s (?:go with|try|use|do)|(?:i${A}ll |i will |i${A}d )?go with`,
    String.raw`how about|what about|maybe|i (?:like|prefer|choose|pick)`,
    String.raw`you(?:${A}re| are)(?: now)?`,
  ].join('|'),
);
// What people put after a name. A phrase goes at any time; one word only in lower case, or
// after a lead-in or a comma, since "Sam Please" may be a name.
const TAIL_PHRASE = new RegExp(
  String.raw`[\s,]+(?:i think|i guess|it is|if (?:that${A}?s|that is|you don${A}?t mind)(?: (?:ok(?:ay)?|fine|alright))?|nice to meet you|for now|from now on|is (?:good|fine|great|ok(?:ay)?)|sounds good|works for me)$`,
  'i',
);
const WORDS = 'please|thanks|instead|then|maybe|now|here|speaking';
const TAIL_WORD = new RegExp(String.raw`\s+(?:${WORDS})$`);
const TAIL_WORD_ANY = new RegExp(String.raw`[\s,]+(?:${WORDS})$`, 'i');
const TAIL_COMMA = new RegExp(String.raw`\s*,\s*(?:${WORDS})$`, 'i');
const END = /[.!?,;:\s]+$/;
const QUOTED = /^["'“‘](.+)["'”’]$/;

/** The text as typed, less its end punctuation and any quotes round it. */
function tidy(text: string): string {
  let t = text.trim().replace(END, '');
  const q = QUOTED.exec(t);
  if (q?.[1]) t = q[1].trim().replace(END, '');
  return t.replace(/\s+/g, ' ');
}

/** "I'm Sam." and "call me Sam" both mean Sam. */
export function nameFrom(text: string, lead: RegExp = INTRO): string {
  const said = tidy(text);
  let name = said;
  // "something else. How about Nova?" has two lead-ins: take them off in turn.
  for (let i = 0; i < 3; i++) {
    const next = name.replace(lead, '');
    if (next === name || !next) break;
    name = next;
  }
  const led = name !== said;
  if (!led) name = name.replace(BARE_FILLER, '') || name;
  for (let i = 0; i < 3; i++) {
    const before = name;
    name = name.replace(TAIL_PHRASE, '');
    name = name.replace(led ? TAIL_WORD_ANY : TAIL_COMMA, '');
    if (!led) name = name.replace(TAIL_WORD, '');
    name = tidy(name);
    if (name === before) break;
  }
  return name || said;
}

const esc = (t: string): string => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** "keep it", "Trillion is fine", "nah, stick with it": her name stays. */
function keepsName(text: string, current: string): boolean {
  const n = esc(current);
  const good = String.raw`(?:fine|good|great|perfect|ok(?:ay)?|nice)`;
  const keep = new RegExp(
    String.raw`^${FILLER}(?:(?:no|nah|nope)[,!. ]+)?(?:` +
      [
        String.raw`keep(?: it| that| the name| ${n})?`,
        String.raw`stick with (?:it|that|${n})`,
        String.raw`stay(?: ${n})?|same|no change|leave it|default`,
        String.raw`(?:that|it|this|${n})(?:${A}s| is) ${good}`,
        String.raw`${n}(?: it is)?`,
        String.raw`i like (?:it|that|${n})|sounds good|fine|good|yes|yep|yeah|sure|ok(?:ay)?`,
      ].join('|') +
      String.raw`)(?:[,!. ]+(?:please|thanks))?[.!? ]*$`,
    'i',
  );
  return keep.test(text.trim());
}

// No wish either way: she keeps the name she has.
const UNSURE = new RegExp(
  String.raw`^(?:i )?(?:don${A}?t know|dunno|not sure|no idea|don${A}?t mind|you (?:pick|choose|decide)|surprise me|whatever|no preference|(?:it${A}?s )?up to you|your call)\b`,
  'i',
);
// Words that answer a question rather than name anyone: never a name on their own.
const STOP: ReadonlySet<string> = new Set(
  (
    'yes y yeah yep yup ya no n nope nah sure ok okay right correct perfect fine good great thanks thank please ' +
    'something else new name different another other anything whatever idk dunno maybe a an the it that this ' +
    'one hi hello hey hmm um uh er erm well so just'
  ).split(' '),
);
const word = (w: string): string => w.toLowerCase().replace(/[^\p{L}\p{M}']/gu, '');
/** Only answer words ("no", "something else", "a new name"): there is no name in it. */
const nameless = (name: string): boolean => name.split(/[\s,]+/).every((w) => !word(w) || STOP.has(word(w)));
/** One or two words that look like a name and are not answer words. */
const plainName = (name: string): boolean => {
  const words = name.split(' ');
  return words.length <= 2 && words.every((w) => /^\p{L}[\p{L}\p{M}'’.-]*$/u.test(w) && !STOP.has(word(w)));
};
const YES_WORD = String.raw`(?:yes|yeah|yep|yup|y|sure|ok(?:ay)?|right|correct|perfect|exactly|fine|good|great|that${A}?s (?:it|right|me)|that is (?:it|right|me)|it${A}?s me|sounds good)`;
const NO_WORD = String.raw`(?:no|nope|nah|n|wrong|not (?:quite|really|right|that))`;
const SEP = String.raw`(?:[\s,.!:;-]+|$)`;
const YES_LEAD = new RegExp(String.raw`^${YES_WORD}${SEP}`, 'i');
const NO_LEAD = new RegExp(String.raw`^${NO_WORD}${SEP}`, 'i');
const POLITE = new RegExp(String.raw`^(?:please|thanks|thank you|but|actually|${YES_WORD})${SEP}`, 'i');

/** What is left of `text` once the words that only agree or thank are taken off the front. */
function besides(text: string, heard = ''): string {
  let rest = text.trim();
  const name = heard ? new RegExp(String.raw`^["“]?${esc(heard)}["”]?${SEP}`, 'i') : null;
  for (let i = 0; i < 6; i++) {
    const next = rest.replace(POLITE, '').replace(name ?? /$^/, '').trim();
    if (next === rest) break;
    rest = next;
  }
  return rest.replace(/^[\s,.!?:;-]+|[\s,.!?:;-]+$/g, '');
}

/** Past this many words it is a sentence the parsing missed, not a name. */
export const NAME_WORDS = 5;

/** Why a name cannot be used, in her words, or null if it can. The core checks again. */
export function nameProblem(name: string): string | null {
  if (!name) return 'I need something to go on. Any name will do.';
  if (name.split(' ').length > NAME_WORDS) return 'I lost the name in that. Give me just the name, on its own.';
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
    case 'agent_check':
      return [
        { label: `Yes, ${o.heard ?? ''}`, value: CONFIRM },
        { label: 'No', value: RETRY },
      ];
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
    case 'folder': {
      const offers = (o.places ?? []).map((p) => ({ label: `Share ${p.label}`, value: SHARE + p.path }));
      return [
        ...offers,
        { label: offers.length ? 'Another folder' : 'Choose a folder', value: OPEN },
        { label: 'Not now', value: LATER },
      ];
    }
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
    case 'agent_check':
      return 'Yes, or the name again';
    case 'directness':
      return 'Blunt, balanced or gentler';
    case 'model':
    case 'voice':
    case 'listening':
      return 'Or type "skip"';
    case 'folder':
      return 'Tap a folder, or type "not now"';
    case 'sharing':
      return 'Sharing it';
    case 'demo':
      return 'Approve or decline the card, or just say yes or no';
    default:
      return '';
  }
}

const ASKING: readonly Step[] = [
  'user_name',
  'agent_name',
  'agent_check',
  'directness',
  'model',
  'voice',
  'listening',
  'folder',
  // The page is sharing the tapped folder: what is typed meanwhile is not for the model.
  'sharing',
  'demo',
];
const CLIMBING: readonly Step[] = ['model', 'voice', 'listening', 'folder', 'sharing', 'demo'];

/** True on the ladder or at the card, where a reply goes to `decide`, not `answer`. */
export const climbing = (o: Onboarding | null): boolean => o !== null && CLIMBING.includes(o.step);

/** True while she is waiting on an answer, so what is typed goes to her script. */
export const active = (o: Onboarding | null): boolean => o !== null && ASKING.includes(o.step);

function add(o: Onboarding, patch: Partial<Onboarding>, ...lines: Line[]): Onboarding {
  return { ...o, ...patch, lines: [...o.lines, ...lines] };
}

/** What the user typed or clicked at the current step. */
export function answer(o: Onboarding, text: string): Onboarding {
  if (!active(o)) return o;
  if (climbing(o)) return o;
  if (text === SKIP) {
    return add(
      o,
      { step: 'skipped' },
      you('Skip for now'),
      her(`Fine. I stay ${o.was} and you stay "you" until you say otherwise. Names are in Settings, at the top.`),
      her(TRUST),
    );
  }
  const kept = text === KEEP;
  switch (o.step) {
    case 'user_name': {
      const typed = text.trim();
      // "yes", "yes, Sam", "yep thats me" keep the name there is; "Yes, but call me
      // Samuel" is Samuel. What follows a yes is read as a name, never taken as typed.
      const rest = YES_LEAD.test(typed) ? besides(typed, o.draft.userName) : typed;
      const keep = kept || (o.returning && !rest);
      const name = keep ? o.draft.userName : nameFrom(rest || typed);
      const said = you(kept ? `Still ${name}` : text.trim());
      if (!keep && name && nameless(name)) return add(o, {}, said, her('I need a name to go on. What should I call you?'));
      const problem = nameProblem(name);
      if (problem) return add(o, {}, said, her(problem));
      const ask = o.returning
        ? `${name}. And I'm ${o.draft.agentName}. Keep that, or rename me?`
        : `${name}. Noted. Your turn: "${o.draft.agentName}" is the name I came with. Keep it, or call me something else?`;
      return add(o, { step: 'agent_name', draft: { ...o.draft, userName: name } }, said, her(ask));
    }
    case 'agent_name': {
      const typed = text.trim();
      const said = you(kept ? `Keep ${o.draft.agentName}` : typed);
      if (kept || keepsName(typed, o.draft.agentName) || UNSURE.test(typed)) return named(o, o.draft.agentName, said);
      // "sure, Nova" and "no, Vega" are read back as the name alone, and checked.
      const lead = YES_LEAD.exec(typed) ?? NO_LEAD.exec(typed);
      const core = (lead && besides(typed.slice(lead[0].length))) || typed;
      const name = nameFrom(core, RENAME);
      return heardName(o, name, said, core === typed && plainName(tidy(typed)) && name === tidy(typed));
    }
    case 'agent_check': {
      const heard = o.heard ?? o.draft.agentName;
      const typed = text.trim();
      if (text === CONFIRM) return named(o, heard, you(`Yes, ${heard}`));
      if (text === RETRY) return instead(o, you('No'));
      const said = you(typed);
      // "Yes", "Yes, Nova", "yeah that's right", "okay thanks": agreed.
      if (YES_LEAD.test(typed)) {
        const rest = besides(typed, heard);
        if (!rest) return named(o, heard, said);
        return heardName(o, nameFrom(rest, RENAME), said, false);
      }
      // The name said again agrees, as a yes would.
      if (tidy(typed).toLowerCase() === heard.toLowerCase()) return named(o, heard, said);
      if (keepsName(typed, o.draft.agentName)) return named(o, o.draft.agentName, said);
      const no = NO_LEAD.exec(typed);
      if (no) {
        // "no", "no thanks": ask again. "no, Vega": Vega, checked unless it is plainly a name.
        const rest = besides(typed.slice(no[0].length));
        if (!rest) return instead(o, said);
        const name = nameFrom(rest, RENAME);
        return heardName(o, name, said, plainName(rest) && name === tidy(rest) && !name.includes(' '));
      }
      // Anything else is the name again, and is always checked: from here a guess is never taken.
      return heardName(o, nameFrom(typed, RENAME), said, false);
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

/** A name read from a reply: taken when `plain`, else said back first, so a wrong guess
 * is never written down. Answer words alone ("something else", "no") ask again. */
function heardName(o: Onboarding, name: string, said: Line, plain: boolean): Onboarding {
  if (name && nameless(name)) return instead(o, said);
  const problem = nameProblem(name);
  if (problem) return add(o, { step: 'agent_name', heard: undefined }, said, her(problem));
  if (plain) return named(o, name, said);
  return add(o, { step: 'agent_check', heard: name }, said, her(`"${name}", then? I'd like to get it right before I write it down.`));
}

function instead(o: Onboarding, said: Line): Onboarding {
  return add(o, { step: 'agent_name', heard: undefined }, said, her('What would you like instead?'));
}

/** Her name is settled: say it back, then ask about the tone. */
function named(o: Onboarding, name: string, said: Line): Onboarding {
  const taken = name === o.draft.agentName ? `${name} it stays.` : `${name} it is.`;
  const ask = o.returning
    ? `${taken} Tone is set to ${o.draft.directness}. Blunt, balanced, or gentler?`
    : `${taken} Last one. I'm dry by default and I aim it at ideas, never at you. Want me blunt, balanced, or gentler?`;
  return add(o, { step: 'directness', heard: undefined, draft: { ...o.draft, agentName: name } }, said, her(ask));
}

/** The core wrote the files. On a first run the ladder comes next; a rename just ends. */
export function saved(o: Onboarding, ready: Ready): Onboarding {
  const { userName, agentName, timezone } = o.draft;
  const where = timezone ? `, and I have you in ${timezone.replace(/_/g, ' ')}` : '';
  const done = add(o, {}, her(`Done. I'm ${agentName}, you're ${userName}${where}. Change any of it in Settings, at the top.`));
  return o.returning ? { ...done, step: 'done' } : climb(done, ready, 'model');
}

const LADDER = ['model', 'voice', 'listening', 'folder', 'demo'] as const;
type Rung = (typeof LADDER)[number];

const ASK: Readonly<Record<Rung, string>> = {
  model:
    "Now the part where I get something to think with. Free: a model running on this computer through Ollama or LM Studio, and nothing leaves the machine. Paid: your own Anthropic or OpenAI key, and they bill you for what you use. A paid key comes with a monthly cap: a warning at $20 and a stop at $50, which you can change when you add it. A model on this computer costs nothing and has no cap. Until one is chosen, everything I say is a script.",
  voice:
    "Next, a voice. Free: this computer's own, which works at once and sounds like a sat nav. Paid: ElevenLabs with your own key, which sounds like a person and is billed by them. Or I stay in text.",
  listening:
    'Then ears. Free: Whisper on this computer, a one-time download, and your words show up after you stop talking. Paid: Deepgram with your own key, which hears you as you speak. Or you keep typing.',
  folder:
    "Then a folder to work in. My file tools reach only folders you share, and none is shared yet. Share one and I can read and change what's in it, and every change still shows you a card first. Never your whole home folder or a whole drive: those stay off limits.",
  demo: "One rule before we start, and it isn't mine to break: anything that changes something shows you a card first. Like this one.",
};

const has = (ready: Ready, rung: Rung): boolean =>
  rung === 'model'
    ? ready.model !== null
    : rung === 'voice'
      ? ready.speech
      : rung === 'listening'
        ? ready.listening
        : rung === 'folder'
          ? ready.folder !== false
          : false;

const OFFERED: readonly string[] = ['Desktop', 'Documents'];

/**
 * The folders to offer, from the places the core lists: the Desktop and Documents when
 * they exist. Never the home folder or a drive, which can never be shared (D4).
 */
export function folderOffers(places: readonly { label: string; path: string; kind: string }[]): Offer[] {
  return places.filter((p) => p.kind === 'folder' && OFFERED.includes(p.label)).map((p) => ({ label: p.label, path: p.path }));
}

/** Move to the first rung from `from` that is not already set up, and ask about it. */
function climb(o: Onboarding, ready: Ready, from: Rung): Onboarding {
  const rung = LADDER.slice(LADDER.indexOf(from)).find((r) => !has(ready, r)) ?? 'demo';
  if (rung === 'demo') {
    return add(o, { step: 'demo' }, her(ASK.demo), { who: 'card', text: EXAMPLE, status: 'waiting' });
  }
  const places = rung === 'folder' ? { places: ready.places ?? [] } : {};
  return add(o, { step: rung, ...places }, her(ASK[rung]));
}

const after = (rung: Rung): Rung => LADDER[Math.min(LADDER.indexOf(rung) + 1, LADDER.length - 1)] ?? 'demo';

// SPN-15: the threat model, said once, so nobody takes it for a stronger guarantee.
const TRUST =
  "Worth knowing: I trust whoever is at this computer. I can't tell your voice from anyone else's, so anything that costs money or can't be undone waits for a tap on its card.";

function finish(o: Onboarding, ready: Ready): Onboarding {
  const trusted = add(o, {}, her(TRUST));
  return ready.model !== null
    ? add(trusted, { step: 'done', greet: true }, her('That was the last scripted line.'))
    : add(trusted, { step: 'done' }, her("Pick a model when you're ready (in Settings) and I'll stop reading from a script."));
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
      return climb(add(o, {}, you('Keep typing'), her('Typing it is.')), ready, 'folder');
    case 'folder': {
      if (text.startsWith(SHARE)) {
        // Only a folder she offered: the page shares it through the same route Settings uses.
        const place = (o.places ?? []).find((p) => SHARE + p.path === text);
        if (!place) return o;
        return add(o, { step: 'sharing', sharing: place }, you(`Share ${place.label}`));
      }
      if (text !== LATER && !/^(skip|later|maybe later|not (?:now|yet)|no|nope|nah|none|never ?mind)\b/.test(t)) return o;
      return climb(
        add(o, { places: undefined }, you('Not now'), her('No folder for now. Settings, Shared folders, when you want one.')),
        ready,
        'demo',
      );
    }
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

/** The places were read after she asked for a folder: offer them now. */
export function placesFound(o: Onboarding, places: readonly Offer[]): Onboarding {
  if (o.step !== 'folder' || (o.places ?? []).length || !places.length) return o;
  return { ...o, places };
}

/** The page shared the tapped folder (`error` empty) or was refused, in the core's words. */
export function sharedFolder(o: Onboarding, error: string, ready: Ready): Onboarding {
  if (o.step !== 'sharing' || !o.sharing) return o;
  const { label } = o.sharing;
  if (error) {
    return add(o, { step: 'folder', sharing: undefined }, her(`${error} Nothing was shared. Pick another, or not now.`));
  }
  const line = `${label} is shared. I can read and change what's in it, and every change still asks you first.`;
  return climb(add(o, { sharing: undefined, places: undefined }, her(line)), ready, 'demo');
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
  if (o.step === 'folder' && ready.folder === true) {
    const line = 'Good, a folder is shared. Settings, Shared folders, is where to change it.';
    return climb(add(o, { places: undefined }, her(line)), ready, after('folder'));
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
