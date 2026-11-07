/** Small DOM helpers shared by the presentation modules. */

const SVG_NS = "http://www.w3.org/2000/svg";

/** Create an HTML element, optionally with a class and text. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** Create an SVG element with attributes and optional text. */
export function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  }
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** Remove every child of one element. */
export function clear(node) {
  if (!node) return;
  node.replaceChildren();
}

/** Return true for a plain object. */
export function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
