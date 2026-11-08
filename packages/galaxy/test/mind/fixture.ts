/** A small mind in the shape of the contract, for the Living Mind's tests. */

import type { MindEdge, MindMap, MindNode } from '../../src/mind/contract';
import type { MindPalette } from '../../src/mind/palette';

const node = (id: string, type: MindNode['type'], region: MindNode['region'], extra: Record<string, unknown> = {}): MindNode => ({
  id, type, region, label: id, size: 1, freshness: 0.8, extra,
});

export function smallMind(memories = 12): MindMap {
  const nodes: MindNode[] = [
    node('core', 'core', 'core'),
    node('ring:stable', 'ring', 'core', { chars: 4000 }),
    node('ring:turn', 'ring', 'core', { chars: 600 }),
    node('chat:c1', 'chat', 'working', { kind: 'chat', last_ms: 1 }),
    node('chat:c2', 'chat', 'working', { kind: 'chat', last_ms: 2 }),
    node('helper:h1', 'helper', 'agents', { kind: 'helper', state: 'idle' }),
    node('know:SOUL.md', 'knowledge', 'knowledge', { kind: 'persona' }),
    node('tool:web_search', 'tool', 'rim', { category: 'web', risk: 'low' }),
    node('tool:read_file', 'tool', 'rim', { category: 'files', risk: 'low' }),
    node('conn:google', 'connector', 'rim', { status: 'ok' }),
  ];
  const edges: MindEdge[] = [
    { source: 'core', target: 'helper:h1', kind: 'handoff', weight: 1 },
    { source: 'know:SOUL.md', target: 'ring:stable', kind: 'feeds', weight: 1 },
    { source: 'conn:google', target: 'tool:web_search', kind: 'serves', weight: 1 },
  ];
  for (let i = 0; i < memories; i++) {
    nodes.push(node(`mem:m${i}`, 'memory', 'memory', { kind: ['fact', 'project'][i % 2], source: 'user_statement' }));
    if (i > 0) edges.push({ source: `mem:m${i}`, target: `mem:m${(i * 5) % i}`, kind: 'similarity', weight: 0.7 });
  }
  edges.push({ source: 'core', target: 'mem:m0', kind: 'recall', weight: 1 });
  return {
    agent: 'Nova',
    regions: [{ id: 'memory', label: 'Memory', count: memories }],
    nodes,
    edges,
    stats: { memory_total: memories, memory_shown: memories, sources: { core: 'ok', memory: 'ok' } },
  };
}

/** Named CSS colours: the palette's shape, without a colour of the design's own. */
export const PALETTE: MindPalette = {
  core: 'aquamarine', memory: 'violet', working: 'cyan', agents: 'pink', knowledge: 'orange',
  rim: 'slategray', void: 'black', danger: 'red',
};
