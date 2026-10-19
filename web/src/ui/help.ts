import { APP_VERSION } from '../doc';
import { TOOLS } from '../editor';
import { dialog, el } from './dom';
import { keyLabel } from './menus';

export function showShortcuts() {
  const rows: [string, string][] = [
    ...TOOLS.map(t => [t.key, t.label] as [string, string]),
    ['Space (hold)', 'Pan with any tool'],
    ['Scroll / pinch', 'Zoom'],
    ['Enter / double-click', 'Finish a line or area'],
    ['Esc', 'Cancel drawing / deselect'],
    ['Del', 'Delete the selection'],
    ['Ctrl+D', 'Duplicate'],
    ['Ctrl+Z / Ctrl+Shift+Z', 'Undo / redo'],
    ['Ctrl+S', 'Save the map file'],
    ['Ctrl+O', 'Open a map file'],
    ['Ctrl+E', 'Export (PDF, PNG, JPEG)'],
    ['Ctrl+L', 'Show / hide the page'],
    ['Ctrl+1', 'Fit the page in the window'],
    ['Ctrl+F', 'Select by attributes'],
    ['Ctrl+A / Ctrl+Shift+A', 'Select all / clear selection'],
    ['Ctrl+I', 'Invert selection'],
    ['Ctrl+3', 'Zoom to selected'],
    ['Ctrl+T', 'Attribute table'],
    ['Ctrl + scroll', 'Zoom the page (Page view)'],
    ['Arrow keys', 'Nudge the selected item (Shift = further)'],
    ['Tab', 'Show / hide side panels'],
  ];
  const t = el('div', { style: { display: 'grid', gridTemplateColumns: '150px 1fr', gap: '6px 12px' } });
  for (const [k, v] of rows) t.append(el('b', {}, keyLabel(k)), el('span', {}, v));
  return dialog('Keyboard shortcuts', [t]);
}

export function showAbout() {
  return dialog('About MapMaker', [
    el('p', {}, `${APP_VERSION} – design maps in the browser, laid out like an image editor.`),
    el('p', {}, 'Maps are kept in this browser as you work and can be saved as .mapmaker files, which also open in the MapMaker desktop app.'),
    el('p', {}, 'Background maps © Esri, HERE, Garmin, OpenStreetMap contributors and others, as credited on the map. Symbols and design defaults follow Judith Tyner, Principles of Map Design.'),
  ]);
}
