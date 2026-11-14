/** Keyboard map of the Run Explorer.
 *
 * The map is data so the help dialog and the installer cannot drift apart. Typing in a form field
 * suppresses the single-key shortcuts; ``Esc`` and ``Cmd/Ctrl+Enter`` always work.
 */

/** Every shortcut, in help-dialog order. ``descriptionKey`` resolves through ``i18n.t``. */
export const SHORTCUTS = [
  { keys: "j", descriptionKey: "shortcuts.nextRow" },
  { keys: "k", descriptionKey: "shortcuts.previousRow" },
  { keys: "Enter", descriptionKey: "shortcuts.openRow" },
  { keys: "Esc", descriptionKey: "shortcuts.escape" },
  { keys: "/", descriptionKey: "shortcuts.search" },
  { keys: "f", descriptionKey: "shortcuts.follow" },
  { keys: "1 – 6", descriptionKey: "shortcuts.tabs" },
  { keys: "[ / ]", descriptionKey: "shortcuts.step" },
  { keys: "?", descriptionKey: "shortcuts.help" },
  { keys: "Cmd/Ctrl + Enter", descriptionKey: "shortcuts.submit" },
];

/** Install the keyboard handlers; returns an uninstall function. */
export function installShortcuts(handlers, target = document) {
  const onKeyDown = (event) => {
    if (event.defaultPrevented) return;
    const key = event.key;
    const modified = event.metaKey || event.ctrlKey || event.altKey;

    if (modified && key === "Enter") {
      event.preventDefault();
      handlers.submitDecision?.();
      return;
    }
    if (key === "Escape") {
      handlers.escape?.();
      return;
    }
    if (isTyping(event.target)) return;
    if (modified) return;

    if (key >= "1" && key <= "6") {
      event.preventDefault();
      handlers.tab?.(Number(key));
      return;
    }
    switch (key) {
      case "j":
        event.preventDefault();
        handlers.next?.();
        break;
      case "k":
        event.preventDefault();
        handlers.previous?.();
        break;
      case "Enter":
        event.preventDefault();
        handlers.open?.();
        break;
      case "/":
        event.preventDefault();
        handlers.search?.();
        break;
      case "f":
        event.preventDefault();
        handlers.follow?.();
        break;
      case "[":
        event.preventDefault();
        handlers.previousStep?.();
        break;
      case "]":
        event.preventDefault();
        handlers.nextStep?.();
        break;
      case "?":
        event.preventDefault();
        handlers.help?.();
        break;
      default:
        break;
    }
  };
  target.addEventListener("keydown", onKeyDown);
  return () => target.removeEventListener("keydown", onKeyDown);
}

/** Whether one event target is an editable control. */
export function isTyping(target) {
  if (!target || !(target instanceof Element)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}
