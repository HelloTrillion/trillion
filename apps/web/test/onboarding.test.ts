import { describe, expect, it } from 'vitest';
import {
  APPROVE,
  DECLINE,
  EXAMPLE,
  LATER,
  type Onboarding,
  OPEN,
  type Profile,
  type Ready,
  SKIP,
  active,
  answer,
  begin,
  choices,
  configured,
  decide,
  directnessFrom,
  failed,
  nameFrom,
  nameProblem,
  placeholder,
  saved,
} from '../src/onboarding';

const FRESH: Profile = { userName: '', agentName: 'Trillion', timezone: 'America/Chicago', directness: 'balanced' };
const said = (o: Onboarding): string => o.lines[o.lines.length - 1]?.text ?? '';
const run = (answers: string[], from: Profile = FRESH): Onboarding => answers.reduce(answer, begin(from));
const keep = (o: Onboarding): string => choices(o)[0]?.value ?? '';
const NOTHING: Ready = { model: null, speech: false, listening: false };
const EVERYTHING: Ready = { model: 'model-a', speech: true, listening: true };
const named = (ready: Ready): Onboarding => saved(run(['Sam', 'Nova', 'blunt']), ready);
const texts = (o: Onboarding): string[] => o.lines.map((l) => l.text);

describe('onboarding', () => {
  it('opens by saying who she is and asking who the user is', () => {
    const o = begin(FRESH);
    expect(o.step).toBe('user_name');
    expect(o.lines).toEqual([{ who: 'her', text: "I'm Trillion. Before anything else: what should I call you?" }]);
    expect(placeholder(o)).toBe('Your name');
    expect(choices(o).map((c) => c.label)).toEqual(['Skip for now']);
  });

  it('walks name, her name and tone, then is ready to save', () => {
    const o = run(["I'm Sam.", 'call you Nova', 'gentler']);
    expect(o.step).toBe('saving');
    expect(o.draft).toEqual({ userName: 'Sam', agentName: 'Nova', timezone: 'America/Chicago', directness: 'gentle' });
    expect(o.lines.filter((l) => l.who === 'you').map((l) => l.text)).toEqual(["I'm Sam.", 'call you Nova', 'gentler']);
    expect(active(o)).toBe(false);
  });

  it('uses the name she already has in every line until the user changes it', () => {
    const o = run(['Sam'], { ...FRESH, agentName: 'Nova' });
    expect(said(o)).toContain('"Nova" is the name I came with');
    expect(o.lines.some((l) => l.text.includes('Trillion'))).toBe(false);
  });

  it('keeps her name on a click or a plain yes', () => {
    const first = run(['Sam']);
    expect(choices(first).map((c) => c.label)).toEqual(['Keep Trillion', 'Skip for now']);
    for (const reply of [keep(first), 'keep it', "that's fine", 'ok']) {
      const o = answer(first, reply);
      expect(o.draft.agentName).toBe('Trillion');
      expect(said(o)).toContain('Trillion it stays.');
    }
  });

  it('offers the three tones as buttons and understands them typed', () => {
    const o = run(['Sam', 'Nova']);
    expect(choices(o).map((c) => c.value)).toEqual(['blunt', 'balanced', 'gentle', SKIP]);
    expect(answer(o, 'blunt').lines.at(-1)).toEqual({ who: 'you', text: 'Blunt' });
    expect(directnessFrom('as blunt as you like')).toBe('blunt');
    expect(directnessFrom('somewhere in the middle')).toBe('balanced');
    expect(directnessFrom('go easy on me')).toBe('gentle');
    expect(directnessFrom('purple')).toBeNull();
  });

  it('asks again, in her words, when an answer cannot be used', () => {
    expect(said(run(['   ...']))).toBe('I need something to go on. Any name will do.');
    expect(said(run(['x'.repeat(41)]))).toContain('Forty characters');
    expect(said(run(['Sam{agent_name}']))).toContain('No brackets, braces');
    const o = run(['Sam', 'Nova', 'purple']);
    expect(o.step).toBe('directness');
    expect(said(o)).toBe('Blunt, balanced, or gentler. Pick one.');
  });

  it('reads a name out of the ways people say one', () => {
    expect(nameFrom("I'm Sam.")).toBe('Sam');
    expect(nameFrom('hi, my name is Sam Okafor!')).toBe('Sam Okafor');
    expect(nameFrom('Call me   Dr. K')).toBe('Dr. K');
    expect(nameFrom('Imogen')).toBe('Imogen'); // not "I'm ogen"
    expect(nameProblem("O'Brien")).toBeNull();
    expect(nameProblem('李雷')).toBeNull();
    expect(nameProblem('<b>')).not.toBeNull();
  });

  it('reads more of the ways people introduce themselves', () => {
    expect(nameFrom('you can call me Sam')).toBe('Sam');
    expect(nameFrom('I go by Sam')).toBe('Sam');
    expect(nameFrom('Sam please')).toBe('Sam');
    expect(nameFrom('ok, call me Sam please.')).toBe('Sam');
    expect(nameFrom('Please')).toBe('Please'); // a name, not a filler
  });

  it('reads her new name out of the ways people give one', () => {
    const her = (reply: string): string => run(['Sam', reply]).draft.agentName;
    expect(her('Call yourself Nova')).toBe('Nova');
    expect(her('Call yourself Nova please')).toBe('Nova');
    expect(her('please call yourself Nova')).toBe('Nova');
    expect(her('hmm, name yourself Nova')).toBe('Nova');
    expect(her("I'll name you Nova")).toBe('Nova');
    expect(her('I will name you Nova')).toBe('Nova');
    expect(her("let's name you Nova")).toBe('Nova');
    expect(her('you can be called Nova')).toBe('Nova');
    expect(her('be called Nova')).toBe('Nova');
    expect(her('you can be Nova')).toBe('Nova');
    expect(her('just make it Nova')).toBe('Nova');
    expect(her('your new name is Nova')).toBe('Nova');
    expect(her('change your name to Nova')).toBe('Nova');
    expect(her('change to Nova')).toBe('Nova');
    expect(her('rename yourself to Nova')).toBe('Nova');
    expect(her('ok call you Nova')).toBe('Nova');
    // A name that only starts like a lead-in stays whole.
    expect(her('Make')).toBe('Make');
    expect(her('Nova')).toBe('Nova');
    expect(said(run(['Sam', 'keep it']))).toContain('Trillion it stays.');
  });

  it('says what was saved, then asks about a model first', () => {
    const o = named(NOTHING);
    expect(texts(o)).toContain(
      "Done. I'm Nova, you're Sam, and I have you in America/Chicago. Change any of it in Settings, at the top.",
    );
    expect(o.step).toBe('model');
    expect(said(o)).toContain('Free: a model running on this computer');
    expect(said(o)).toContain('Paid: your own Anthropic or OpenAI key');
    expect(choices(o)).toEqual([
      { label: 'Choose a model', value: OPEN },
      { label: 'Skip for now', value: LATER },
    ]);
    const nowhere = saved(run(['Sam', 'Nova', 'blunt'], { ...FRESH, timezone: '' }), NOTHING);
    expect(texts(nowhere).some((t) => t.includes("you're Sam. Change"))).toBe(true);
  });

  it('names the free choice and the paid one at every rung', () => {
    let o = configured(named(NOTHING), { ...NOTHING, model: 'model-a' });
    expect(o.step).toBe('voice');
    expect(texts(o)).toContain("model-a. That'll do.");
    expect(said(o)).toMatch(/Free: this computer's own.*Paid: ElevenLabs/);
    o = configured(o, { model: 'model-a', speech: true, listening: false });
    expect(o.step).toBe('listening');
    expect(said(o)).toMatch(/Free: Whisper on this computer.*Paid: Deepgram/);
    o = configured(o, EVERYTHING);
    expect(o.step).toBe('demo');
  });

  it('does not ask about what is already set up', () => {
    expect(named({ model: 'model-a', speech: false, listening: true }).step).toBe('voice');
    const all = named(EVERYTHING);
    expect(all.step).toBe('demo');
    expect(texts(all).some((t) => t.includes('Ollama') || t.includes('ElevenLabs') || t.includes('Whisper'))).toBe(false);
  });

  it('ignores a setting that is not the one she asked about', () => {
    const o = named(NOTHING);
    expect(configured(o, { model: null, speech: true, listening: true })).toBe(o);
  });

  it('lets a voice and a mic be left for later, one at a time', () => {
    const ready: Ready = { model: 'model-a', speech: false, listening: false };
    let o = named(ready);
    o = decide(o, LATER, ready);
    expect(o.step).toBe('listening');
    expect(texts(o)).toContain('Text it is.');
    o = decide(o, 'keep typing', ready);
    expect(o.step).toBe('demo');
    expect(texts(o)).toContain('Typing it is.');
  });

  it('stops the ladder when the model is skipped, because nothing else works without one', () => {
    const o = decide(named(NOTHING), 'skip', NOTHING);
    expect(o.step).toBe('done');
    expect(o.greet).toBeUndefined();
    expect(said(o)).toContain("Pick a model when you're ready");
    expect(texts(o).some((t) => t.includes('ElevenLabs'))).toBe(false);
  });

  it('leaves the step alone for anything that is not a skip, so the page can open its settings', () => {
    for (const ready of [NOTHING, { ...NOTHING, model: 'model-a' }]) {
      const o = named(ready);
      expect(decide(o, 'what does it cost?', ready)).toBe(o);
    }
  });

  it('shows the confirmation rule with a card that does nothing', () => {
    const o = named(EVERYTHING);
    expect(said(o)).toBe(EXAMPLE);
    expect(o.lines.at(-1)).toEqual({ who: 'card', text: EXAMPLE, status: 'waiting' });
    expect(o.lines.at(-2)?.text).toContain('shows you a card first');
    expect(choices(o)).toEqual([]);
    expect(placeholder(o)).toContain('yes or no');

    const yes = decide(o, APPROVE, EVERYTHING);
    expect(yes.lines.find((l) => l.who === 'card')).toMatchObject({ status: 'done' });
    expect(texts(yes).some((t) => t.includes('that one was a drill'))).toBe(true);
    expect(yes.step).toBe('done');
    expect(yes.greet).toBe(true);
    expect(said(yes)).toBe('That was the last scripted line.');

    const no = decide(o, 'nope', EVERYTHING);
    expect(no.lines.find((l) => l.who === 'card')).toMatchObject({ status: 'declined' });
    expect(texts(no).some((t) => t.includes("that's how you say no"))).toBe(true);
    expect(no.greet).toBe(true);

    const unclear = decide(o, 'purple', EVERYTHING);
    expect(unclear.step).toBe('demo');
    expect(said(unclear)).toBe('Yes or no will do. Or tap the card.');
  });

  it('never claims the example did anything', () => {
    const lines = texts(decide(named(EVERYTHING), APPROVE, EVERYTHING)).join(' ');
    expect(lines).toContain('Nothing happened');
    expect(lines).not.toMatch(/created|written|saved the file/i);
  });

  it('can be put off at any step, and says where to find it again', () => {
    for (const answers of [[], ['Sam'], ['Sam', 'Nova']]) {
      const o = answer(run(answers), SKIP);
      expect(o.step).toBe('skipped');
      expect(said(o)).toContain('Names are in Settings, at the top');
      expect(choices(o)).toEqual([]);
    }
  });

  it('goes back to the right question when saving fails', () => {
    const ready = run(['Sam', 'Nova', 'blunt']);
    const tone = failed(ready, 'not_saved');
    expect(tone.step).toBe('directness');
    expect(said(tone)).toContain("the data folder couldn't be written");
    expect(said(tone)).toContain('Nothing changed');
    expect(failed(ready, 'name_not_allowed').step).toBe('user_name');
    expect(said(failed(ready, 'unreachable'))).toContain("the app didn't answer");
  });

  it('saves again without a timezone the machine does not know, rather than looping', () => {
    const ready = run(['Sam', 'Nova', 'blunt']); // the browser reported America/Chicago
    const dropped = failed(ready, 'timezone_unknown');
    expect(dropped.step).toBe('saving'); // straight back to saving: nothing to ask the user
    expect(dropped.draft.timezone).toBe('');
    expect(said(dropped)).toBe("Your browser says America/Chicago, which isn't a timezone I know, so I've left the timezone out.");
    const nearest = failed(ready, 'timezone_unknown', 'America/Chicago_Real');
    expect(nearest.draft.timezone).toBe('America/Chicago_Real');
    expect(said(nearest)).toContain("so I've used America/Chicago Real instead.");
    // With no timezone left to drop, a refusal goes back to the tone like any other.
    expect(failed(dropped, 'timezone_unknown').step).toBe('directness');
  });

  it('changes names later without offering to skip', () => {
    const now: Profile = { userName: 'Sam', agentName: 'Nova', timezone: 'Europe/Paris', directness: 'blunt' };
    const o = begin(now);
    expect(said(o)).toContain("You're Sam at the moment");
    expect(choices(o).map((c) => c.label)).toEqual(['Still Sam']);
    const kept = answer(o, keep(o));
    expect(kept.draft.userName).toBe('Sam');
    const renamed = answer(kept, 'Vega');
    expect(said(renamed)).toContain('Vega. I can work with that. Tone is set to blunt.');
    expect(choices(renamed).map((c) => c.label)).toEqual(['Blunt', 'Balanced', 'Gentler']);
    const ready = answer(renamed, 'balanced');
    expect(ready.draft).toEqual({ ...now, agentName: 'Vega', directness: 'balanced' });
    // A rename ends there: no ladder, no example card, no second hello.
    const done = saved(ready, NOTHING);
    expect(done.step).toBe('done');
    expect(done.greet).toBeUndefined();
    expect(said(done)).toContain("Done. I'm Vega, you're Sam");
  });

  it('ignores answers once it is over', () => {
    const done = decide(named(EVERYTHING), APPROVE, EVERYTHING);
    expect(active(done)).toBe(false);
    expect(answer(done, 'hello')).toBe(done);
    expect(decide(done, 'yes', EVERYTHING)).toBe(done);
  });
});
