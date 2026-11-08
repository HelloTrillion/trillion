import { describe, expect, it } from 'vitest';
import { nodeId, parseMindMap, parseObserveEvent } from '../../src/mind/contract';

describe('the skeleton, as the contract has it', () => {
  it('keeps what it understands and drops what it does not', () => {
    const map = parseMindMap({
      agent: 'Nova',
      regions: [{ id: 'memory', label: 'Memory', count: 2 }, { id: 'dreams', label: 'Dreams', count: 1 }],
      nodes: [
        { id: 'core', type: 'core', region: 'core', label: 'Nova', size: 3, freshness: 1, extra: {} },
        { id: 'mem:a', type: 'memory', region: 'memory', label: 'likes tea', size: 9, freshness: -1, extra: { kind: 'fact', source: 'user_statement' } },
        { id: 'mem:b', type: 'memory', region: 'memory', label: 'b' },
        { id: 'mem:a', type: 'memory', region: 'memory', label: 'duplicate' },
        { id: 'x:1', type: 'satellite', region: 'memory', label: 'unknown type' },
        { id: 'x:2', type: 'memory', region: 'elsewhere', label: 'unknown region' },
        'not a node',
      ],
      edges: [
        { source: 'mem:a', target: 'mem:b', kind: 'similarity', weight: 0.71 },
        { source: 'mem:a', target: 'x:1', kind: 'similarity', weight: 0.9 },
        { source: 'mem:a', target: 'mem:b', kind: 'telepathy', weight: 1 },
        { source: 'core', target: 'mem:a', kind: 'recall' },
      ],
      stats: { memory_total: 640, memory_shown: 500, sources: { memory: 'no_meaning', rim: 'error', agents: 'sideways' } },
    });
    expect(map).not.toBeNull();
    expect(map!.agent).toBe('Nova');
    expect(map!.nodes.map((n) => n.id)).toEqual(['core', 'mem:a', 'mem:b']);
    const a = map!.nodes[1]!;
    expect(a.size).toBe(3);
    expect(a.freshness).toBe(0);
    expect(a.extra).toEqual({ kind: 'fact', source: 'user_statement' });
    const b = map!.nodes[2]!;
    expect(b.size).toBe(1);
    expect(b.freshness).toBe(1);
    expect(map!.edges).toEqual([
      { source: 'mem:a', target: 'mem:b', kind: 'similarity', weight: 0.71 },
      { source: 'core', target: 'mem:a', kind: 'recall', weight: 1 },
    ]);
    expect(map!.regions.map((r) => r.id)).toEqual(['memory']);
    expect(map!.stats).toEqual({ memory_total: 640, memory_shown: 500, sources: { memory: 'no_meaning', rim: 'error' } });
  });

  it('is fine with regions, edges and stats missing', () => {
    const map = parseMindMap({ nodes: [{ id: 'mem:a', type: 'memory', region: 'memory', label: 'a' }] });
    expect(map).toMatchObject({ agent: '', regions: [], edges: [], stats: { memory_total: 1, memory_shown: 1, sources: {} } });
  });

  it('is null when what arrived is not a skeleton', () => {
    expect(parseMindMap(null)).toBeNull();
    expect(parseMindMap({ detail: 'Not Found' })).toBeNull();
    expect(parseMindMap([1, 2])).toBeNull();
  });
});

describe('observe events', () => {
  it('reads each event in the table', () => {
    expect(parseObserveEvent({ type: 'turn', conversation_id: 'c1' })).toEqual({ type: 'turn', conversation_id: 'c1' });
    expect(parseObserveEvent({ type: 'turn' })).toEqual({ type: 'turn', conversation_id: null });
    expect(parseObserveEvent({ type: 'tool.start', name: 'web_search' })).toEqual({ type: 'tool.start', name: 'web_search' });
    expect(parseObserveEvent({ type: 'tool.end', name: 'web_search', ok: false })).toEqual({ type: 'tool.end', name: 'web_search', ok: false });
    expect(parseObserveEvent({ type: 'memory.written', id: 'mem_1' })).toEqual({ type: 'memory.written', id: 'mem_1' });
    expect(parseObserveEvent({ type: 'memory.recalled', ids: ['a', 3, '', 'b'] })).toEqual({ type: 'memory.recalled', ids: ['a', 'b'] });
    expect(parseObserveEvent({ type: 'helper.started', id: 'h1' })).toEqual({ type: 'helper.started', id: 'h1' });
    expect(parseObserveEvent({ type: 'helper.done', id: 'h1', ok: true })).toEqual({ type: 'helper.done', id: 'h1', ok: true });
    expect(parseObserveEvent({ type: 'alert', critical: true })).toEqual({ type: 'alert', critical: true });
  });

  it('ignores the handshake, unknown types and events with no ids', () => {
    for (const raw of [{ type: 'observe.ready' }, { type: 'mood' }, { type: 'tool.start' }, { type: 'memory.recalled', ids: [] }, 'turn', null]) {
      expect(parseObserveEvent(raw)).toBeNull();
    }
  });

  it('namespaces ids the way the map does, once', () => {
    expect(nodeId('mem', 'mem_0123')).toBe('mem:mem_0123');
    expect(nodeId('mem', 'mem:mem_0123')).toBe('mem:mem_0123');
    expect(nodeId('tool', 'web_search')).toBe('tool:web_search');
  });
});
