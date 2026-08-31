/* motion-ui.js — Motion (https://motion.dev) animation layer for the Convert screen.
 *
 * Loaded after vendor/motion.js (UMD → window.Motion) and after convert.js.
 * convert.js calls into window.MotionUI at a handful of points; everything else
 * here wires itself up by delegation so re-rendered cards keep working.
 *
 * Every entry point is defensive: if the vendored bundle is missing, MotionUI
 * becomes a set of no-ops and the page behaves exactly as it did before.
 */
(function () {
  "use strict";

  var M = window.Motion;
  var REDUCED = false;
  try { REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

  /* No library, or the tester asked the OS for less motion → hand back no-ops.
     The UI must never depend on an animation having run. */
  if (!M || !M.animate || REDUCED) {
    /* Every no-op still runs any callback it was handed. Callers finish their
       work inside those callbacks, so swallowing one would stall the UI. */
    window.MotionUI = new Proxy({}, {
      get: function () {
        return function () {
          for (var i = arguments.length - 1; i >= 0; i--)
            if (typeof arguments[i] === "function") { arguments[i](); break; }
        };
      }
    });
    document.documentElement.setAttribute("data-motion", "off");
    return;
  }

  var animate = M.animate, stagger = M.stagger, inView = M.inView;
  document.documentElement.setAttribute("data-motion", "on");

  // ------------------------------------------------------------------ tuning
  var EASE   = [0.22, 0.61, 0.36, 1];      // calm, no overshoot
  var OUT    = [0.4, 0, 1, 1];
  var SPRING = { type: "spring", stiffness: 520, damping: 32, mass: 0.7 };
  var SOFT   = { type: "spring", stiffness: 260, damping: 28 };

  function on(el) { return el && el.nodeType === 1; }

  /* Run `cb` when the animation finishes — but never later than `ms`, and
     never twice. A backgrounded tab pauses rAF, so `finished` may never
     resolve; anything that changes real state must not wait on it. */
  function guard(anim, ms, cb) {
    var done = false;
    function fire() { if (!done) { done = true; try { cb(); } catch (e) {} } }
    if (anim && anim.finished && anim.finished.then)
      anim.finished.then(fire, fire);
    setTimeout(fire, ms);
  }
  function all(root, sel) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  /* Entrance animations start from opacity 0. A hidden tab freezes them at
     that first keyframe, so the content stays invisible for as long as the tab
     is in the background — a tester who switches away mid-conversion would
     come back to a blank page. Two rules keep the resting state safe:
       1. never start a reveal while the page is hidden;
       2. if the page goes hidden mid-reveal, drop the animation and snap the
          element to where it was heading.
     Content is visible by default; the animation is only ever decoration. */
  var revealing = [];

  function rest(els) {
    els.forEach(function (el) {
      if (!on(el)) return;
      el.style.opacity = "";
      el.style.transform = "";
      el.style.willChange = "";
    });
  }

  function reveal(els, keyframes, opts) {
    els = [].concat(els).filter(on);
    if (!els.length) return;
    if (document.hidden) { rest(els); return; }
    var anim = animate(els, keyframes, opts);
    var entry = { anim: anim, els: els };
    revealing.push(entry);
    var budget = ((opts && opts.duration) || 0.4) * 1000 + els.length * 45 + 400;
    guard(anim, budget, function () {
      var i = revealing.indexOf(entry);
      if (i >= 0) revealing.splice(i, 1);
      rest(els);
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) return;
    revealing.splice(0).forEach(function (e) {
      try { (e.anim.stop || e.anim.cancel || function () {}).call(e.anim); } catch (x) {}
      rest(e.els);
    });
  });

  // ------------------------------------------------------- height auto helper
  /* Animating to `auto` is unreliable across browsers, so measure the real
     height, animate to that in px, then release the inline value. */
  function openHeight(el, done) {
    if (!on(el)) return;
    el.style.overflow = "hidden";
    el.style.willChange = "height, opacity";
    var h = el.scrollHeight;
    guard(animate(el,
      { height: [0, h + "px"], opacity: [0, 1] },
      { duration: 0.30, ease: EASE }
    ), 420, function () {
      el.style.height = "";           // release, so the card can grow later
      el.style.overflow = "";
      el.style.opacity = "";
      el.style.willChange = "";
      if (done) done();
    });
  }

  function closeHeight(el, done) {
    if (!on(el)) return done && done();
    var h = el.scrollHeight;
    el.style.overflow = "hidden";
    el.style.willChange = "height, opacity";
    guard(animate(el,
      { height: [h + "px", "0px"], opacity: [1, 0] },
      { duration: 0.22, ease: OUT }
    ), 340, function () {
      el.style.height = ""; el.style.overflow = "";
      el.style.opacity = ""; el.style.willChange = "";
      if (done) done();
    });
  }

  // ============================================================== public API
  var API = {};

  /* Page load — masthead, then the workflow, then the rail steps. */
  API.ready = function () {
    reveal(document.querySelector(".appbar"),
           { opacity: [0, 1], y: [-14, 0] }, { duration: 0.4, ease: EASE });

    reveal([".wf-title", ".wf-sub"].map(function (q) { return document.querySelector(q); }),
           { opacity: [0, 1], y: [10, 0] },
           { duration: 0.45, delay: stagger(0.06, { startDelay: 0.05 }), ease: EASE });

    reveal(all(document, "#rail .st"),
           { opacity: [0, 1], y: [8, 0], scale: [0.97, 1] },
           { duration: 0.4, delay: stagger(0.05, { startDelay: 0.12 }), ease: EASE });

    /* Cards and helper panels reveal as they scroll into view. Nothing is
       pre-hidden while the page is in the background — see reveal(). */
    all(document, "#stateA .card, #stateA .help").forEach(function (c, i) {
      if (document.hidden) return;
      c.style.opacity = "0";
      inView(c, function () {
        reveal(c, { opacity: [0, 1], y: [16, 0] }, { duration: 0.5, ease: EASE });
        return false;                       // fire once
      }, { amount: 0.15 });
    });
  };

  /* A → B → C. The outgoing panel is already hidden by convert.js, so we only
     animate the panel arriving. */
  API.state = function (which) {
    var el = document.getElementById("state" + which);
    if (!on(el)) return;
    reveal(el, { opacity: [0, 1], y: [12, 0] }, { duration: 0.38, ease: EASE });

    if (which === "B") {
      reveal(all(el, ".prow"), { opacity: [0, 1], x: [-10, 0] },
             { duration: 0.35, delay: stagger(0.07), ease: EASE });
    }
    if (which === "C") {
      reveal(all(el, ".verdict, .conn, .orient, #edtools, #list, .filebar, .actions")
               .filter(function (b) { return !b.classList.contains("hidden"); }),
             { opacity: [0, 1], y: [14, 0] },
             { duration: 0.42, delay: stagger(0.05, { startDelay: 0.08 }), ease: EASE });
    }
  };

  /* Progress: spring the bar, and pop the row that just became active. */
  API.progress = function (step, pct) {
    var bar = document.getElementById("bar");
    if (on(bar)) {
      /* The inline transform is the truth, so the bar is correct even if the
         animation never runs (backgrounded tab). Motion animates from wherever
         it currently is to that same value. */
      var to   = Math.max(0, Math.min(100, pct)) / 100;
      var from = parseFloat(bar.dataset.scale || "0");
      bar.style.transition = "none";
      bar.dataset.scale = String(to);
      animate(bar, { scaleX: [from, to] }, SOFT);
      bar.style.transform = "scaleX(" + to + ")";
    }
    var doing = document.querySelector("#stateB .prow.doing");
    if (on(doing)) animate(doing, { scale: [0.99, 1] }, { duration: 0.3, ease: EASE });

    all(document, "#stateB .prow.done .ic").forEach(function (ic) {
      if (ic.dataset.popped) return;
      ic.dataset.popped = "1";
      animate(ic, { scale: [0.5, 1], rotate: [-25, 0] }, SPRING);
    });
    return on(bar);                       // we own the bar width from here
  };

  /* Scenario list (re)rendered. Fast and shallow — this also runs while the
     tester types in the search box, so it must never feel like a wait. */
  API.list = function () {
    var host = document.getElementById("list");
    if (!on(host)) return;
    var cards = all(host, ".scen");
    if (!cards.length) {
      reveal(host.querySelector(".empty"), { opacity: [0, 1], scale: [0.98, 1] },
             { duration: 0.25, ease: EASE });
      return;
    }
    reveal(cards.slice(0, 24), { opacity: [0, 1], y: [10, 0] },
           { duration: 0.28, delay: stagger(0.022), ease: EASE });
    reveal(cards.slice(24), { opacity: [0, 1] }, { duration: 0.2, ease: EASE });
  };

  /* One card replaced in place by edCardRefresh — a quiet cross-fade so the
     tester sees that something changed without the list jumping. */
  API.card = function (el) {
    reveal(el, { opacity: [0.45, 1] }, { duration: 0.22, ease: EASE });
  };

  /* Expanding / collapsing a scenario. */
  API.expand = function (card, open) {
    if (!on(card)) return;
    var steps = card.querySelector(".steps");
    var caret = card.querySelector(".caret");
    if (on(caret)) {
      caret.style.transition = "none";
      animate(caret, { rotate: open ? 90 : 0 }, SPRING);
    }
    if (!on(steps)) return;
    if (open) {
      steps.classList.remove("hidden");
      openHeight(steps, function () {
        reveal(all(steps, ".srow, .qcard2, .gh").slice(0, 14),
               { opacity: [0, 1], y: [6, 0] },
               { duration: 0.24, delay: stagger(0.015), ease: EASE });
      });
    } else {
      closeHeight(steps, function () { steps.classList.add("hidden"); });
    }
    return true;                            // tells convert.js we handled it
  };

  /* A question was answered — collapse it away before the card re-renders. */
  API.answered = function (el, done) {
    if (!on(el)) return done && done();
    guard(animate(el, { opacity: 0, scale: 0.97, x: 6 }, { duration: 0.16, ease: OUT }),
          260, function () { if (done) done(); });
  };

  /* Marking a scenario reviewed. */
  API.approve = function (card) {
    if (!on(card)) return;
    animate(card, { scale: [1, 1.012, 1] }, { duration: 0.34, ease: EASE });
    var b = card.querySelector(".okbtn");
    if (on(b)) animate(b, { scale: [0.86, 1] }, SPRING);
  };

  /* Count a number up/down in place. `fmt` renders the whole string. */
  API.count = function (el, from, to, fmt) {
    if (!on(el) || from === to) { if (on(el) && fmt) el.textContent = fmt(to); return; }
    animate(from, to, {
      duration: 0.5, ease: EASE,
      onUpdate: function (v) { el.textContent = fmt ? fmt(Math.round(v)) : String(Math.round(v)); }
    });
  };

  API.toast = function (show) {
    var t = document.getElementById("toast");
    if (!on(t)) return;
    t.style.transition = "none";
    if (show) animate(t, { opacity: [0, 1], y: [16, 0], scale: [0.96, 1] }, SPRING);
    else      animate(t, { opacity: 0, y: 10 }, { duration: 0.2, ease: OUT });
  };

  /* Something went wrong — a short, non-comic shake. */
  API.shake = function (el) {
    if (!on(el)) return;
    animate(el, { x: [0, -7, 6, -4, 2, 0] }, { duration: 0.42, ease: EASE });
  };

  API.fail = function () {
    var box = document.getElementById("errbox");
    if (!on(box)) return;
    animate(box, { opacity: [0, 1], y: [-8, 0] }, { duration: 0.3, ease: EASE });
    API.shake(box);
  };

  /* Validate / Run in flight — a slow breath so it reads as working, not stuck. */
  var busy = new WeakMap();
  API.busy = function (btn, isOn) {
    if (!on(btn)) return;
    var running = busy.get(btn);
    if (running) { running.stop(); busy.delete(btn); btn.style.opacity = ""; btn.style.transform = ""; }
    if (!isOn) return;
    busy.set(btn, animate(btn, { opacity: [1, 0.55, 1] },
      { duration: 1.5, repeat: Infinity, ease: "easeInOut" }));
  };

  /* FLIP: the element has already moved to its new place in the DOM. Start it
     back where it was and let it travel to where it now is. */
  API.slide = function (el, fromDy) {
    if (!on(el) || !fromDy) return;
    animate(el, { y: [fromDy, 0] }, { duration: 0.19, ease: EASE });
  };

  /* A dragged scenario has just been dropped — a short settle so the eye can
     follow where it landed in the list. */
  API.settle = function (el) {
    if (!on(el)) return;
    animate(el, { scale: [1.015, 1] }, SPRING);
    animate(el, { boxShadow: [
      "0 0 0 3px rgba(91,91,214,.30)", "0 0 0 0 rgba(91,91,214,0)"
    ] }, { duration: 0.55, ease: "easeOut" });
  };

  /* Draw the eye to something off-screen (used by "Answer the next question"). */
  API.spotlight = function (el) {
    if (!on(el)) return;
    animate(el, { scale: [1, 1.02, 1] }, { duration: 0.5, ease: EASE });
    animate(el, { boxShadow: [
      "0 0 0 0 rgba(91,91,214,.45)", "0 0 0 10px rgba(91,91,214,0)"
    ] }, { duration: 0.7, ease: "easeOut" });
  };

  // ------------------------------------------------- delegated hover / press
  /* Delegation rather than Motion's hover()/press() bindings, because the
     scenario cards are thrown away and rebuilt on every edit. */
  /* The hover lift is pure CSS (see .lift in convert.css). A Motion tween
     would end on a non-base value and revert the moment it settled; CSS :hover
     holds the state for exactly as long as the pointer is there. */
  var PRESS = "button, .pick, .chip, .abtn, .qb, .tbtn, .bigbtn, .okbtn, .valbtn, .nextq";

  document.addEventListener("pointerdown", function (e) {
    var t = e.target.closest && e.target.closest(PRESS);
    if (t) animate(t, { scale: 0.965 }, { duration: 0.09, ease: EASE });
  });
  ["pointerup", "pointercancel"].forEach(function (ev) {
    document.addEventListener(ev, function (e) {
      var t = e.target.closest && e.target.closest(PRESS);
      if (t) animate(t, { scale: 1 }, SPRING);
    });
  });

  window.MotionUI = API;

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", API.ready);
  else API.ready();
})();
