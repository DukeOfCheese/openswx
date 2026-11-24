let names = null;

const known = (name) => {
  names ??= new Set([...document.querySelectorAll("#icons symbol")].map((s) => s.id.replace(/^icon-/, "")));
  return names.has(name);
};

export function icon(name) {
  const n = known(name) ? name : "part";
  return `<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" fill="none" stroke-linejoin="round" stroke-linecap="round"><use href="#icon-${n}" width="100%" height="100%"></use></svg>`;
}
