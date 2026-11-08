/**
 * What an observe event does to the map (the spec's event table), as plain data, so
 * it is decided and tested without a scene. The scene carries the effects out.
 *
 * An event naming a node that is not on the page does nothing: it is dropped, never
 * faked. Under reduced motion nothing travels; the ends of a pulse brighten instead.
 */

import { type ObserveEvent, type RegionId, nodeId } from './contract';

/** A pulse's colour: a region's, or danger for something that failed. */
export type Tint = RegionId | 'danger';

/** A node id, or `region:<id>` for the middle of a region (a spark into the memory web). */
export type Endpoint = string;

export type Effect =
  | { readonly kind: 'travel'; readonly from: Endpoint; readonly to: Endpoint; readonly tint: Tint; readonly flareFrom?: boolean }
  | { readonly kind: 'brighten'; readonly id: string; readonly tint: Tint }
  | { readonly kind: 'halo'; readonly id: string; readonly on: boolean }
  | { readonly kind: 'ring' }
  | { readonly kind: 'ripple' }
  | { readonly kind: 'refetch' };

export interface PlanContext {
  /** Whether a node with this id is on the page now. */
  has(id: string): boolean;
  /** Whether a region has anything drawn in it. */
  hasRegion(region: RegionId): boolean;
  readonly reducedMotion: boolean;
}

const CORE = 'core';
const TURN_RING = 'ring:turn';

/** Travel, or under reduced motion a brightening of whichever ends are nodes. */
function pulse(ctx: PlanContext, from: Endpoint, to: Endpoint, tint: Tint, flareFrom = false): Effect[] {
  if (!ctx.reducedMotion) return [{ kind: 'travel', from, to, tint, ...(flareFrom ? { flareFrom } : {}) }];
  return [from, to].filter((id) => !id.startsWith('region:')).map((id) => ({ kind: 'brighten', id, tint }));
}

/** The effects of one event; an empty list means it was dropped. */
export function planEvent(event: ObserveEvent, ctx: PlanContext): Effect[] {
  switch (event.type) {
    case 'turn': {
      const out: Effect[] = [];
      if (ctx.has(TURN_RING)) out.push({ kind: 'ring' });
      if (event.conversation_id) {
        const chat = nodeId('chat', event.conversation_id);
        if (ctx.has(chat)) out.push({ kind: 'brighten', id: chat, tint: 'working' });
      }
      return out;
    }
    case 'tool.start': {
      const tool = nodeId('tool', event.name);
      if (!ctx.has(tool) || !ctx.has(CORE)) return [];
      return pulse(ctx, CORE, tool, 'rim');
    }
    case 'tool.end': {
      const tool = nodeId('tool', event.name);
      if (!ctx.has(tool)) return [];
      return [{ kind: 'brighten', id: tool, tint: event.ok ? 'rim' : 'danger' }];
    }
    case 'memory.written': {
      // The new memory is not on the page yet: a spark from the chats into the web,
      // and the skeleton is read again so it appears where it really sits.
      const out: Effect[] = [];
      const mem = nodeId('mem', event.id);
      if (ctx.has(mem)) out.push({ kind: 'brighten', id: mem, tint: 'memory' });
      else if (!ctx.reducedMotion && ctx.hasRegion('working') && ctx.hasRegion('memory')) {
        out.push({ kind: 'travel', from: 'region:working', to: 'region:memory', tint: 'working' });
      }
      out.push({ kind: 'refetch' });
      return out;
    }
    case 'memory.recalled': {
      const out: Effect[] = [];
      for (const raw of event.ids) {
        const mem = nodeId('mem', raw);
        if (!ctx.has(mem)) continue;
        if (ctx.has(CORE)) out.push(...pulse(ctx, mem, CORE, 'memory', true));
        else out.push({ kind: 'brighten', id: mem, tint: 'memory' });
      }
      // Each memory brightens once, even when it is both an end and a source.
      return out.filter((e, i) => e.kind !== 'brighten' || out.findIndex((x) => x.kind === 'brighten' && x.id === e.id) === i);
    }
    case 'helper.started':
    case 'helper.done': {
      const helper = nodeId('helper', event.id);
      if (!ctx.has(helper)) return [];
      const started = event.type === 'helper.started';
      const ok = started || event.ok;
      const out: Effect[] = [{ kind: 'halo', id: helper, on: started }];
      if (ctx.has(CORE)) out.push(...(started ? pulse(ctx, CORE, helper, 'agents') : pulse(ctx, helper, CORE, ok ? 'agents' : 'danger')));
      else out.push({ kind: 'brighten', id: helper, tint: ok ? 'agents' : 'danger' });
      return out;
    }
    case 'alert':
      return event.critical ? [{ kind: 'ripple' }] : [];
  }
}
