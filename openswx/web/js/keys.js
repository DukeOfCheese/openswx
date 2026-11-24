export const SHORTCUTS = [
  // [action, label, default keys, group]
  ["new_part", "New part", "Ctrl+N", "File"], ["open", "Open", "Ctrl+O", "File"], ["save", "Save", "Ctrl+S", "File"],
  ["close", "Close document", "Ctrl+W", "File"], ["home", "Home page", "Ctrl+Shift+H", "File"], ["next_document", "Next document", "Ctrl+Tab", "File"],
  ["undo", "Undo", "Ctrl+Z", "Edit"], ["redo", "Redo", "Ctrl+Y", "Edit"], ["rebuild", "Rebuild", "Ctrl+B", "Edit"],
  ["force_rebuild", "Rebuild (all features)", "Ctrl+Q", "Edit"], ["rename", "Rename selected feature", "F2", "Edit"],
  ["view_front", "Front view", "Ctrl+1", "View"], ["view_back", "Back view", "Ctrl+2", "View"],
  ["view_left", "Left view", "Ctrl+3", "View"], ["view_right", "Right view", "Ctrl+4", "View"],
  ["view_top", "Top view", "Ctrl+5", "View"], ["view_bottom", "Bottom view", "Ctrl+6", "View"],
  ["view_isometric", "Isometric view", "Ctrl+7", "View"], ["normal_to", "Normal to selection", "Ctrl+8", "View"],
  ["previous_view", "Previous view", "Ctrl+Shift+Z", "View"], ["view_orientation", "View orientation menu", "Space", "View"],
  ["zoom_fit", "Zoom to fit", "F", "View"], ["zoom_out", "Zoom out", "Z", "View"], ["zoom_in", "Zoom in", "Shift+Z", "View"],
  ["filter_faces", "Selection filter: faces", "X", "Selection"], ["filter_edges", "Selection filter: edges", "E", "Selection"],
  ["filter_vertices", "Selection filter: vertices", "V", "Selection"], ["filter_toolbar", "Selection filter toolbar", "F5", "Selection"],
  ["shortcut_bar", "Shortcut bar", "S", "Tools"], ["command_search", "Search commands", "Ctrl+K", "Tools"],
  ["focus_tree", "Go to the FeatureManager tree", "Alt+T", "Keyboard navigation"],
  ["open_menu", "Go to the menu bar", "F10", "Keyboard navigation"],
];

export const DEFAULT_KEYS = Object.fromEntries(SHORTCUTS.map(([id, , keys]) => [id, keys]));

const NAMED = { " ": "Space", Esc: "Escape", Del: "Delete", Left: "ArrowLeft", Right: "ArrowRight", Up: "ArrowUp", Down: "ArrowDown" };

// A key press as "Ctrl+Shift+K"; null for a modifier on its own
export function comboOf(e) {
  let k = NAMED[e.key] ?? e.key;
  if (["Control", "Shift", "Alt", "Meta", "AltGraph", "OS"].includes(k)) return null;
  // letters and digits by their key position, so Shift+1 is "Shift+1" and Alt+T is not "Alt+†" on a Mac
  if (/^Key[A-Z]$/.test(e.code)) k = e.code.slice(3);
  else if (/^Digit[0-9]$/.test(e.code)) k = e.code.slice(5);
  else if (k.length === 1) k = k.toUpperCase();
  return `${e.ctrlKey || e.metaKey ? "Ctrl+" : ""}${e.altKey ? "Alt+" : ""}${e.shiftKey ? "Shift+" : ""}${k}`;
}

// The keys for each action with the user's changes applied ("" = no shortcut)
export function effectiveKeys(overrides = {}) {
  const out = { ...DEFAULT_KEYS };
  for (const [id, keys] of Object.entries(overrides || {})) if (id in out) out[id] = keys;
  return out;
}

// keys -> action, for looking up a key press
export function keyTable(keys) {
  const t = new Map();
  for (const [id, k] of Object.entries(keys)) if (k && !t.has(k)) t.set(k, id);
  return t;
}

// Actions sharing the same keys: [[keys, [action, action, ...]], ...]
export function conflicts(keys) {
  const by = new Map();
  for (const [id, k] of Object.entries(keys)) if (k) by.set(k, [...(by.get(k) || []), id]);
  return [...by].filter(([, ids]) => ids.length > 1);
}
