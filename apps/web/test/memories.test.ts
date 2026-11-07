import { describe, expect, it } from 'vitest';
import {
  askedText,
  countText,
  editError,
  groupMemories,
  matches,
  memoriesSummary,
  resolvedText,
  sourceText,
  typeLabel,
  whenText,
  type Memory,
} from '../src/memories';

const memory = (over: Partial<Memory> = {}): Memory => ({
  id: 'mem_000000000001',
  type: 'user',
  text: 'I take my coffee black.',
  source: 'user_statement',
  moves: false,
  version: 1,
  created_ms: 1_000,
  as_of_ms: 1_000,
  supersedes: null,
  ...over,
});

describe('groupMemories', () => {
  const list = [
    memory({ id: 'mem_a', type: 'reference', text: 'The invoices are in the shared drive.', as_of_ms: 5 }),
    memory({ id: 'mem_b', type: 'user', text: 'I take my coffee black.', as_of_ms: 1 }),
    memory({ id: 'mem_c', type: 'user', text: 'My sister is called Ana.', as_of_ms: 9 }),
    memory({ id: 'mem_d', type: 'feedback', text: 'Keep answers short.', as_of_ms: 3 }),
  ];

  it('keeps a fixed group order, newest first within each, and leaves out empty groups', () => {
    const groups = groupMemories(list, '');
    expect(groups.map((g) => g.type)).toEqual(['user', 'feedback', 'reference']);
    expect(groups[0]?.items.map((m) => m.id)).toEqual(['mem_c', 'mem_b']);
    expect(groups[0]?.label).toBe('About you');
  });

  it('filters by every word of the search, ignoring case and accents', () => {
    expect(groupMemories(list, 'COFFEE')[0]?.items.map((m) => m.id)).toEqual(['mem_b']);
    expect(groupMemories(list, 'shared invoices').map((g) => g.type)).toEqual(['reference']);
    expect(groupMemories([memory({ text: 'I like the café downstairs.' })], 'cafe')).toHaveLength(1);
    expect(groupMemories(list, 'coffee tea')).toEqual([]);
  });

  it('finds a memory by its group name too', () => {
    expect(matches(memory({ type: 'project', text: 'The garden fence.' }), 'projects')).toBe(true);
    expect(matches(memory(), '   ')).toBe(true);
  });
});

describe('words', () => {
  it('says where a memory came from, and when it corrected an earlier one', () => {
    expect(sourceText(memory())).toBe('You said this');
    expect(sourceText(memory({ source: 'imported_claim' }))).toBe('From something she read');
    expect(sourceText(memory({ source: 'agent_inference', supersedes: 'mem_x' }))).toBe('Her own guess · corrects an earlier one');
  });

  it('says when it was saved', () => {
    const now = Date.UTC(2026, 10, 7, 12, 0);
    expect(whenText(now - 30_000, now)).toBe('Saved just now');
    expect(whenText(now - 2 * 3_600_000, now)).toBe('Saved 2 h ago');
  });

  it('names every type', () => {
    expect((['user', 'feedback', 'project', 'reference'] as const).map(typeLabel)).toEqual([
      'About you',
      'How you like things done',
      'Projects',
      'Where things are',
    ]);
  });

  it('summarises and counts', () => {
    const state = (n: number) => ({ memories: Array.from({ length: n }, () => memory()), max_text: 1200, diverged: false, unreadable: 0 });
    expect(memoriesSummary(null)).toBe('');
    expect(memoriesSummary(state(0))).toBe('Nothing yet');
    expect(memoriesSummary(state(2))).toBe('2 remembered');
    expect(countText(1, 1, '')).toBe('1 memory');
    expect(countText(0, 4, 'tea')).toBe('Nothing matches.');
    expect(countText(2, 4, 'tea')).toBe('2 of 4 match');
  });

  it('turns the core’s codes into plain lines', () => {
    expect(editError('unchanged')).toBe('Nothing was changed.');
    expect(editError('something new')).toBe('That did not save. Try again.');
    expect(askedText('confirmation_required', null)).toBe('Approve the card in the conversation to forget it.');
    expect(askedText('refused', 'precondition_failed')).toBe('That memory is already forgotten or replaced.');
    expect(resolvedText('succeeded')).toMatch(/^Forgotten\./);
    expect(resolvedText('cancelled')).toMatch(/^Kept\./);
    expect(resolvedText('refused')).toMatch(/changed/);
  });
});
