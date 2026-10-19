// Interface icons: simple strokes on a 24 grid, coloured by currentColor.

const S = (body: string) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

export const ICONS: Record<string, string> = {
  select: S('<path d="M5 3l13 8-6 1.5L9 19z" fill="currentColor" stroke="none"/><path d="M12.4 12.6 16 19"/>'),
  pan: S('<path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11m0-6.5a1.5 1.5 0 0 1 3 0V11m0-4.5a1.5 1.5 0 0 1 3 0V13c0 4-2.5 7-6.5 7-3 0-4.6-1.6-6-4l-2-3.6a1.5 1.5 0 0 1 2.6-1.5L8 13"/>'),
  text: S('<path d="M5 6V4h14v2M12 4v16M9 20h6"/>'),
  symbol: S('<path d="M12 21s-6.5-6.6-6.5-11.5a6.5 6.5 0 0 1 13 0C18.5 14.4 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.3"/>'),
  line: S('<path d="M4 18 9.5 9l5 6L20 6"/><circle cx="4" cy="18" r="1.4" fill="currentColor"/><circle cx="20" cy="6" r="1.4" fill="currentColor"/>'),
  area: S('<path d="M5 7.5 12 4l7 5-2 10H7z" fill="currentColor" fill-opacity=".22"/>'),
  eye: S('<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/>'),
  eyeOff: S('<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" opacity=".5"/><path d="M4 4l16 16"/>'),
  lock: S('<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>'),
  unlock: S('<rect x="5" y="10.5" width="14" height="9.5" rx="2" opacity=".55"/><path d="M8 10.5V8a4 4 0 0 1 7.6-1.7" opacity=".55"/>'),
  plus: S('<path d="M12 5v14M5 12h14"/>'),
  trash: S('<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5"/>'),
  copy: S('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  up: S('<path d="M12 19V5M6 11l6-6 6 6"/>'),
  down: S('<path d="M12 5v14M6 13l6 6 6-6"/>'),
  caretRight: S('<path d="M10 7l5 5-5 5"/>'),
  caretDown: S('<path d="M7 10l5 5 5-5"/>'),
  // layer kinds
  basemap: S('<path d="M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20z"/><path d="M9 4v13.5M15 6.5V20"/>'),
  service: S('<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.8 5.2 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.2-3.8-8.5s1.2-6.1 3.8-8.5z"/>'),
  color: S('<rect x="4" y="4" width="16" height="16" rx="2.5" fill="currentColor" fill-opacity=".35"/>'),
  annotations: S('<path d="M4 6V4.5h9V6M8.5 4.5v10"/><path d="M17 21s-3.5-3.6-3.5-6.3a3.5 3.5 0 0 1 7 0C20.5 17.4 17 21 17 21z"/>'),
  terrain: S('<path d="M2.5 19 9 8l3.5 5.5L15 10l6.5 9z"/>'),
  data: S('<circle cx="7" cy="8" r="2"/><circle cx="16" cy="6.5" r="2"/><circle cx="11.5" cy="15" r="2"/><circle cx="18" cy="16.5" r="2"/>'),
  grid: S('<path d="M4 4h16v16H4zM4 12h16M12 4v16"/>'),
  features: S('<rect x="3.5" y="3.5" width="17" height="17" rx="1" stroke-dasharray="3 2.4"/><circle cx="9" cy="10" r="1.6" fill="currentColor"/><circle cx="15" cy="9" r="1.6" fill="currentColor"/><circle cx="12" cy="15" r="1.6" fill="currentColor"/>'),
  close: S('<path d="M6 6l12 12M18 6 6 18"/>'),
  page: S('<path d="M6 3.5h8.5L19 8v12.5H6z"/><path d="M14.5 3.5V8H19"/><rect x="8.5" y="11" width="8" height="6" rx=".5"/>'),
  table: S('<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M3.5 9.5h17M3.5 14h17M9.5 9.5V19"/>'),
  menu: S('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  panel: S('<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M14.5 4.5v15"/>'),
};

export const icon = (name: string) => ICONS[name] || ICONS.data;
