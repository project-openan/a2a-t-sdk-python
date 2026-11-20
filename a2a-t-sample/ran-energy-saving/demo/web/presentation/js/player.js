/** Scene player of the presentation page.
 *
 * The SSE stream is a queue of scenes. The player applies them at a human pace so a fast backend
 * does not flash through the story. It supports auto / step-by-step playback, pause, previous and
 * next, speed, and a hold for a pending engineer decision.
 */

//: Pause after each story beat, so consecutive scenes read as separate steps instead of a burst.
const BEAT_MS = 260;

//: Pause after the engineer answers, so the next scene does not snap in under the click.
const RELEASE_MS = 420;

export function createPlayer({ onApply, onRewind, onChange, onScene, holds = null }) {
  let scenes = [];
  let playhead = 0;
  let mode = "auto";
  let speed = 1;
  let paused = false;
  let hold = false;
  let waitingNext = false;
  let timer = null;

  function enqueue(scene) {
    if (scene === null || scene === undefined) return;
    scenes.push(scene);
    onChange();
    drain();
  }

  function drain() {
    if (paused || hold || waitingNext || timer !== null) return;
    if (playhead >= scenes.length) {
      onChange();
      return;
    }
    const scene = scenes[playhead];
    playhead += 1;
    onApply(scene.frame);
    // A scene queued before a decision can still be applied after it (the approval frame arrived
    // while an earlier beat was pacing). Re-check whether the gate is still open at apply time;
    // otherwise the page would hold on a card that no longer exists and never release it.
    const shouldHold = scene.hold && (holds === null || holds(scene));
    // Mark the hold before the scene callback so a terminal frame already queued behind the gate
    // can be applied and release it (the server may have closed the gate while the player was
    // still pacing earlier scenes).
    if (shouldHold) hold = true;
    if (onScene) onScene(scene, { hold: shouldHold });
    if (shouldHold) {
      onChange();
      return;
    }
    onChange();
    if (mode === "step" && !scene.evidence) {
      waitingNext = true;
      onChange();
      return;
    }
    if (scene.duration <= 0) {
      drain();
      return;
    }
    const delay = scene.evidence ? scene.duration : scene.duration / speed + BEAT_MS;
    timer = setTimeout(() => {
      timer = null;
      drain();
    }, delay);
  }

  function pause() {
    if (paused) return;
    paused = true;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    onChange();
  }

  function resume() {
    if (!paused) return;
    paused = false;
    onChange();
    drain();
  }

  function next() {
    if (paused) resume();
    if (waitingNext) {
      waitingNext = false;
      onChange();
      drain();
      return true;
    }
    return false;
  }

  function previous() {
    if (playhead <= 1) return false;
    rewind(Math.max(0, playhead - 2), { pause: true });
    return true;
  }

  function jumpToStep(step) {
    const index = scenes.findIndex((scene) => scene.step >= step && scene.duration > 0);
    if (index < 0) return false;
    rewind(index, { pause: mode === "step" });
    return true;
  }

  function rewind(index, { pause: shouldPause }) {
    cancelTimer();
    hold = false;
    waitingNext = false;
    paused = false;
    playhead = index;
    onRewind(index);
    if (shouldPause) {
      paused = true;
      waitingNext = true;
      onChange();
      return;
    }
    onChange();
    drain();
  }

  function release() {
    if (!hold) return;
    hold = false;
    onChange();
    // The click resolved the hold; let the confirmation land before the next beat starts.
    timer = setTimeout(() => {
      timer = null;
      drain();
    }, RELEASE_MS);
  }

  function reset() {
    cancelTimer();
    scenes = [];
    playhead = 0;
    paused = false;
    hold = false;
    waitingNext = false;
    onChange();
  }

  function setMode(value) {
    mode = value === "step" ? "step" : "auto";
    if (mode === "auto") {
      waitingNext = false;
      paused = false;
      drain();
    }
    onChange();
  }

  function setSpeed(value) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) speed = parsed;
    onChange();
  }

  function cancelTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  return {
    enqueue,
    pause,
    resume,
    next,
    previous,
    jumpToStep,
    release,
    reset,
    setMode,
    setSpeed,
    holdNow() {
      hold = true;
      onChange();
    },
    sceneAt(index) {
      return scenes[index] || null;
    },
    status() {
      return {
        scenes: scenes.length,
        playhead,
        paused,
        hold,
        waitingNext,
        mode,
        speed,
        current: scenes[Math.max(0, playhead - 1)] || null,
        atEnd: playhead >= scenes.length,
      };
    },
  };
}
