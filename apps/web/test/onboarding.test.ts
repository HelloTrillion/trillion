import { describe, expect, it } from 'vitest';
import {
  type Onboarding,
  type Profile,
  SKIP,
  active,
  answer,
  begin,
  choices,
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

  it('says what was saved and what comes next', () => {
    const ready = run(['Sam', 'Nova', 'blunt']);
    const withModel = saved(ready, true);
    expect(withModel.step).toBe('done');
    expect(withModel.lines.at(-2)?.text).toBe(
      "Done. I'm Nova, you're Sam, and I have you in America/Chicago. Change any of it from Names, at the top.",
    );
    expect(said(withModel)).toContain('last scripted line');
    expect(said(saved(ready, false))).toContain('I need a model to think with');
    const nowhere = saved(run(['Sam', 'Nova', 'blunt'], { ...FRESH, timezone: '' }), true);
    expect(nowhere.lines.at(-2)?.text).toContain("you're Sam. Change");
  });

  it('can be put off at any step, and says where to find it again', () => {
    for (const answers of [[], ['Sam'], ['Sam', 'Nova']]) {
      const o = answer(run(answers), SKIP);
      expect(o.step).toBe('skipped');
      expect(said(o)).toContain('The Names button is at the top');
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
    expect(answer(renamed, 'balanced').draft).toEqual({ ...now, agentName: 'Vega', directness: 'balanced' });
  });

  it('ignores answers once it is over', () => {
    const done = saved(run(['Sam', 'Nova', 'blunt']), true);
    expect(answer(done, 'hello')).toBe(done);
  });
});
