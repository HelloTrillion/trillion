import { describe, expect, it } from 'vitest';
import { type MindMap, type MindNode, parseMindMap } from '@hellotrillion/galaxy';
import { TOKEN_KEY, deepLink, deepLinkNode, nodeDetail, pageTitle, rankMatches, statsLine, takeToken } from '../src/mind-page';

const store = (initial: Record<string, string> = {}) => {
  const data = new Map(Object.entries(initial));
  return { data, getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) };
};

describe('the token', () => {
  it('comes from the fragment, is kept, and leaves the address bar', () => {
    const s = store();
    expect(takeToken('#token=abc-123_x', s)).toEqual({ token: 'abc-123_x', hash: '' });
    expect(s.data.get(TOKEN_KEY)).toBe('abc-123_x');
  });

  it('keeps a deep link that came with it', () => {
    expect(takeToken('#token=abc&node=mem%3Amem_1', store())).toEqual({ token: 'abc', hash: '#node=mem%3Amem_1' });
    expect(takeToken('#node=core&token=abc', store())).toEqual({ token: 'abc', hash: '#node=core' });
  });

  it('falls back to the kept one, as the main page keeps it, and to nothing', () => {
    expect(takeToken('#node=core', store({ [TOKEN_KEY]: 'kept' }))).toEqual({ token: 'kept', hash: '#node=core' });
    expect(takeToken('', store())).toEqual({ token: null, hash: '' });
    expect(takeToken('', null)).toEqual({ token: null, hash: '' });
    expect(takeToken('', store({ [TOKEN_KEY]: 'not a token!' })).token).toBeNull();
  });
});

describe('the deep link', () => {
  it('reads #node=<id> and writes it back the same', () => {
    expect(deepLinkNode('#node=mem%3Amem_0123')).toBe('mem:mem_0123');
    expect(deepLinkNode('#token=x&node=know%3Apersona%2Fprompts%2Fa.md')).toBe('know:persona/prompts/a.md');
    expect(deepLinkNode(deepLink('conn:mcp:notes'))).toBe('conn:mcp:notes');
    expect(deepLinkNode('#node=%E0%A4%A')).toBeNull();
    expect(deepLinkNode('#token=x')).toBeNull();
    expect(deepLink(null)).toBe('');
  });
});

const map = (sources: Record<string, string>, extra: Partial<{ memory_total: number; memory_shown: number }> = {}): MindMap =>
  parseMindMap({
    agent: 'Nova',
    nodes: [{ id: 'core', type: 'core', region: 'core', label: 'Nova' }, { id: 'mem:a', type: 'memory', region: 'memory', label: 'a' }],
    edges: [],
    stats: { memory_total: 1, memory_shown: 1, ...extra, sources },
  })!;

describe('the stats line', () => {
  it('gives the counts, and how many memories are left out', () => {
    expect(statsLine(map({})).summary).toBe('2 nodes · 0 links · 1 memory');
    expect(statsLine(map({}, { memory_total: 640, memory_shown: 500 })).summary).toBe('2 nodes · 0 links · 500 of 640 memories (the newest)');
  });

  it('names each empty or failed region and why', () => {
    expect(statsLine(map({ core: 'ok', memory: 'no_meaning', agents: 'empty', rim: 'error', working: 'empty' })).notes).toEqual([
      'No memories by meaning: install the meaning extra',
      'No chats yet',
      'No helpers yet',
      "Tools and connectors couldn't be read (see her log)",
    ]);
    expect(statsLine(map({ memory: 'empty', knowledge: 'error' })).notes).toEqual(['No memories yet', "Knowledge couldn't be read (see her log)"]);
  });
});

const n = (id: string, label: string, type: MindNode['type'] = 'memory', extra: Record<string, unknown> = {}): MindNode => ({
  id, type, region: 'memory', label, size: 1, freshness: 1, extra,
});

describe('search', () => {
  it('ranks a label prefix over an id prefix over a match inside, then shorter first', () => {
    const nodes = [n('mem:1', 'likes green tea'), n('mem:2', 'tea at four'), n('tool:tea', 'kettle', 'tool'), n('mem:3', 'tea')];
    expect(rankMatches(nodes, 'TEA').map((x) => x.id)).toEqual(['mem:3', 'mem:2', 'tool:tea', 'mem:1']);
    expect(rankMatches(nodes, '  ')).toEqual([]);
    expect(rankMatches(nodes, 'coffee')).toEqual([]);
  });
});

describe('words on the page', () => {
  it('gives a tooltip line per type', () => {
    expect(nodeDetail(n('mem:1', 'x', 'memory', { kind: 'project', source: 'user_statement' }))).toBe('project · user statement');
    expect(nodeDetail(n('tool:x', 'x', 'tool', { category: 'web', risk: 'low' }))).toBe('web · low risk');
    expect(nodeDetail(n('conn:google', 'x', 'connector', { status: 'ok' }))).toBe('ok');
  });

  it('takes her name only from the payload', () => {
    expect(pageTitle('Nova')).toBe("Nova's mind");
    expect(pageTitle('')).toBe('Her mind');
  });
});
