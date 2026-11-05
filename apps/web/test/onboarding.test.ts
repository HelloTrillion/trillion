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
  folderOffers,
  placesFound,
  sharedFolder,
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
const NOTHING: Ready = { model: null, speech: false, listening: false, folder: false };
const EVERYTHING: Ready = { model: 'model-a', speech: true, listening: true, folder: true };
const named = (ready: Ready): Onboarding => saved(run(['Sam', 'Nova', 'blunt']), ready);
/** Her name as the draft has it after one reply, and a yes if she checks it. */
const herName = (reply: string, from: Profile = FRESH): string => {
  const o = run(['Sam', reply], from);
  return (o.step === 'agent_check' ? answer(o, 'yes') : o).draft.agentName;
};
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
    const o = run(["I'm Sam.", 'call you Nova', 'yes', 'gentler']);
    expect(o.step).toBe('saving');
    expect(o.draft).toEqual({ userName: 'Sam', agentName: 'Nova', timezone: 'America/Chicago', directness: 'gentle' });
    expect(o.lines.filter((l) => l.who === 'you').map((l) => l.text)).toEqual(["I'm Sam.", 'call you Nova', 'yes', 'gentler']);
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
    expect(nameFrom('Sam, please')).toBe('Sam');
    expect(nameFrom('Sam Please')).toBe('Sam Please'); // no comma, no lead-in: could be a name
    expect(nameFrom('ok, call me Sam please.')).toBe('Sam');
    expect(nameFrom('Please')).toBe('Please'); // a name, not a filler
  });

  it('reads her new name out of the ways people give one', () => {
    const her = herName;
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
    expect(her('your new name is Nova')).toBe('Nova');
    expect(her('change your name to Nova')).toBe('Nova');
    expect(her('change to Nova')).toBe('Nova');
    expect(her('rename yourself to Nova')).toBe('Nova');
    expect(her('ok call you Nova')).toBe('Nova');
    // A name that only starts like a lead-in stays whole.
    expect(her('Make')).toBe('Make');
    expect(her('Make It Rain')).toBe('Make It Rain');
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

  it('says a paid key comes with caps, set in the same step, and a local model costs nothing', () => {
    const o = named(NOTHING);
    expect(said(o)).toContain(
      'A paid key comes with a monthly cap: a warning at $20 and a stop at $50, which you can change when you add it. A model on this computer costs nothing and has no cap.',
    );
  });

  it('names the free choice and the paid one at every rung', () => {
    let o = configured(named(NOTHING), { ...NOTHING, model: 'model-a' });
    expect(o.step).toBe('voice');
    expect(texts(o)).toContain("model-a. That'll do.");
    expect(said(o)).toMatch(/Free: this computer's own.*Paid: ElevenLabs/);
    o = configured(o, { model: 'model-a', speech: true, listening: false, folder: true });
    expect(o.step).toBe('listening');
    expect(said(o)).toMatch(/Free: Whisper on this computer.*Paid: Deepgram/);
    o = configured(o, EVERYTHING);
    expect(o.step).toBe('demo');
  });

  it('does not ask about what is already set up', () => {
    expect(named({ model: 'model-a', speech: false, listening: true, folder: true }).step).toBe('voice');
    const all = named(EVERYTHING);
    expect(all.step).toBe('demo');
    expect(texts(all).some((t) => t.includes('Ollama') || t.includes('ElevenLabs') || t.includes('Whisper'))).toBe(false);
  });

  it('ignores a setting that is not the one she asked about', () => {
    const o = named(NOTHING);
    expect(configured(o, { model: null, speech: true, listening: true, folder: false })).toBe(o);
  });

  it('lets a voice and a mic be left for later, one at a time', () => {
    const ready: Ready = { model: 'model-a', speech: false, listening: false, folder: true };
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
      expect(texts(o).some((t) => t.includes('Names are in Settings, at the top'))).toBe(true);
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
    expect(said(renamed)).toContain('Vega it is. Tone is set to blunt.');
    expect(choices(renamed).map((c) => c.label)).toEqual(['Blunt', 'Balanced', 'Gentler']);
    const ready = answer(renamed, 'balanced');
    expect(ready.draft).toEqual({ ...now, agentName: 'Vega', directness: 'balanced' });
    // A rename ends there: no ladder, no example card, no second hello.
    const done = saved(ready, NOTHING);
    expect(done.step).toBe('done');
    expect(done.greet).toBeUndefined();
    expect(said(done)).toContain("Done. I'm Vega, you're Sam");
  });

  it('reads her new name out of whole sentences, and drops the question mark', () => {
    for (const reply of [
      'call me Nova',
      'I want to call you Nova',
      "I'd like to call you Nova",
      'can I call you Nova?',
      'something else, Nova',
      'Something else: Nova.',
      'something else. How about Nova?',
      'How about Nova?',
      'what about Nova',
      "let's go with Nova",
      "Let’s go with Nova!",
      'go with Nova',
      'Nova please',
      'Nova, please',
      'maybe Nova',
      'hmm, Nova I think',
      'call you "Nova"',
      'your name is Nova now',
      'Nova?',
      'Nova!',
    ]) {
      expect(herName(reply), reply).toBe('Nova');
    }
    expect(herName('How about Mary Jane?')).toBe('Mary Jane');
  });

  it('keeps her name for the ways people say keep it', () => {
    for (const reply of [
      'keep it',
      'Keep it!',
      'keep Trillion',
      'Trillion is fine',
      "Trillion's fine",
      'Trillion’s good',
      'no, keep it',
      'nah keep it',
      'stick with Trillion',
      "that's fine",
      'I like it',
      'sounds good',
      'leave it',
      'Trillion',
      "I don't know",
      'you pick',
    ]) {
      const o = run(['Sam', reply]);
      expect(o.step, reply).toBe('directness');
      expect(o.draft.agentName, reply).toBe('Trillion');
      expect(said(o), reply).toContain('Trillion it stays.');
    }
  });

  it('says a plain new name back at once: nothing to check', () => {
    const o = run(['Sam', 'Nova']);
    expect(o.step).toBe('directness');
    expect(said(o)).toMatch(/^Nova it is\./);
  });

  it('checks a name it read out of a sentence before it is used', () => {
    const o = run(['Sam', 'I want to call you Nova']);
    expect(o.step).toBe('agent_check');
    expect(o.draft.agentName).toBe('Trillion'); // not yet
    expect(said(o)).toContain('"Nova"');
    expect(choices(o).map((c) => c.label)).toEqual(['Yes, Nova', 'No']);
    expect(active(o)).toBe(true);

    const yes = answer(o, choices(o)[0]?.value ?? '');
    expect(yes.step).toBe('directness');
    expect(yes.draft.agentName).toBe('Nova');
    expect(yes.lines.at(-2)).toEqual({ who: 'you', text: 'Yes, Nova' });
    expect(said(yes)).toMatch(/^Nova it is\./);

    const no = answer(o, choices(o)[1]?.value ?? '');
    expect(no.step).toBe('agent_name');
    expect(no.draft.agentName).toBe('Trillion');
    expect(said(no)).toBe('What would you like instead?');

    // A typed no, then the name alone.
    const retyped = answer(answer(o, 'no'), 'Vega');
    expect(retyped.draft.agentName).toBe('Vega');
    // "No, Vega" is Vega. A new name typed without a no is checked again.
    expect(answer(o, 'No, Vega').draft.agentName).toBe('Vega');
    const bare = answer(o, 'Vega');
    expect(bare.step).toBe('agent_check');
    expect(bare.heard).toBe('Vega');
    const again = answer(o, 'how about Vega');
    expect(again.step).toBe('agent_check');
    expect(said(again)).toContain('"Vega"');
  });

  it('asks again when a reply reads as a sentence, not a name', () => {
    const o = run(['Sam', 'I really have no strong feelings about it either way']);
    expect(o.step).toBe('agent_name');
    expect(o.draft.agentName).toBe('Trillion');
    expect(said(o)).toContain('just the name');
  });

  it('reads the ways people give their own name', () => {
    for (const [reply, name] of [
      ["I'm Robin", 'Robin'],
      ['I’m Robin', 'Robin'],
      ['my name is Robin', 'Robin'],
      ["my name's Robin.", 'Robin'],
      ["it's Robin", 'Robin'],
      ['call me Rob', 'Rob'],
      ['hi there, I am Robin', 'Robin'],
      ['Robin?', 'Robin'],
      ['Robin here', 'Robin'],
      ["I'm Robin, nice to meet you", 'Robin'],
      ['people call me Rob', 'Rob'],
      ['Robin Okafor', 'Robin Okafor'],
    ] as const) {
      expect(nameFrom(reply), reply).toBe(name);
      expect(run([reply]).draft.userName, reply).toBe(name);
    }
  });

  it('caps a name at a few words', () => {
    expect(nameProblem('Maria de la Cruz')).toBeNull();
    expect(nameProblem('well I suppose you could say it is Sam')).not.toBeNull();
  });

  describe('the check step never takes an answer for a name', () => {
    const checking = (): Onboarding => run(['Sam', 'I want to call you Nova']);

    it('agrees on a yes, with or without the name and thanks', () => {
      for (const reply of [
        'Yes, Nova',
        'yes',
        'Yes!',
        'yeah that\'s right',
        'perfect',
        'that is right',
        "that's it",
        'yes please',
        'okay thanks',
        'sure',
        'yep, Nova please',
        'sounds good',
        'correct',
      ]) {
        const o = answer(checking(), reply);
        expect(o.step, reply).toBe('directness');
        expect(o.draft.agentName, reply).toBe('Nova');
        expect(said(o), reply).toMatch(/^Nova it is\./);
      }
    });

    it('asks what instead on a plain no', () => {
      for (const reply of ['no', 'No.', 'nope', 'nah', 'no thanks', 'not quite', 'wrong']) {
        const o = answer(checking(), reply);
        expect(o.step, reply).toBe('agent_name');
        expect(o.draft.agentName, reply).toBe('Trillion');
        expect(said(o), reply).toBe('What would you like instead?');
      }
    });

    it('takes a no with a name as that name, checked unless it is one plain word', () => {
      expect(answer(checking(), 'no, Vega').draft.agentName).toBe('Vega');
      expect(answer(checking(), 'nope Vega').draft.agentName).toBe('Vega');
      const two = answer(checking(), 'no, Mary Jane');
      expect(two.step).toBe('agent_check');
      expect(two.heard).toBe('Mary Jane');
      const sentence = answer(checking(), 'no, call you Vega');
      expect(sentence.step).toBe('agent_check');
      expect(sentence.heard).toBe('Vega');
      expect(answer(checking(), 'no, something else').step).toBe('agent_name');
    });
  });

  it('never takes the words of her own question as her name', () => {
    for (const reply of ['something else', 'Something else.', 'a new name', 'no', 'nope', 'call me something else', 'a different one']) {
      const o = run(['Sam', reply]);
      expect(o.step, reply).toBe('agent_name');
      expect(o.draft.agentName, reply).toBe('Trillion');
      expect(said(o), reply).toBe('What would you like instead?');
    }
  });

  it('takes only one or two name-like words at once; anything else is checked', () => {
    expect(run(['Sam', 'Nova']).step).toBe('directness');
    expect(run(['Sam', 'Mary Jane']).step).toBe('directness');
    expect(run(['Sam', "O'Neil"]).step).toBe('directness');
    for (const reply of ['Make It Rain', 'Nova 2', 'new Nova', 'R2-D2']) {
      const o = run(['Sam', reply]);
      expect(o.step, reply).toBe('agent_check');
    }
  });

  it('keeps a name that only starts with a hum', () => {
    expect(nameFrom('Um Ji-won')).toBe('Um Ji-won');
    expect(nameFrom('Er Wei')).toBe('Er Wei');
    expect(nameFrom('Uh Kim')).toBe('Uh Kim');
    expect(nameFrom('um, Sam')).toBe('Sam');
    expect(nameFrom('hmm. Sam')).toBe('Sam');
    expect(run(['Um Ji-won']).draft.userName).toBe('Um Ji-won');
  });

  it('does not take yes or no as the user\'s own name', () => {
    for (const reply of ['yes', 'no', 'nope', 'okay', 'hi', 'something else']) {
      const o = run([reply]);
      expect(o.step, reply).toBe('user_name');
      expect(o.draft.userName, reply).toBe('');
      expect(said(o), reply).toBe('I need a name to go on. What should I call you?');
    }
    // Coming back to change names, a yes keeps the one there is.
    const back = answer(begin({ userName: 'Sam', agentName: 'Nova', timezone: '', directness: 'blunt' }), 'yes');
    expect(back.step).toBe('agent_name');
    expect(back.draft.userName).toBe('Sam');
  });

  describe('second review', () => {
    const back = (): Onboarding => begin({ userName: 'Sam', agentName: 'Nova', timezone: '', directness: 'blunt' });

    it('keeps a returning name on a yes, and reads a new one out of what follows it', () => {
      for (const reply of ['yes', 'yes, Sam', 'Yes Sam!', 'yep thats me', "yeah, that's me", 'sure']) {
        const o = answer(back(), reply);
        expect(o.step, reply).toBe('agent_name');
        expect(o.draft.userName, reply).toBe('Sam');
        expect(said(o), reply).toMatch(/^Sam\. And I'm Nova/);
      }
      expect(answer(back(), 'Yes, but call me Samuel').draft.userName).toBe('Samuel');
      expect(answer(back(), 'yes, Samuel').draft.userName).toBe('Samuel');
    });

    it('reads her name back without a yes or no in front, or "is good" after', () => {
      for (const [reply, name] of [
        ['sure, Nova', 'Nova'],
        ['Yes, Nova', 'Nova'],
        ['no, Vega', 'Vega'],
        ['Nova is good', 'Nova'],
        ['Nova is fine', 'Nova'],
        ['Nova sounds good', 'Nova'],
      ] as const) {
        const o = run(['Sam', reply]);
        expect(o.step, reply).toBe('agent_check');
        expect(o.heard, reply).toBe(name);
        expect(said(o), reply).toMatch(new RegExp(`^"${name}", then\\?`));
        expect(answer(o, 'yes').draft.agentName, reply).toBe(name);
      }
    });

    it('keeps her name when the user does not mind', () => {
      for (const reply of ['i dont mind', "i don't mind", 'dunno', 'no idea', 'up to you', "it's up to you", 'whatever you like']) {
        const o = run(['Sam', reply]);
        expect(o.step, reply).toBe('directness');
        expect(o.draft.agentName, reply).toBe('Trillion');
      }
    });

    it('at the check, takes the name said again as a yes, keep it as keep it, and a yes with a new name as that name', () => {
      const checking = (): Onboarding => run(['Sam', 'how about Nova']);
      for (const reply of ['Nova', 'nova', 'Nova!']) {
        const o = answer(checking(), reply);
        expect(o.step, reply).toBe('directness');
        expect(o.draft.agentName, reply).toBe('Nova');
      }
      for (const reply of ['keep Trillion', 'Trillion is fine']) {
        const o = answer(checking(), reply);
        expect(o.step, reply).toBe('directness');
        expect(o.draft.agentName, reply).toBe('Trillion');
        expect(said(o), reply).toContain('Trillion it stays.');
      }
      const vega = answer(checking(), 'yes but Vega');
      expect(vega.step).toBe('agent_check');
      expect(vega.heard).toBe('Vega');
      expect(answer(vega, 'yes').draft.agentName).toBe('Vega');
    });

    it('drops a greeting before the user name, with or without punctuation', () => {
      for (const reply of ['hey Sam', 'hi Sam', 'hello, Sam', 'Hey there Sam', 'hi! Sam']) {
        expect(nameFrom(reply), reply).toBe('Sam');
        expect(run([reply]).draft.userName, reply).toBe('Sam');
      }
      // A greeting alone is not a name.
      expect(run(['hey']).step).toBe('user_name');
    });
  });

  describe('a folder to work in', () => {
    const PLACES = [
      { label: 'Home', path: '/Users/sam', kind: 'home' as const },
      { label: 'Desktop', path: '/Users/sam/Desktop', kind: 'folder' as const },
      { label: 'Documents', path: '/Users/sam/Documents', kind: 'folder' as const },
      { label: 'Downloads', path: '/Users/sam/Downloads', kind: 'folder' as const },
      { label: 'Backup', path: '/Volumes/Backup', kind: 'drive' as const },
    ];
    const READY: Ready = { model: 'model-a', speech: true, listening: true, folder: false, places: folderOffers(PLACES) };
    const asked = (): Onboarding => named(READY);

    it('offers only the Desktop and Documents, never the home folder or a drive', () => {
      expect(folderOffers(PLACES)).toEqual([
        { label: 'Desktop', path: '/Users/sam/Desktop' },
        { label: 'Documents', path: '/Users/sam/Documents' },
      ]);
      expect(folderOffers([])).toEqual([]);
    });

    it('comes after the voice and the ears, before the card, and says what sharing gives her', () => {
      const o = asked();
      expect(o.step).toBe('folder');
      expect(active(o)).toBe(true);
      expect(said(o)).toContain('read and change');
      expect(said(o)).toContain('home folder');
      expect(choices(o).map((c) => c.label)).toEqual(['Share Desktop', 'Share Documents', 'Another folder', 'Not now']);
      expect(choices(o)[2]?.value).toBe(OPEN);
      expect(choices(o)[3]?.value).toBe(LATER);
      expect(placeholder(o)).toContain('not now');
    });

    it('is not asked when a folder is already shared', () => {
      expect(named({ ...READY, folder: true }).step).toBe('demo');
    });

    it('waits on the app to share the folder the user tapped, with change access', () => {
      const o = asked();
      const tapped = decide(o, choices(o)[0]?.value ?? '', READY);
      expect(tapped.step).toBe('sharing');
      expect(tapped.sharing).toEqual({ label: 'Desktop', path: '/Users/sam/Desktop' });
      expect(tapped.lines.at(-1)).toEqual({ who: 'you', text: 'Share Desktop' });
      // Still the script's: what is typed meanwhile never goes to the model.
      expect(active(tapped)).toBe(true);
      expect(answer(tapped, 'hello?')).toBe(tapped);
      expect(decide(tapped, 'hello?', READY)).toBe(tapped);

      const done = sharedFolder(tapped, '', { ...READY, folder: true });
      expect(done.step).toBe('demo');
      expect(done.sharing).toBeUndefined();
      expect(texts(done).some((t) => t.startsWith('Desktop is shared.') && t.includes('change'))).toBe(true);
    });

    it('says why a share was refused and asks again', () => {
      const tapped = decide(asked(), choices(asked())[1]?.value ?? '', READY);
      const refused = sharedFolder(tapped, 'A home folder is never shared: it holds everything in it.', READY);
      expect(refused.step).toBe('folder');
      expect(refused.sharing).toBeUndefined();
      expect(said(refused)).toContain('A home folder is never shared');
      expect(said(refused)).toContain('Nothing was shared');
    });

    it('never shares on typed words: only a tap does that', () => {
      const o = asked();
      for (const typed of ['Desktop', 'yes', 'share Documents']) expect(decide(o, typed, READY)).toBe(o);
      // A path that was not offered is not shared either.
      expect(decide(o, '\u0000share:/Users/sam', READY)).toBe(o);
    });

    it('can be left for later', () => {
      for (const reply of [LATER, 'not now', 'skip', 'nope', 'nah', 'no thanks', 'maybe later', 'not yet']) {
        const o = decide(asked(), reply, READY);
        expect(o.step).toBe('demo');
        expect(texts(o)).toContain('No folder for now. Settings, Shared folders, when you want one.');
      }
    });

    it('moves on when a folder is shared in Settings meanwhile', () => {
      const o = configured(asked(), { ...READY, folder: true });
      expect(o.step).toBe('demo');
      expect(configured(asked(), READY).step).toBe('folder');
    });

    it('offers the places that arrive after she asked', () => {
      const late = named({ ...READY, places: [] });
      expect(late.places).toEqual([]);
      const found = placesFound(late, READY.places ?? []);
      expect(choices(found).map((c) => c.label)).toEqual(['Share Desktop', 'Share Documents', 'Another folder', 'Not now']);
      // Only at the folder step, and never over places already offered.
      expect(placesFound(named(EVERYTHING), READY.places ?? [])).toEqual(named(EVERYTHING));
      const offered = asked();
      expect(placesFound(offered, [{ label: 'Desktop', path: '/x' }])).toBe(offered);
    });

    it('does not ask while the shares are not read yet, nor say one was shared', () => {
      const unknown = named({ ...READY, folder: null });
      expect(unknown.step).toBe('demo');
      expect(texts(unknown).some((t) => t.includes('a folder is shared'))).toBe(false);
      expect(configured(asked(), { ...READY, folder: null }).step).toBe('folder');
    });

    it('offers Settings alone when there is no Desktop or Documents', () => {
      const o = named({ ...READY, places: [] });
      expect(choices(o).map((c) => c.label)).toEqual(['Choose a folder', 'Not now']);
    });
  });

  it('says once who she trusts, and that a tap is how she checks, on every way out of the ladder', () => {
    const trust = (o: Onboarding): boolean =>
      texts(o).some((t) => t.includes('whoever is at this computer') && t.includes('tap'));
    expect(trust(decide(named(EVERYTHING), APPROVE, EVERYTHING))).toBe(true);
    expect(trust(decide(named(EVERYTHING), DECLINE, EVERYTHING))).toBe(true);
    expect(trust(decide(named(NOTHING), 'skip', NOTHING))).toBe(true);
    // Putting the names off ends it too.
    for (const answers of [[], ['Sam'], ['Sam', 'Nova']]) expect(trust(answer(run(answers), SKIP))).toBe(true);
    expect(texts(decide(named(EVERYTHING), APPROVE, EVERYTHING)).filter((t) => t.includes('whoever')).length).toBe(1);
  });

  it('ignores answers once it is over', () => {
    const done = decide(named(EVERYTHING), APPROVE, EVERYTHING);
    expect(active(done)).toBe(false);
    expect(answer(done, 'hello')).toBe(done);
    expect(decide(done, 'yes', EVERYTHING)).toBe(done);
  });
});
