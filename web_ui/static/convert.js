/* ==========================================================================
   Stratus QA — Convert Test Cases
   The screen from Stratus-QA-Convert-Screen-DEMO-clickable.html, wired to the
   real backend. Feature set is exactly the demo's — nothing added.

     GET  /api/template/xlsx      the example test-case file
     GET  /api/profiles           saved server connections
     POST /api/test-connection    server health dot
     GET  /api/llm-status         is the AI available
     POST /api/import-testcases   .xlsx            -> YAML + counts
     POST /api/nl-to-yaml         pasted / described steps -> YAML
     POST /api/modify-testcases   a tester's plain answer applied to the YAML
     POST /api/scenarios          Save to my library
     POST /api/run                Run the test now
   ========================================================================== */
(function () {
"use strict";

// ------------------------------------------------------------------ state
var S = {
  method: "upload",          // upload | paste | describe
  file: null,
  yaml: "",
  screen: "",
  total: 0,                  // steps in the generated test
  tests: 1,                  // test cases found in the file
  steps: [],
  todos: [],                 // [{text, state:'open'|'fixed'|'skipped'}]
  mode: "safe",
  aiOn: false,               // from /api/llm-status
  profiles: [],
  converted: false,
  dirty: false,
  abort: null,
  yamlBackup: null
};

var SAVE_KEY = "stratus-qa-convert";

// ------------------------------------------------------------------ helpers
function $(id){ return document.getElementById(id); }
function qsa(s, r){ return Array.prototype.slice.call((r||document).querySelectorAll(s)); }
function showEl(el, yes){ if (el) el.classList.toggle("hidden", !yes); }

var toastTimer;
function toast(msg){
  var t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function(){ t.classList.remove("show"); }, 2800);
}

function fail(html){ $("errbox").innerHTML = html; $("errbox").classList.add("show"); }
function clearFail(){ $("errbox").classList.remove("show"); $("errbox").innerHTML = ""; }

function esc(s){
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function api(url, opts){
  opts = opts || {};
  opts.credentials = "same-origin";
  return fetch(url, opts).then(function (r) {
    if (r.status === 401) {
      fail("<b>You are signed out.</b> Please <a href=\"/\">sign in</a>, then come back to this page.");
      throw new Error("login required");
    }
    return r.json().catch(function(){ throw new Error("The server sent back something unreadable"); })
      .then(function (j) {
        if (!r.ok || j.error) throw new Error(j.error || ("Request failed (" + r.status + ")"));
        return j;
      });
  });
}

// ------------------------------------------------------------------ YAML (display only)
/* The server emits block-style YAML (yaml.safe_dump). We parse it only to show
   steps and find the `todo` ones — the server's YAML string stays the truth. */
function unquote(v){
  v = (v == null ? "" : String(v)).trim();
  if (v.length > 1 && ((v[0] === "'" && v.slice(-1) === "'") || (v[0] === '"' && v.slice(-1) === '"'))) {
    v = v.slice(1, -1).replace(/''/g, "'");
  }
  return v;
}

function parseYaml(text){
  var steps = [], screen = "", cur = null, stepsIndent = -1;
  var lines = String(text || "").split(/\r?\n/);
  function indentOf(l){ return l.length - l.replace(/^\s+/, "").length; }
  function push(){ if (cur && Object.keys(cur).length) steps.push(cur); cur = null; }

  for (var i = 0; i < lines.length; i++) {
    var raw = lines[i];
    if (!raw.trim() || /^\s*#/.test(raw)) continue;
    var ind = indentOf(raw);

    /* In a YAML block sequence the "-" items sit at the SAME indent as the
       parent key, so only a non-item line at that indent (or anything less
       indented, e.g. the next `- screen:`) closes the steps block. */
    if (stepsIndent >= 0) {
      var isItem = /^\s*-\s/.test(raw);
      if (ind < stepsIndent || (ind === stepsIndent && !isItem)) { push(); stepsIndent = -1; }
    }
    if (stepsIndent < 0) {
      var mScreen = raw.match(/^\s*-?\s*screen:\s*(.+)$/);
      if (mScreen) { if (!screen) screen = unquote(mScreen[1]); continue; }
      var mSteps = raw.match(/^(\s*)steps:\s*$/);
      if (mSteps) { stepsIndent = mSteps[1].length; continue; }
      continue;
    }
    var mItem = raw.match(/^\s*-\s*(\w+):\s*(.*)$/);
    if (mItem) { push(); cur = {}; cur[mItem[1]] = unquote(mItem[2]); continue; }
    var mKey = raw.match(/^\s*(\w+):\s*(.*)$/);
    if (mKey && cur) { cur[mKey[1]] = unquote(mKey[2]); continue; }
  }
  push();
  return {steps: steps, screen: screen};
}

/* A plain-English sentence per step, so a tester can proofread without code. */
function humanise(st){
  var a = (st.action || "").toLowerCase(), t = st.target || "", v = st.value || "";
  switch (a) {
    case "open_search":      return "Open the search area";
    case "click":            return "Click <b>" + esc(t) + "</b>";
    case "fill":             return "Type <b>“" + esc(v) + "”</b> into <b>" + esc(t) + "</b>";
    case "select":           return "Choose <b>“" + esc(v) + "”</b> in <b>" + esc(t) + "</b>";
    case "wait":             return "Wait " + esc(t || v) + " seconds";
    case "assert_visible":   return "Check <b>" + esc(t) + "</b> is on screen";
    case "assert_text":      return "Check the page shows <b>“" + esc(v || t) + "”</b>";
    case "assert_no_errors": return "Check there are no errors on the page";
    case "assert_rows_min":  return "Check the list has at least <b>" + esc(t || v) + "</b> rows";
    case "assert_rows_max":  return "Check the list has at most <b>" + esc(t || v) + "</b> rows";
    case "screenshot":       return "Take a screenshot" + (t ? " (<b>" + esc(t) + "</b>)" : "");
    case "todo":             return esc(t);
    default:                 return esc(a) + (t ? " " + esc(t) : "") + (v ? " = " + esc(v) : "");
  }
}

/* One specific question for an unclear step — never an error code. */
function questionFor(text){
  var t = (text || "").toLowerCase();
  if (/\bdb\b|database|sql|table/.test(t))            return "Should we check the database after this step, or is looking at the screen enough?";
  if (/log ?in|login|sign ?in/.test(t))               return "The tool signs in for you already — should we drop this step?";
  if (/not visible|should not|shouldn't/.test(t))     return "What exactly should NOT be on the screen here?";
  if (/select|choose|dropdown|drop-down/.test(t))     return "Which screen is this dropdown on, and what should we choose?";
  if (/verify|check|confirm|expect/.test(t))          return "What should we look at on screen to confirm this worked?";
  return "We couldn't work this step out. Which screen and which box does it mean?";
}

// ------------------------------------------------------------------ rail
function railSet(active){
  /* A converted result proves steps 1 and 2 were completed, even when the
     chosen .xlsx itself can't be restored after a reload. */
  var supplied = hasInput() || S.converted;
  var done = [ supplied, supplied, S.converted && !S.dirty ];
  [1,2,3].forEach(function (n) {
    var el = $("r"+n), num = $("n"+n), isDone = done[n-1] && n !== active;
    el.classList.toggle("on", n === active);
    el.classList.toggle("done", isDone);
    num.innerHTML = isDone ? '<i class="ti ti-check"></i>' : n;
  });
}

[1,2,3].forEach(function (n) {
  $("r"+n).addEventListener("click", function () {
    var el = $("r"+n);
    if (el.classList.contains("on")) return;
    if (!el.classList.contains("done")) {                 // forward — never allowed
      if (n === 3) toast(S.dirty ? "Press Convert again — the result is out of date"
                                 : "Convert first — then the review appears here");
      if (n === 2 && !hasInput()) toast("Add your test steps first — everything else is already filled in");
      return;
    }
    showState("A"); railSet(hasInput() ? 2 : 1);          // back — everything preserved
  });
});

function showState(which){
  showEl($("stateA"), which === "A");
  showEl($("stateB"), which === "B");
  showEl($("stateC"), which === "C");
}

// ------------------------------------------------------------------ input methods
function hasInput(){
  if (S.method === "upload") return !!S.file;
  var v = (S.method === "paste" ? $("paste").value : $("describe").value).trim();
  return v.length > 10;
}

["upload","paste","describe"].forEach(function (m) {
  $("pk-"+m).addEventListener("click", function () {
    S.method = m;
    ["upload","paste","describe"].forEach(function (x) {
      $("pk-"+x).classList.toggle("on", x === m);
      showEl($("pane-"+x), x === m);
    });
    refreshBtn(); markDirty();
  });
});

$("drop").addEventListener("click", function (e) {
  if (e.target.id === "example") return;
  $("file").click();
});
["dragenter","dragover"].forEach(function (ev) {
  $("drop").addEventListener(ev, function (e) { e.preventDefault(); $("drop").classList.add("over"); });
});
["dragleave","drop"].forEach(function (ev) {
  $("drop").addEventListener(ev, function (e) { e.preventDefault(); $("drop").classList.remove("over"); });
});
$("drop").addEventListener("drop", function (e) {
  if (e.dataTransfer.files && e.dataTransfer.files[0]) takeFile(e.dataTransfer.files[0]);
});
$("file").addEventListener("change", function () { if (this.files[0]) takeFile(this.files[0]); });

$("example").addEventListener("click", function (e) {
  e.stopPropagation();
  window.location.href = "/api/template/xlsx";
  toast("Example file downloading — open it to see how test cases are written");
});

function takeFile(f){
  if (!/\.xlsx$/i.test(f.name)) { toast("Please choose an Excel .xlsx file"); return; }
  S.file = f;
  $("fcname").textContent = f.name;

  var base = f.name.replace(/\.xlsx$/i, "");
  var tk = base.match(/STRAT[-_ ]?\d{4,6}/i);
  var ticket = tk ? tk[0].toUpperCase().replace(/[_ ]/g, "-") : "";
  /* The real number of test cases is only known once the server has read the
     file, so the chip says what we honestly know now and is filled in after
     the conversion (see renderResult). */
  $("fcmeta").textContent = (f.size/1024).toFixed(0) + " KB · ready to convert"
                          + (ticket ? " · ticket " + ticket + " detected automatically" : "");
  showEl($("filechip"), true);

  if (!$("testname").value) $("testname").value = base;
  if (ticket && !$("ticket").value) $("ticket").value = ticket;

  refreshBtn(); markDirty(); railSet(2); saveLocal();
  toast("File added — settings filled in for you");
}

$("fcremove").addEventListener("click", function () {
  S.file = null; $("file").value = "";
  showEl($("filechip"), false);
  refreshBtn(); markDirty(); railSet(1); saveLocal();
});

$("paste").addEventListener("input", onTyped);
$("describe").addEventListener("input", onTyped);
function onTyped(){
  refreshBtn(); markDirty();
  railSet(hasInput() ? 2 : 1);
  saveLocal();
}

function refreshBtn(){ $("convertbtn").disabled = !hasInput(); }

// ------------------------------------------------------------------ settings
$("testname").addEventListener("change", function(){ markDirty(); saveLocal(); });
$("ticket").addEventListener("change",   function(){ markDirty(); saveLocal(); });

$("mode-safe").addEventListener("click", function(){ setMode("safe"); });
$("mode-full").addEventListener("click", function(){ setMode("full"); });
function setMode(m){
  S.mode = m;
  $("mode-safe").classList.toggle("on", m === "safe");
  $("mode-full").classList.toggle("on", m === "full");
  $("modehint").textContent = (m === "safe")
    ? "Practice run is selected for you. It clicks through the screens without saving anything, so it's always safe to try."
    : "Full run will create and edit practice data on the test server. The live store is still never touched.";
  markDirty(); saveLocal();
}

$("server").addEventListener("change", function () {
  $("serverdot").className = "dot";
  $("serverstate").textContent = "not checked yet";
  saveLocal();
});

$("servercheck").addEventListener("click", function () {
  var p = S.profiles[Number($("server").value)];
  if (!p || !p.url) { toast("Pick a server first — add one on the Run & Reports page"); return; }
  $("serverdot").className = "dot";
  $("serverstate").textContent = "checking…";
  api("/api/test-connection", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url: p.url, user: p.user || "", password: p.password || "",
                          machine_id: p.machine_id || ""})
  }).then(function (j) {
    var ok = j && (j.ok === true || j.success === true || j.status === "ok");
    $("serverdot").className = "dot " + (ok ? "ok" : "busy");
    $("serverstate").textContent = ok ? "reachable — safe to run" : "not responding right now";
  }).catch(function (e) {
    $("serverdot").className = "dot bad";
    $("serverstate").textContent = "could not reach it";
    toast("Server check failed: " + e.message);
  });
});

// ------------------------------------------------------------------ stale guard
function markDirty(){
  if (!S.converted) return;
  S.dirty = true;
  $("stale").classList.add("show");
  $("convertbtn").textContent = "Convert again  →";
  railSet(2);
}
function clearDirty(){
  S.dirty = false;
  $("stale").classList.remove("show");
  $("convertbtn").textContent = "Convert my test cases  →";
}

// ------------------------------------------------------------------ autosave
/* The screen promises "you can close this page and come back anytime", so it
   has to be true. The generated test and the settings live in localStorage;
   the chosen .xlsx cannot be restored, which the notice below makes clear. */
function saveLocal(){
  try {
    localStorage.setItem(SAVE_KEY, JSON.stringify({
      method: S.method, yaml: S.yaml, screen: S.screen, total: S.total, tests: S.tests,
      todos: S.todos, converted: S.converted, mode: S.mode,
      testname: $("testname").value, ticket: $("ticket").value,
      paste: $("paste").value, describe: $("describe").value,
      server: $("server").value, hadFile: !!S.file
    }));
  } catch (e) {}
}

function restoreLocal(){
  var raw;
  try { raw = localStorage.getItem(SAVE_KEY); } catch (e) { return; }
  if (!raw) return;
  var d;
  try { d = JSON.parse(raw); } catch (e) { return; }
  if (!d) return;

  $("testname").value = d.testname || "";
  $("ticket").value   = d.ticket   || "";
  $("paste").value    = d.paste    || "";
  $("describe").value = d.describe || "";
  if (d.mode) setModeQuiet(d.mode);
  if (d.method) pickQuiet(d.method);

  if (d.converted && d.yaml) {
    S.yaml = d.yaml; S.screen = d.screen || ""; S.total = d.total || 0;
    S.tests = d.tests || 1; S.todos = d.todos || []; S.converted = true;
    S.steps = parseYaml(S.yaml).steps;
    renderReview();
    showState("C"); railSet(3);
    if (d.hadFile && d.method === "upload") {
      toast("Welcome back — your converted test is here. Re-add the file only if you want to convert again.");
    } else {
      toast("Welcome back — your converted test is still here");
    }
    return;
  }
  refreshBtn(); railSet(hasInput() ? 2 : 1);
}

function setModeQuiet(m){
  S.mode = m;
  $("mode-safe").classList.toggle("on", m === "safe");
  $("mode-full").classList.toggle("on", m === "full");
  $("modehint").textContent = (m === "safe")
    ? "Practice run is selected for you. It clicks through the screens without saving anything, so it's always safe to try."
    : "Full run will create and edit practice data on the test server. The live store is still never touched.";
}
function pickQuiet(m){
  S.method = m;
  ["upload","paste","describe"].forEach(function (x) {
    $("pk-"+x).classList.toggle("on", x === m);
    showEl($("pane-"+x), x === m);
  });
}

// ------------------------------------------------------------------ convert
var timers = [];
function progress(step, pct, text){
  [1,2,3,4].forEach(function (n) {
    var el = $("p"+n);
    el.className = "prow" + (n < step ? " done" : n === step ? " doing" : "");
    el.querySelector(".ic").innerHTML = n < step ? '<i class="ti ti-check"></i>'
                                      : n === step ? '<i class="ti ti-loader-2"></i>'
                                      : '<i class="ti ti-point"></i>';
  });
  $("bar").style.width = pct + "%";
  if (text) $("ptext").textContent = text;
}
function detail(n, text){
  var b = $("p"+n).querySelector("b");
  if (b) b.textContent = text;
}
function stopConvert(){
  timers.forEach(clearTimeout); timers = [];
  if (S.abort) { try { S.abort.abort(); } catch (e) {} S.abort = null; }
}

$("cancel").addEventListener("click", function () {
  stopConvert();
  showState("A"); railSet(2);
  toast("Cancelled — your steps are still here, nothing was lost");
});

$("convertbtn").addEventListener("click", function () {
  if (!hasInput()) return;
  clearFail(); stopConvert(); clearDirty();
  showState("B"); railSet(3);

  progress(1, 12, "Reading your steps…");
  timers = [
    setTimeout(function(){ progress(2, 38, "Understanding your steps…"); }, 800),
    setTimeout(function(){ progress(3, 66, "Matching steps to Stratus screens…"); }, 1700),
    setTimeout(function(){ progress(4, 88, "Building your automatic test…"); }, 2700)
  ];

  S.abort = new AbortController();
  var req;
  if (S.method === "upload") {
    var fd = new FormData();
    fd.append("file", S.file);
    fd.append("use_llm", S.aiOn ? "1" : "0");
    req = api("/api/import-testcases", {method: "POST", body: fd, signal: S.abort.signal});
  } else {
    var text = (S.method === "paste" ? $("paste").value : $("describe").value).trim();
    req = api("/api/nl-to-yaml", {
      method: "POST", headers: {"Content-Type": "application/json"}, signal: S.abort.signal,
      body: JSON.stringify({prompt: text, use_llm: S.aiOn, safe_mode: S.mode === "safe"})
    });
  }

  req.then(function (j) {
    timers.forEach(clearTimeout);
    S.abort = null;
    renderResult(j);
  }).catch(function (err) {
    timers.forEach(clearTimeout);
    if (err && err.name === "AbortError") return;      // the user pressed Cancel
    S.abort = null;
    showState("A"); railSet(2);
    fail("<b>We couldn't convert that.</b> " + esc(err.message) +
         "<br>Check the file is a normal Excel test-case sheet, or try the example file.");
  });
});

function renderResult(j){
  S.yaml   = j.yaml || "";
  S.screen = j.screen || (j.screens && j.screens[0]) || "";
  var parsed = parseYaml(S.yaml);
  S.steps  = parsed.steps;

  /* No steps means we found nothing to automate — usually the wrong file.
     Say so, rather than reporting a cheerful "all 0 steps are ready to run". */
  if (!S.steps.length) {
    stopConvert();
    showState("A"); railSet(hasInput() ? 2 : 1);
    fail("<b>We couldn't find any test cases in that file.</b> " +
         "It needs a column of test steps — a heading like <i>Test Steps</i> or <i>Steps</i>, " +
         "with one step per line. Try the example file from the drop zone to see the format.");
    return;
  }

  if (!S.screen) S.screen = parsed.screen;
  S.total  = j.n_steps_total || S.steps.length;
  S.tests  = j.n_tests || 1;
  S.converted = true;

  S.todos = [];
  S.steps.forEach(function (s) {
    if ((s.action || "") === "todo") S.todos.push({text: s.target || "", state: "open"});
  });

  // the counts are known now — finish the narration with real numbers
  detail(1, S.method === "upload"
    ? "Read your file — found " + S.tests + (S.tests === 1 ? " test case" : " test cases") +
      " with " + S.total + " steps"
    : "Read your steps — " + S.total + " in total");
  detail(2, "Understood your steps — written in plain English, got it");
  detail(3, "Matched your steps to real Stratus screens");
  detail(4, "Built your automatic test");
  progress(5, 100, "Done!");

  if (S.method === "upload" && S.file) {
    $("fcmeta").textContent = S.tests + (S.tests === 1 ? " test case" : " test cases") + " found"
      + ($("ticket").value ? " · ticket " + $("ticket").value + " detected automatically" : "");
  }

  setTimeout(function () {
    renderReview();
    showState("C"); railSet(3); saveLocal();
  }, 700);
}

// ------------------------------------------------------------------ review
function renderReview(){
  paintVerdict(); paintQuestions(); paintSteps();
  $("techarea").value = S.yaml;
}

function counts(){
  var open    = S.todos.filter(function(t){ return t.state === "open"; }).length;
  var skipped = S.todos.filter(function(t){ return t.state === "skipped"; }).length;
  return {open: open, skipped: skipped, ready: Math.max(0, S.total - open - skipped)};
}

function paintVerdict(){
  var c = counts();
  $("verdicticon").className = c.open ? "ti ti-help-circle" : "ti ti-circle-check";

  if (c.open) {
    $("verdicttxt").textContent = "Done! We understood " + c.ready + " of your " + S.total + " steps.";
    $("verdictsub").textContent = c.open + (c.open === 1 ? " step needs" : " steps need") +
                                  " a quick answer from you — see below. Everything is saved.";
  } else if (c.skipped) {
    $("verdicttxt").textContent = "Ready! " + c.ready + " steps will run (" + c.skipped + " skipped by you).";
    $("verdictsub").textContent = "Press “Run the test now” whenever you like.";
  } else {
    $("verdicttxt").textContent = "Perfect — all " + S.total + " steps are ready to run!";
    $("verdictsub").textContent = "Press “Run the test now” whenever you like.";
  }

  var html = '<span class="chip ok"><i class="ti ti-check"></i> ' + c.ready + " ready</span>";
  if (c.open)    html += '<span class="chip warn"><i class="ti ti-help-circle"></i> ' + c.open + " need you</span>";
  if (c.skipped) html += '<span class="chip skip">' + c.skipped + " skipped</span>";
  if (S.tests > 1) html += '<span class="chip skip">' + S.tests + " test cases</span>";
  $("chips").innerHTML = html;
  $("acccount").textContent = c.ready;
}

function paintQuestions(){
  var c = counts();
  var host = $("questions");
  host.innerHTML = "";
  showEl($("qhead"), S.todos.length > 0);
  $("qhead").textContent = c.open
    ? "Help us with these " + c.open + " step" + (c.open === 1 ? "" : "s")
    : (c.skipped ? "All questions answered or skipped ✓" : "All questions answered ✓");

  S.todos.forEach(function (t, i) {
    var d = document.createElement("div");
    d.className = "qcard" + (t.state === "fixed" ? " fixed" : t.state === "skipped" ? " skipped" : "");
    d.innerHTML = '<div class="orig">Your step said: “' + esc(t.text) + '”</div>';

    if (t.state === "open") {
      d.innerHTML +=
        '<div class="q"><i class="ti ti-help-circle"></i> ' + esc(questionFor(t.text)) + '</div>' +
        '<div class="row">' +
          '<input class="inp" placeholder="Type your answer, e.g. “It\'s the Brand dropdown on the product detail page”">' +
          '<button class="qbtn">That fixes it</button>' +
        '</div>' +
        '<button class="qskip">Skip for now — run the test without this step</button>';
      var input = d.querySelector("input"), btn = d.querySelector(".qbtn"), skip = d.querySelector(".qskip");
      btn.addEventListener("click", function(){ applyFix(i, input.value, btn); });
      input.addEventListener("keydown", function(e){ if (e.key === "Enter") btn.click(); });
      skip.addEventListener("click", function () {
        t.state = "skipped"; renderReview(); saveLocal();
      });
    } else if (t.state === "fixed") {
      d.innerHTML += '<div class="fixednote"><i class="ti ti-circle-check"></i> Fixed — we updated the step from your answer</div>';
    } else {
      d.innerHTML += '<div class="skipnote">Skipped — the test will run without this step (shown in the report)</div>';
    }
    host.appendChild(d);
  });
}

/* The tester's own words go to the server, which applies them with the rule
   engine first and the AI only for what the rules can't work out. */
function applyFix(i, answer, btn){
  answer = (answer || "").trim();
  if (!answer) { toast("Type a quick answer first — in your own words is fine"); return; }
  var t = S.todos[i], before = S.yaml, old = btn.innerHTML;
  btn.innerHTML = '<span class="spin"></span>Applying'; btn.disabled = true;

  api("/api/modify-testcases", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({yaml: S.yaml, screen: S.screen, prompt: answer, use_llm: S.aiOn})
  }).then(function (j) {
    var changed = !!j.yaml && j.yaml !== before;
    if (changed) { S.yaml = j.yaml; S.steps = parseYaml(S.yaml).steps; }

    var stillTodo = S.steps.some(function (s) {
      return (s.action || "") === "todo" && (s.target || "") === t.text;
    });

    if (!stillTodo || changed) {
      t.state = "fixed";
      renderReview(); saveLocal();
      toast("Step fixed ✓ your answer was turned into the technical fix");
    } else {
      btn.innerHTML = old; btn.disabled = false;
      var why = (j.llm_used === false && j.llm_error) ? j.llm_error
              : "we couldn't turn that into a test step automatically";
      toast("Not applied — " + why);
      fail("<b>“" + esc(answer) + "” wasn't applied.</b> Try naming the screen and the box " +
           "(e.g. <i>“it's the Brand dropdown on the product detail screen”</i>), " +
           "or skip this step and carry on." +
           (S.aiOn ? "" : " The AI is not configured on this server, so only direct instructions work."));
    }
  }).catch(function (e) {
    btn.innerHTML = old; btn.disabled = false;
    toast("Could not apply that: " + e.message);
  });
}

