/** Line icons for Settings and the sidebar, built as SVG elements: no markup strings, no library. */

const PATHS: Readonly<Record<string, readonly string[]>> = {
  // A four-point star: what she is, against the galaxy.
  model: ['M12 3.5c.7 4.6 3.4 7.3 8 8c-4.6.7-7.3 3.4-8 8c-.7-4.6-3.4-7.3-8-8c4.6-.7 7.3-3.4 8-8z'],
  voice: ['M4 9.5v5h3.5L13 19V5L7.5 9.5H4z', 'M16.5 9a4.2 4.2 0 0 1 0 6', 'M19 6.5a7.8 7.8 0 0 1 0 11'],
  mic: ['M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z', 'M5.5 11.5a6.5 6.5 0 0 0 13 0', 'M12 18v3'],
  names: ['M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8z', 'M4.5 20.5a7.5 7.5 0 0 1 15 0'],
  shortcuts: ['M3.5 6.5h17v11h-17z', 'M7 10h.01M10.5 10h.01M14 10h.01M17.5 10h.01M7.5 14h9'],
  appearance: ['M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 0 0 0-17z', 'M12 3.5v17', 'M12 7h4M12 11h5.5M12 15h4.5'],
  folders: ['M3.5 6.5h6l2 2h9v10h-17z'],
  // A plug: tools from servers the user connected.
  connected: ['M9 3.5v4M15 3.5v4', 'M6.5 7.5h11v3a5.5 5.5 0 0 1-11 0z', 'M12 16v4.5'],
  // An open book: instructions she reads when a task calls for them.
  skills: ['M12 6.5c-2-1.5-5-2-8-1.5v13c3-.5 6 0 8 1.5c2-1.5 5-2 8-1.5v-13c-3-.5-6 0-8 1.5z', 'M12 6.5v13'],
  usage: ['M4 17a8 8 0 1 1 16 0', 'M12 17l4-5'],
  // A clock: what is set to come back at a time.
  reminders: ['M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 0 0 0-17z', 'M12 7.5V12l3 2'],
  about: ['M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 0 0 0-17z', 'M12 11v5.5', 'M12 7.8h.01'],
  close: ['M6.5 6.5l11 11M17.5 6.5l-11 11'],
  menu: ['M4 7h16M4 12h16M4 17h16'],
  // A window with a panel down its left side: show or hide the sidebar.
  sidebar: ['M3.5 5.5h17v13h-17z', 'M9.5 5.5v13'],
  // A pencil: start a new conversation.
  edit: ['M14.5 5.5l4 4L9 19H5v-4z', 'M12.5 7.5l4 4'],
  // A clock turning back: past conversations.
  history: ['M4.5 12a7.5 7.5 0 1 0 2.2-5.3', 'M4.5 4.5v3.5H8', 'M12 8v4.2l2.8 1.8'],
  // The ask bar: send (an arrow up), stop (a square), done speaking (a tick).
  send: ['M12 18.5V6', 'M6.5 11.5L12 6l5.5 5.5'],
  stop: ['M7.5 7.5h9v9h-9z'],
  check: ['M5.5 12.5l4 4l9-9'],
  // Four tiles: the workspace and its widgets.
  workspace: ['M4 4.5h7v7H4z', 'M13 4.5h7v4h-7z', 'M13 10.5h7v9h-7z', 'M4 13.5h7v6H4z'],
  search: ['M10.5 4a6.5 6.5 0 1 0 0 13a6.5 6.5 0 0 0 0-13z', 'M20 20l-4.8-4.8'],
};

const SVG = 'http://www.w3.org/2000/svg';

export function icon(name: string, size = 18): SVGSVGElement {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  for (const d of PATHS[name] ?? []) {
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}
