/**
 * Made-up data for looking at the Living Mind in development only:
 * `pnpm --filter @hellotrillion/web dev`, then http://127.0.0.1:5174/mind.html?fixture
 * (add `&events` for a stream of made-up events). mind.ts loads this file only behind
 * `import.meta.env.DEV`, so a production build neither contains nor can reach it.
 */

import type { MindEdge, MindNode, ObserveEvent } from '@hellotrillion/galaxy';
import type { Detail } from './mind-inspector';

const TOPICS = [
  'prefers short answers in the morning', 'is planning a garden this spring', 'the team meets on Tuesdays',
  'likes tea more than coffee', 'is learning to play the piano', 'the project deadline moved to next month',
  'keeps receipts in the finance folder', 'wants reminders an hour early', 'reads science fiction at night',
  'the car is due for a service', 'is training for a 10k run', 'prefers metric units',
  'the quarterly report goes to the board', 'has a dentist appointment soon', 'wants weekly summaries on Friday',
];
const KINDS = ['fact', 'preference', 'project', 'person', 'routine'];

function rand(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const node = (id: string, type: MindNode['type'], region: MindNode['region'], label: string, extra: Record<string, unknown> = {}, size = 1, freshness = 1): unknown => ({
  id, type, region, label, size, freshness, extra,
});

export function fixtureSkeleton(memories = 160): unknown {
  const r = rand(7);
  const nodes: unknown[] = [
    node('core', 'core', 'core', 'Nova', {}, 3),
    node('ring:stable', 'ring', 'core', 'Stable prompt', { chars: 9200 }),
    node('ring:turn', 'ring', 'core', 'This turn', { chars: 1400 }),
  ];
  const edges: MindEdge[] = [];
  const memIds: string[] = [];
  for (let i = 0; i < memories; i++) {
    const id = `mem:mem_${i.toString(16).padStart(12, '0')}`;
    memIds.push(id);
    const topic = TOPICS[i % TOPICS.length] ?? '';
    nodes.push(node(id, 'memory', 'memory', `${topic} (${i})`, { kind: KINDS[i % KINDS.length], source: i % 3 ? 'user_statement' : 'chat_note' }, 1, Math.pow(0.5, (r() * 120) / 30)));
  }
  // Similar memories: those about the same topic, top three each.
  const degree = new Map<string, number>();
  for (let i = 0; i < memories; i++) {
    for (let k = 1; k <= 3; k++) {
      const j = i + k * TOPICS.length;
      if (j >= memories) break;
      edges.push({ source: memIds[i]!, target: memIds[j]!, kind: 'similarity', weight: 0.6 + r() * 0.3 });
      degree.set(memIds[i]!, (degree.get(memIds[i]!) ?? 0) + 1);
      degree.set(memIds[j]!, (degree.get(memIds[j]!) ?? 0) + 1);
    }
  }
  for (const id of [...degree.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([id]) => id)) edges.push({ source: 'core', target: id, kind: 'recall', weight: 1 });
  for (let i = 0; i < 10; i++) nodes.push(node(`chat:c${i}`, 'chat', 'working', `Chat about ${TOPICS[i]}`, { kind: 'chat', last_ms: Date.now() - i * 3_600_000 }));
  const helpers = [['h_research', 'Researcher', 'helper', 'working'], ['h_inbox', 'Inbox sorter', 'routine', 'idle'], ['h_watch', 'Price watcher', 'watcher', 'idle'], ['h_daily', 'Morning brief', 'timed', 'idle']];
  for (const [id, name, kind, state] of helpers) {
    nodes.push(node(`helper:${id}`, 'helper', 'agents', name ?? '', { kind, state }, 1.6));
    edges.push({ source: 'core', target: `helper:${id}`, kind: 'handoff', weight: 1 });
  }
  edges.push({ source: 'helper:h_inbox', target: 'conn:google', kind: 'uses', weight: 1 });
  for (const [path, kind] of [['SOUL.md', 'persona'], ['IDENTITY.md', 'persona'], ['USER.md', 'persona'], ['persona/prompts/voice.md', 'prompt'], ['persona/prompts/tools.md', 'prompt'], ['skill:summaries', 'skill']]) {
    nodes.push(node(`know:${path}`, 'knowledge', 'knowledge', path ?? '', { kind }));
    edges.push({ source: `know:${path}`, target: 'ring:stable', kind: 'feeds', weight: 1 });
  }
  const tools: [string, string][] = [
    ['web_search', 'web'], ['fetch_page', 'web'], ['read_file', 'files'], ['write_file', 'files'], ['list_folder', 'files'],
    ['set_reminder', 'time'], ['timer', 'time'], ['calculator', 'math'], ['gmail_search', 'google'], ['calendar_list', 'google'],
    ['slack_post', 'slack'], ['notes_search', 'mcp'], ['notes_add', 'mcp'],
  ];
  for (const [name, category] of tools) nodes.push(node(`tool:${name}`, 'tool', 'rim', name.replace(/_/g, ' '), { category, risk: name.startsWith('write') || name.endsWith('post') ? 'medium' : 'low' }));
  for (const [id, status, served] of [['conn:google', 'ok', ['gmail_search', 'calendar_list']], ['conn:slack', 'off', ['slack_post']], ['conn:mcp:notes', 'ok', ['notes_search', 'notes_add']]] as const) {
    nodes.push(node(id, 'connector', 'rim', id.replace(/^conn:(mcp:)?/, ''), { status }));
    for (const t of served) edges.push({ source: id, target: `tool:${t}`, kind: 'serves', weight: status === 'ok' ? 1 : 0.3 });
  }
  return {
    agent: 'Nova',
    regions: [
      { id: 'core', label: 'Core', count: 3 }, { id: 'memory', label: 'Memory', count: memories }, { id: 'working', label: 'Recent chats', count: 10 },
      { id: 'agents', label: 'Helpers', count: helpers.length }, { id: 'knowledge', label: 'Knowledge', count: 6 }, { id: 'rim', label: 'Tools', count: tools.length + 3 },
    ],
    nodes,
    edges,
    stats: { memory_total: memories + 40, memory_shown: memories, sources: { core: 'ok', memory: 'ok', working: 'ok', agents: 'ok', knowledge: 'ok', rim: 'ok' } },
  };
}

export function fixtureDetail(id: string, skeleton: { nodes: readonly MindNode[]; edges: readonly MindEdge[] }): Detail | null {
  const n = skeleton.nodes.find((x) => x.id === id);
  if (!n) return null;
  switch (n.type) {
    case 'memory': {
      const similar = skeleton.edges
        .filter((e) => e.kind === 'similarity' && (e.source === id || e.target === id))
        .map((e) => {
          const other = e.source === id ? e.target : e.source;
          return { id: other, label: skeleton.nodes.find((x) => x.id === other)?.label ?? other, score: e.weight };
        });
      return { text: `The user ${n.label}. A made-up memory for looking at the page.`, kind: n.extra['kind'], source: n.extra['source'], created_ms: Date.now() - 86_400_000 * 9, as_of_ms: Date.now() - 86_400_000 * 9, version: 1, similar };
    }
    case 'chat':
      return { title: n.label, kind: 'chat', last_ms: n.extra['last_ms'], messages: 14 };
    case 'helper':
      return { name: n.label, job: 'A made-up helper for looking at the page.', state: n.extra['state'], doing: null, last_run: Date.now() - 3_600_000, next_ms: Date.now() + 7_200_000, connections: ['google'] };
    case 'knowledge':
      return { path: n.label, modified_ms: Date.now() - 86_400_000, chars: 1800, kind: n.extra['kind'], text: '# A made-up file\n\nFor looking at the page.' };
    case 'tool':
      return { label: n.label, description: 'A made-up tool for looking at the page.', risk: n.extra['risk'], category: n.extra['category'], recoverable: true };
    case 'connector':
      return { label: n.label, status: n.extra['status'], tools: skeleton.edges.filter((e) => e.source === id).map((e) => e.target.slice(5)) };
    case 'core':
      return { agent: 'Nova', blocks: { stable: 9200, turn: 1400 } };
    case 'ring':
      return { chars: n.extra['chars'], parts: ['persona', 'prompts', 'skills'] };
  }
}

/** One made-up event at a time, for `?fixture&events`. */
export function fixtureEvents(skeleton: { nodes: readonly MindNode[] }, fire: (e: ObserveEvent) => void): () => void {
  const r = rand(3);
  const pick = (type: MindNode['type']): string | null => {
    const list = skeleton.nodes.filter((n) => n.type === type);
    return list[Math.floor(r() * list.length)]?.id ?? null;
  };
  const after = (id: string | null): string => (id ? id.slice(id.indexOf(':') + 1) : '');
  const timer = setInterval(() => {
    const roll = r();
    if (roll < 0.3) fire({ type: 'memory.recalled', ids: [after(pick('memory')), after(pick('memory'))] });
    else if (roll < 0.5) fire({ type: 'tool.start', name: after(pick('tool')) });
    else if (roll < 0.6) fire({ type: 'tool.end', name: after(pick('tool')), ok: r() > 0.3 });
    else if (roll < 0.75) fire({ type: 'turn', conversation_id: after(pick('chat')) });
    else if (roll < 0.9) fire({ type: 'helper.started', id: after(pick('helper')) });
    else if (roll < 0.97) fire({ type: 'helper.done', id: after(pick('helper')), ok: true });
    else fire({ type: 'alert', critical: true });
  }, 1800);
  return () => clearInterval(timer);
}