function paintSteps(){
  var host = $("accbody");
  host.innerHTML = "";
  var n = 0;
  S.steps.forEach(function (s) {
    if ((s.action || "") === "todo") return;
    n++;
    var r = document.createElement("div");
    r.className = "steprow";
    r.innerHTML = '<i class="ti ti-check"></i> Step ' + n + " — " + humanise(s);
    host.appendChild(r);
  });
  if (!n) host.innerHTML = '<div class="steprow">No steps understood yet — answer the questions above.</div>';
}

$("acc").addEventListener("click", function () {
  var open = $("accbody").classList.toggle("open");
  $("accicon").className = open ? "ti ti-chevron-up" : "ti ti-chevron-down";
});

// ------------------------------------------------------------------ technical file
$("techtoggle").addEventListener("click", function () {
  $("tech").classList.toggle("open");
});
$("tedit").addEventListener("click", function () {
  S.yamlBackup = $("techarea").value;
  $("techarea").removeAttribute("readonly");
  $("techarea").focus();
  $("techstatus").textContent = "Editing"; $("techstatus").classList.add("editing");
  showEl($("tedit"), false); showEl($("tsave"), true); showEl($("tundo"), true);
  toast("You can now edit the file directly — Save or Undo when done");
});
function endEdit(){
  $("techarea").setAttribute("readonly", "readonly");
  $("techstatus").textContent = "View only"; $("techstatus").classList.remove("editing");
  showEl($("tedit"), true); showEl($("tsave"), false); showEl($("tundo"), false);
}
$("tsave").addEventListener("click", function () {
  var text = $("techarea").value;
  var parsed = parseYaml(text);
  if (!parsed.steps.length) {
    toast("That file has no steps we can read — check it, or press Undo");
    return;
  }
  S.yaml = text; S.steps = parsed.steps;
  if (parsed.screen) S.screen = parsed.screen;
  S.total = S.steps.length;
  S.todos = [];
  S.steps.forEach(function (s) {
    if ((s.action || "") === "todo") S.todos.push({text: s.target || "", state: "open"});
  });
  endEdit(); renderReview(); saveLocal();
  toast("Changes saved ✓ your edited file will be used when you press Run");
});
$("tundo").addEventListener("click", function () {
  if (S.yamlBackup != null) $("techarea").value = S.yamlBackup;
  endEdit();
  toast("Changes undone — back to the file we generated");
});

