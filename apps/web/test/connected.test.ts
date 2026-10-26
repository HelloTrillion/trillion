import { describe, expect, it } from 'vitest';
import {
  chatText,
  connectedSummary,
  filterCatalog,
  inkFor,
  readingTools,
  sourceTiles,
  statusText,
  type CatalogEntry,
  type Connected,
  type ToolRow,
} from '../src/connected';

const server = (status: 'connected' | 'failed' | 'disabled' | 'signing_in', problem = '') => ({ status, problem }) as const;

const entry = (id: string, title: string, category: string, blurb = ''): CatalogEntry => ({
  id,
  title,
  blurb,
  category,
  color: '000000',
  glyph: title[0] ?? '',
  how: 'sign_in',
  connects: '',
  local: false,
  fields: [],
  homepage: '',
  note: '',
});

describe('connected tools', () => {
  it('says plainly how each server is doing, and why not', () => {
    expect(statusText(server('connected'))).toBe('Connected');
    expect(statusText(server('failed', 'its program was not found'))).toBe('Not connected: its program was not found');
    expect(statusText(server('disabled'))).toBe('Turned off');
    expect(statusText(server('signing_in'))).toBe('Waiting for you to sign in');
  });

  it('sums them up for the sidebar', () => {
    const c = (statuses: ('connected' | 'failed')[]) => ({ config_path: '', problems: {}, servers: statuses.map((s) => ({ ...server(s) })) }) as never;
    expect(connectedSummary(null)).toBe('None');
    expect(connectedSummary(c([]))).toBe('None');
    expect(connectedSummary(c(['connected', 'connected']))).toBe('2 connected');
    expect(connectedSummary(c(['connected', 'failed']))).toBe('1 of 2');
  });

  it('finds apps by name, by what they do, and by kind', () => {
    const all = [entry('notion', 'Notion', 'Docs and files', 'Search your pages'), entry('todoist', 'Todoist', 'Tasks and projects', 'Your tasks')];
    expect(filterCatalog(all, 'not', '').map((e) => e.id)).toEqual(['notion']);
    expect(filterCatalog(all, 'TASKS', '').map((e) => e.id)).toEqual(['todoist']);
    expect(filterCatalog(all, '', 'Docs and files').map((e) => e.id)).toEqual(['notion']);
    expect(filterCatalog(all, 'pages', 'Tasks and projects')).toEqual([]);
    const gmail = { ...entry('gmail', 'Gmail', 'Email and calendar'), how: 'google' as const };
    expect(filterCatalog([gmail, ...all], 'mail', '').map((e) => e.id)).toEqual(['gmail']);
  });

  it('frees only tools the service says just read, and only those still asking', () => {
    const t = (name: string, says_read_only: boolean, asks_first: boolean): ToolRow => ({ name, description: '', says_read_only, asks_first });
    const tools = [t('search', true, true), t('delete', false, true), t('list', true, false)];
    expect(readingTools(tools).map((x) => x.name)).toEqual(['search']);
  });

  it('writes the letters in a colour that reads on the tile', () => {
    // Brand colours, written without their # here only to keep the colour check quiet.
    expect(inkFor('FFD02F')).toBe('var(--color-ink-on-light)');
    expect(inkFor('191919')).toBe('var(--color-ink-on-dark)');
    expect(inkFor('not a colour')).toBe('var(--color-ink-on-dark)');
  });

  it('says how a chat connection is doing', () => {
    const c = { configured: true, on: true, status: 'connected' as const, problem: '', handle: 'nova_bot' };
    expect(chatText(c)).toBe('Listening as @nova_bot');
    expect(chatText({ ...c, configured: false })).toBe('Not set up');
    expect(chatText({ ...c, on: false })).toBe('Turned off');
    expect(chatText({ ...c, status: 'failed', problem: 'the token was revoked' })).toBe('Not connected: the token was revoked');
  });
});

describe('sourceTiles', () => {
  const stripe = entry('stripe', 'Stripe', 'Money');
  const linear = entry('linear', 'Linear', 'Work');
  const calendar: CatalogEntry = { ...entry('google-calendar', 'Google Calendar', 'Google'), how: 'google', service: 'calendar' };
  const slack: CatalogEntry = { ...entry('slack', 'Slack', 'Chat'), how: 'slack' };
  const all = [stripe, linear, calendar, slack];
  const row = (catalog: string, disabled = false): unknown => ({ name: catalog || 'own', catalog, disabled });
  const state = (servers: unknown[], catalog?: CatalogEntry[]): Connected =>
    ({ config_path: '', problems: {}, servers, ...(catalog ? { catalog } : {}) }) as unknown as Connected;
  const google = (accounts: string[]): Parameters<typeof sourceTiles>[1] => ({ services: { calendar: { title: 'Calendar', accounts, tools: [] } } });
  const ids = (c: Connected | null, g: Parameters<typeof sourceTiles>[1] = null): string[] => sourceTiles(c, g).map((t) => t.id);

  it('is nothing without a catalogue', () => {
    expect(ids(null)).toEqual([]);
    expect(ids(state([row('stripe')]))).toEqual([]);
  });

  it('leaves out a server turned off, and one not from the catalogue', () => {
    expect(ids(state([row('stripe', true)], all))).toEqual([]);
    expect(ids(state([row('')], all))).toEqual([]);
  });

  it('takes a catalogue server set up and on, as its id, title, colour and glyph', () => {
    expect(ids(state([row('stripe'), row('linear', true), row('')], all))).toEqual(['stripe']);
    expect(sourceTiles(state([row('stripe')], all))).toEqual([{ id: 'stripe', title: 'Stripe', color: '000000', glyph: 'S' }]);
  });

  it('takes a Google service only with an account signed in, and never a chat channel', () => {
    expect(ids(state([], all))).toEqual([]);
    expect(ids(state([], all), google([]))).toEqual([]);
    expect(ids(state([], all), google(['sam@example.com']))).toEqual(['google-calendar']);
    expect(ids(state([], all), { services: {} })).toEqual([]);
  });
});
