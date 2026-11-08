import { describe, expect, it } from 'vitest';
import type { RegionId } from '../../src/mind/contract';
import { type PlanContext, planEvent } from '../../src/mind/pulses';

const ctx = (ids: string[], reducedMotion = false, regions: RegionId[] = ['working', 'memory']): PlanContext => ({
  has: (id) => ids.includes(id),
  hasRegion: (r) => regions.includes(r),
  reducedMotion,
});
const ON_PAGE = ['core', 'ring:turn', 'chat:c1', 'tool:web_search', 'mem:mem_1', 'mem:mem_2', 'helper:h1'];

describe('observe events become pulses', () => {
  it('sends a tool call from her to the tool, and tints a failure', () => {
    expect(planEvent({ type: 'tool.start', name: 'web_search' }, ctx(ON_PAGE))).toEqual([{ kind: 'travel', from: 'core', to: 'tool:web_search', tint: 'rim' }]);
    expect(planEvent({ type: 'tool.end', name: 'web_search', ok: false }, ctx(ON_PAGE))).toEqual([{ kind: 'brighten', id: 'tool:web_search', tint: 'danger' }]);
  });

  it('drops an event that names nothing on the page', () => {
    expect(planEvent({ type: 'tool.start', name: 'not_here' }, ctx(ON_PAGE))).toEqual([]);
    expect(planEvent({ type: 'memory.recalled', ids: ['gone'] }, ctx(ON_PAGE))).toEqual([]);
    expect(planEvent({ type: 'helper.started', id: 'nobody' }, ctx(ON_PAGE))).toEqual([]);
    expect(planEvent({ type: 'turn', conversation_id: 'c9' }, ctx([]))).toEqual([]);
    expect(planEvent({ type: 'alert', critical: false }, ctx(ON_PAGE))).toEqual([]);
  });

  it('flares recalled memories toward her, skipping the ones not shown', () => {
    expect(planEvent({ type: 'memory.recalled', ids: ['mem_1', 'gone', 'mem:mem_2'] }, ctx(ON_PAGE))).toEqual([
      { kind: 'travel', from: 'mem:mem_1', to: 'core', tint: 'memory', flareFrom: true },
      { kind: 'travel', from: 'mem:mem_2', to: 'core', tint: 'memory', flareFrom: true },
    ]);
  });

  it('brightens instead of travelling under reduced motion', () => {
    expect(planEvent({ type: 'tool.start', name: 'web_search' }, ctx(ON_PAGE, true))).toEqual([
      { kind: 'brighten', id: 'core', tint: 'rim' },
      { kind: 'brighten', id: 'tool:web_search', tint: 'rim' },
    ]);
    expect(planEvent({ type: 'memory.recalled', ids: ['mem_1', 'mem_2'] }, ctx(ON_PAGE, true))).toEqual([
      { kind: 'brighten', id: 'mem:mem_1', tint: 'memory' },
      { kind: 'brighten', id: 'core', tint: 'memory' },
      { kind: 'brighten', id: 'mem:mem_2', tint: 'memory' },
    ]);
  });

  it('sparks a new memory into the web and reads the skeleton again', () => {
    expect(planEvent({ type: 'memory.written', id: 'mem_9' }, ctx(ON_PAGE))).toEqual([
      { kind: 'travel', from: 'region:working', to: 'region:memory', tint: 'working' },
      { kind: 'refetch' },
    ]);
    expect(planEvent({ type: 'memory.written', id: 'mem_9' }, ctx(ON_PAGE, true))).toEqual([{ kind: 'refetch' }]);
    expect(planEvent({ type: 'memory.written', id: 'mem_9' }, ctx(ON_PAGE, false, ['memory']))).toEqual([{ kind: 'refetch' }]);
  });

  it('flickers the per-turn ring and the chat, haloes a helper, ripples on a critical alert', () => {
    expect(planEvent({ type: 'turn', conversation_id: 'c1' }, ctx(ON_PAGE))).toEqual([{ kind: 'ring' }, { kind: 'brighten', id: 'chat:c1', tint: 'working' }]);
    expect(planEvent({ type: 'helper.started', id: 'h1' }, ctx(ON_PAGE))).toEqual([
      { kind: 'halo', id: 'helper:h1', on: true },
      { kind: 'travel', from: 'core', to: 'helper:h1', tint: 'agents' },
    ]);
    expect(planEvent({ type: 'helper.done', id: 'h1', ok: false }, ctx(ON_PAGE))).toEqual([
      { kind: 'halo', id: 'helper:h1', on: false },
      { kind: 'travel', from: 'helper:h1', to: 'core', tint: 'danger' },
    ]);
    expect(planEvent({ type: 'alert', critical: true }, ctx([]))).toEqual([{ kind: 'ripple' }]);
  });
});