// ------------------------------------------------------------------ finish
$("savebtn").addEventListener("click", function () {
  var btn = this, old = btn.innerHTML;
  var title = $("testname").value.trim() || "Untitled test";
  var id = ($("ticket").value.trim() || title).replace(/[^A-Za-z0-9_-]+/g, "-")
             .replace(/^-+|-+$/g, "").slice(0, 60);
  if (!id) { toast("Give the test a name first"); return; }

  btn.innerHTML = '<span class="spin"></span>Saving'; btn.disabled = true;
  api("/api/scenarios", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      id: id, title: title, yaml: S.yaml, screen: S.screen,
      description: $("ticket").value.trim() ? ("Ticket " + $("ticket").value.trim()) : "",
      tags: $("ticket").value.trim() ? [$("ticket").value.trim()] : [],
      overwrite: true
    })
  }).then(function () {
    toast("Saved to your library — find it any time under “Scenarios”");
  }).catch(function (e) {
    toast("Could not save: " + e.message);
  }).then(function () { btn.innerHTML = old; btn.disabled = false; });
});

$("runbtn").addEventListener("click", function () {
  var c = counts();
  if (c.open && !confirm(c.open + " step(s) still need an answer. They will be skipped. Run anyway?")) return;

  var p = S.profiles[Number($("server").value)];
  if (!p || !p.url) { toast("Pick a server first — add one on the Run & Reports page"); return; }

  var btn = this, old = btn.innerHTML;
  btn.innerHTML = '<span class="spin"></span>Starting'; btn.disabled = true;

  api("/api/run", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      url: p.url, user: p.user || "", password: p.password || "", machine_id: p.machine_id || "",
      screen: S.screen || "customer",
      single_mode: true, single_screenname: S.screen || "",
      single_safe: S.mode === "safe", read_only: S.mode === "safe",
      custom_tests_yaml: S.yaml
    })
  }).then(function () {
    toast("Test started on " + (p.name || "the test server") + " — opening the live view…");
    setTimeout(function(){ window.location.href = "/"; }, 1000);
  }).catch(function (e) {
    btn.innerHTML = old; btn.disabled = false;
    fail("<b>Could not start the run.</b> " + esc(e.message));
  });
});

