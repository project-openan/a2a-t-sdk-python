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

/** Copy text to the clipboard, falling back to a hidden textarea on plain-HTTP origins.
 *
 * ``navigator.clipboard`` only exists in a secure context; the demo is sometimes opened over
 * plain HTTP from another host, where the legacy ``execCommand`` path is the only one available.
 */
export async function copyText(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // A denied permission falls through to the legacy path.
    }
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    return copied;
  } catch {
    return false;
  }
}
