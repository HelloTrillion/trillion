import { describe, expect, it } from 'vitest';
import { changedFile, refusal, skillState, skillsSummary, toolLabel, unlistedText, type SkillRow, type SkillsState } from '../src/skills';

const row = (state: SkillRow['state'], reason = ''): SkillRow => ({
  id: 'curated:a',
  source: 'curated',
  folder: '',
  name: 'a',
  description: 'd',
  license: '',
  tools: [],
  has_scripts: false,
  state,
  reason,
  fingerprint: 'sha256:x',
});

const list = (...skills: SkillRow[]): SkillsState => ({ folders: [], skills, problems: [], unlisted: 0 });

describe('skills', () => {
  it('says plainly what state each skill is in', () => {
    expect(skillState(row('off'))).toBe('Off');
    expect(skillState(row('on'))).toBe('On');
    expect(skillState(row('changed'))).toBe('Changed since you turned it on');
    expect(skillState(row('invalid', 'its header has no name'))).toBe("Can't be used: its header has no name");
    expect(skillState(row('invalid'))).toBe("Can't be used");
  });

  it('sums them up for the sidebar', () => {
    expect(skillsSummary(null)).toBe('None on');
    expect(skillsSummary(list(row('off')))).toBe('None on');
    expect(skillsSummary(list(row('on'), row('on'), row('off')))).toBe('2 on');
    expect(skillsSummary(list(row('on'), row('changed')))).toBe('1 on, 1 changed');
    expect(skillsSummary(list(row('changed')))).toBe('0 on, 1 changed');
  });

  it('names the tools a skill mentions, and what the missing ones need', () => {
    expect(toolLabel({ name: 'web_fetch', available: true })).toBe('web_fetch');
    expect(toolLabel({ name: 'gmail_send', available: false, needs: 'google' })).toBe('gmail_send (connect Google)');
    expect(toolLabel({ name: 'Bash', available: false })).toBe('Bash (not available in Trillion)');
  });

  it('says how many skills that are on her list leaves out', () => {
    expect(unlistedText(0)).toBe('');
    expect(unlistedText(1)).toBe('1 more skill is on than her instructions list');
    expect(unlistedText(3)).toBe('3 more skills are on than her instructions list');
  });
});

describe('skill words', () => {
  it('turns a refusal into one sentence', () => {
    expect(refusal('that skill changed since this page showed it; look again')).toBe('That skill changed since this page showed it; look again.');
    expect(refusal('not a folder.')).toBe('Not a folder.');
    expect(refusal('  ends with dots... ')).toBe('Ends with dots.');
    expect(refusal(undefined)).toBe('That did not work.');
    expect(refusal([{ msg: 'x' }])).toBe('That did not work.');
  });

  it('names the file to read again, relative to the folder when it can', () => {
    const at = (id: string, folder: string, source: SkillRow['source'] = 'folder') => changedFile({ id, folder, source });
    expect(at('folder:/home/me/skills/pdf-forms', '/home/me/skills')).toBe('pdf-forms/SKILL.md');
    expect(at('folder:/home/me/skills/pdf-forms', '/home/me/skills/')).toBe('pdf-forms/SKILL.md');
    expect(at('folder:/home/me/skills', '/home/me/skills')).toBe('/home/me/skills/SKILL.md');
    expect(at('folder:/elsewhere/x', '/home/me/skills')).toBe('/elsewhere/x/SKILL.md');
    expect(at('folder:/home/me/skillset/x', '/home/me/skills')).toBe('/home/me/skillset/x/SKILL.md');
    expect(at('curated:a', '', 'curated')).toBe('');
  });
});
