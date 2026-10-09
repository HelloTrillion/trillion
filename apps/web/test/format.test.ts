import { describe, expect, it } from 'vitest';
import { inline, parse } from '../src/format';

describe('reply formatting', () => {
  it('leaves plain text alone', () => {
    expect(parse('Short answer: yes.')).toEqual([
      { kind: 'paragraph', inline: [{ kind: 'text', text: 'Short answer: yes.' }] },
    ]);
  });

  it('marks bold, italic and code', () => {
    expect(inline('**Done.** `hello.txt` is *there*, _really_.')).toEqual([
      { kind: 'bold', text: 'Done.' },
      { kind: 'text', text: ' ' },
      { kind: 'code', text: 'hello.txt' },
      { kind: 'text', text: ' is ' },
      { kind: 'italic', text: 'there' },
      { kind: 'text', text: ', ' },
      { kind: 'italic', text: 'really' },
      { kind: 'text', text: '.' },
    ]);
  });

  it('does not read arithmetic, file names or stray marks as emphasis', () => {
    for (const text of ['2 * 3 * 4', 'my_file_name.txt', 'a * b', 'snake_case_word', '**unclosed', '`unclosed']) {
      expect(inline(text)).toEqual([{ kind: 'text', text }]);
    }
  });

  it('keeps what is inside code exactly as written', () => {
    expect(inline('`**not bold**`')).toEqual([{ kind: 'code', text: '**not bold**' }]);
  });

  it('builds lists, and separates a bulleted list from a numbered one', () => {
    const blocks = parse('Options:\n- one\n- **two**\n1. first\n2. second');
    expect(blocks.map((b) => b.kind)).toEqual(['paragraph', 'list', 'list']);
    expect(blocks[1]).toMatchObject({ ordered: false, items: [[{ text: 'one' }], [{ kind: 'bold', text: 'two' }]] });
    expect(blocks[2]).toMatchObject({ ordered: true });
  });

  it('keeps a code block verbatim, even one still being streamed', () => {
    expect(parse('Run:\n```bash\nls -la\n**x**\n```\nDone.')).toEqual([
      { kind: 'paragraph', inline: [{ kind: 'text', text: 'Run:' }] },
      { kind: 'code', text: 'ls -la\n**x**' },
      { kind: 'paragraph', inline: [{ kind: 'text', text: 'Done.' }] },
    ]);
    expect(parse('```\nhalf')).toEqual([{ kind: 'code', text: 'half' }]);
  });

  it('never produces markup: tags and links stay text', () => {
    const hostile = '<img src=x onerror=alert(1)> [click](javascript:alert(1)) <script>x</script>';
    const blocks = parse(hostile);
    expect(blocks).toEqual([{ kind: 'paragraph', inline: [{ kind: 'text', text: hostile }] }]);
    // The tree holds only text and a closed set of kinds; there is nothing to inject through.
    expect(JSON.stringify(blocks)).not.toMatch(/"kind":"(html|link|image)"/);
  });

  it('splits paragraphs on blank lines and keeps line breaks within one', () => {
    expect(parse('one\ntwo\n\nthree').map((b) => (b.kind === 'paragraph' ? b.inline[0]?.text : ''))).toEqual([
      'one\ntwo',
      'three',
    ]);
  });
});