$("startover").addEventListener("click", function () {
  if (!confirm("Start over? Your converted test will be cleared.")) return;
  stopConvert();
  S.file = null; S.yaml = ""; S.steps = []; S.todos = [];
  S.converted = false; S.total = 0; S.tests = 1;
  $("file").value = ""; $("paste").value = ""; $("describe").value = "";
  $("testname").value = ""; $("ticket").value = "";
  showEl($("filechip"), false);
  clearDirty(); clearFail();
  try { localStorage.removeItem(SAVE_KEY); } catch (e) {}
  showState("A"); refreshBtn(); railSet(1);
});

// ------------------------------------------------------------------ boot
function boot(){
  api("/api/auth/status").then(function (j) {
    $("who").innerHTML = j && j.user ? ("Signed in as <b>" + esc(j.user) + "</b>") : "";
  }).catch(function(){});

  api("/api/profiles").then(function (list) {
    S.profiles = Array.isArray(list) ? list : [];
    var sel = $("server");
    sel.innerHTML = "";
    if (!S.profiles.length) {
      sel.innerHTML = '<option value="">No saved server yet — add one on Run &amp; Reports</option>';
      return;
    }
    S.profiles.forEach(function (p, i) {
      var o = document.createElement("option");
      o.value = String(i);
      o.textContent = (p.name || ("Server " + (i+1))) + (p.url ? "  ·  " + p.url : "");
      sel.appendChild(o);
    });
    try {
      var d = JSON.parse(localStorage.getItem(SAVE_KEY) || "{}");
      if (d.server && sel.options[Number(d.server)]) sel.value = d.server;
    } catch (e) {}
  }).catch(function () {
    $("server").innerHTML = '<option value="">Could not load servers</option>';
  });

  /* The AI is used automatically whenever the server has it configured —
     there is no switch on this screen, exactly as in the design. */
  api("/api/llm-status").then(function (j) {
    S.aiOn = !!(j && (j.available || j.configured || j.enabled));
  }).catch(function(){ S.aiOn = false; });

  restoreLocal();
  refreshBtn();
}

boot();
})();
