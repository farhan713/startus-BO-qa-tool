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
/* The structured view of the imported test: scenarios, their source lines,
   and their steps. Declared beside S because restoreLocal() writes to it. */
var ED = { scenarios: [], cov: null, filter: null };

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
      var e = new Error("login required");
      e.needsLogin = true;
      throw e;
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

/* Picking a saved server just fills in the connection panel on stage 3,
   which is the one place a run is configured from. */
$("server").addEventListener("change", function () {
  var p = S.profiles[Number($("server").value)];
  if (p) {
    if (p.url) $("cnUrl").value = p.url;
    if (p.user) $("cnUser").value = p.user;
    if (p.machine_id) $("cnMachine").value = p.machine_id;
    setConn("", "not checked yet");
  }
  $("serverdot").className = "dot";
  $("serverstate").textContent = p ? "filled in below" : "not checked yet";
  saveLocal();
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
      server: $("server").value, hadFile: !!S.file,
      /* The structured view too. A File object cannot survive a reload, so on
         restore edLoad used to rebuild from the YAML — and YAML carries no
         source sentences, so every scenario came back with no lines and every
         step was relabelled "added by the tool". Keeping the real structure is
         the only way a reload shows the tester her own words again. */
      scenarios: ED.scenarios, cov: ED.cov, sheet: S.sheet
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

  if (d.scenarios && d.scenarios.length){ ED.scenarios = d.scenarios; ED.cov = d.cov || null; }
  if (d.sheet) S.sheet = d.sheet;

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
    if ((s.action || "") === "todo") S.todos.push({text: s.target || "", state: "open", kind: "step"});
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

  /* Already have the real structure (fresh import, or restored from a reload)?
     Use it. Going back through YAML would silently drop every source sentence. */
  if (ED.scenarios && ED.scenarios.length && !S.file){
    showEl($("edtools"), true);
    edRender(); edCounts();
    return;
  }
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
  /* Stage 3 is the scenario/step editor now — edLoad() fetches the structured
     view and paints the verdict from it. The old per-step question cards and
     the "steps we understood" accordion are gone: both showed the same steps,
     split by whether a regex happened to match, which is not a distinction a
     tester cares about. */
  edSheets();
  edLoad();
  $("techarea").value = S.yaml;
}

function counts(){
  var open    = S.todos.filter(function(t){ return t.state === "open" && isQuestion(t); }).length;
  var skipped = S.todos.filter(function(t){ return t.state === "skipped"; }).length;
  var context = S.todos.filter(function(t){ return !isQuestion(t); }).length;
  return {open: open, skipped: skipped, context: context,
          ready: Math.max(0, S.total - open - skipped - context)};
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
}

/* Ask the server to label each todo. Purely additive: if the call fails every
   row keeps its default kind of "step" and the screen behaves as it did. */


var KIND_NOTE = {
  setup_db:  "Database or module setup — do this before the run, not during it.",
  setup_sec: "A permission that must be granted first.",
  rule:      "A business rule or expected result — not a step to perform."
};

function isQuestion(t){ return (t.kind || "step") === "step"; }



/* The tester's own words go to the server, which applies them with the rule
   engine first and the AI only for what the rules can't work out. */






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


/* Converting works signed out, but running a test and saving to the library
   both need a Stratus QA account. Say that plainly instead of "login required". */
function needsLogin(what){
  fail('<b>Sign in to ' + esc(what) + '.</b> Converting works without an account, but ' +
       esc(what) + ' needs one. <a href="/" target="_blank">Open Stratus QA</a>, create or sign in ' +
       'to your account, then come back to this tab — your converted test is still here.');
}

// ------------------------------------------------------------------ run connection
/* Read straight from the fields on screen. The password is deliberately never
   written to localStorage, never saved to a profile, and never echoed back —
   it exists only for the duration of this one run. */
function connection(){
  var url = $("cnUrl").value.trim();
  return {
    url: url,
    user: $("cnUser").value.trim(),
    password: $("cnPass").value,
    machine_id: $("cnMachine").value.trim(),
    label: url.replace(/^https?:\/\//, "").split("/")[0] || "the test server"
  };
}

function setConn(state, text){
  $("conndot").className = "dot" + (state ? " " + state : "");
  $("connstate").textContent = text;
}

/* Prefill the address and username from a saved profile if the team has one,
   so the demo does not start from a blank form. The password is never
   prefilled, even when the profile has one stored. */
function prefillConnection(){
  var p = S.profiles && S.profiles[0];
  if (!p) return;
  if (!$("cnUrl").value && p.url) $("cnUrl").value = p.url;
  if (!$("cnUser").value && p.user) $("cnUser").value = p.user;
  if (!$("cnMachine").value && p.machine_id) $("cnMachine").value = p.machine_id;
}

$("cntest").addEventListener("click", function () {
  var p = connection();
  if (!p.url) { toast("Enter the BackOffice address first"); $("cnUrl").focus(); return; }
  var btn = this, old = btn.innerHTML;
  btn.innerHTML = '<span class="spin"></span>Checking'; btn.disabled = true;
  setConn("", "checking\u2026");
  api("/api/test-connection", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url: p.url, user: p.user, password: p.password, machine_id: p.machine_id})
  }).then(function (j) {
    var ok = j && (j.ok === true || j.success === true || j.status === "ok");
    setConn(ok ? "ok" : "bad", ok ? "connected \u2014 ready to run"
                                  : (j && j.message ? j.message : "could not sign in"));
    if (!ok) toast("Could not sign in. Check the address, username and password.");
  }).catch(function (e) {
    setConn("bad", "could not reach it");
    toast("Connection failed: " + e.message);
  }).then(function () { btn.innerHTML = old; btn.disabled = false; });
});

["cnUrl","cnUser","cnPass","cnMachine"].forEach(function (id) {
  $(id).addEventListener("input", function () { setConn("", "not checked yet"); });
});

// ------------------------------------------------------------------ live run
/* The run is watched here rather than on the dashboard. The dashboard needs an
   account; the Convert screen is meant to stand on its own. */
var liveTimer = null, liveSeen = 0;

function liveIcon(t){
  if (t === "ok")   return "\u2713";
  if (t === "fail") return "!";
  if (t === "screenshot") return "\u25a3";
  if (t === "section") return "";
  return "\u00b7";
}

function paintLive(j){
  var ev = (j && j.event_log) || [];
  var host = $("livelog");
  for (var i = liveSeen; i < ev.length; i++) {
    var e = ev[i], type = e.type || "info";
    var txt = (e.text || "").trim();
    if (!txt && type !== "screenshot") continue;
    var d = document.createElement("div");
    d.className = "lrow " + type;
    d.innerHTML = '<span class="li">' + liveIcon(type) + "</span><span>" +
                  esc(txt || e.screenshot || "") + "</span>";
    host.appendChild(d);
  }
  if (ev.length !== liveSeen) { liveSeen = ev.length; host.scrollTop = host.scrollHeight; }

  var ok = ev.filter(function(e){ return e.type === "ok"; }).length;
  var bad = ev.filter(function(e){ return e.type === "fail"; }).length;
  var chips = "";
  if (ok)  chips += '<span class="chip ok">' + ok + " passed</span>";
  if (bad) chips += '<span class="chip warn">' + bad + " to look at</span>";
  $("livecounts").innerHTML = chips;

  if (j && j.running) {
    $("livestate").textContent = "Running on " + ((j.config && j.config.screen) || "Stratus") + "\u2026";
    $("livepulse").className = "pulse";
  } else {
    stopLive();
    $("livepulse").className = "pulse " + (bad ? "bad" : "ok");
    $("livestate").textContent = bad ? "Finished with " + bad + " step(s) to look at"
                                     : "Finished — everything passed";
    paintSummary(j && j.result);
  }
}


/* When the run ends, show the numbers here. The dashboard needs an account and
   there is no run report for web-UI runs, so this is the report. */
function paintSummary(r){
  if (!r) return;
  var total = r.steps_total || 0, ok = r.steps_passed || 0, bad = r.steps_failed || 0;
  var secs = Math.round(r.duration_s || 0);
  var mins = Math.floor(secs / 60), rest = secs % 60;
  var dur = mins ? (mins + "m " + rest + "s") : (secs + "s");

  var html = '<div class="sumrow">' +
    '<div class="sumcell"><span class="sv">' + total + '</span><span class="sl">steps run</span></div>' +
    '<div class="sumcell good"><span class="sv">' + ok + '</span><span class="sl">passed</span></div>' +
    '<div class="sumcell bad"><span class="sv">' + bad + '</span><span class="sl">to look at</span></div>' +
    '<div class="sumcell"><span class="sv">' + dur + '</span><span class="sl">taken</span></div>' +
  "</div>";

  var f = r.failures || [];
  if (f.length) {
    html += '<div class="sumfails">';
    f.slice(0, 40).forEach(function (row) {
      var name = Array.isArray(row) ? row[0] : (row && row.name) || "";
      var why  = Array.isArray(row) ? row[1] : (row && row.reason) || "";
      html += '<div class="sumfail"><b>' + esc(name) + "</b>" + esc(why) + "</div>";
    });
    if (f.length > 40) html += '<div class="sumfail">\u2026 and ' + (f.length - 40) + " more</div>";
    html += "</div>";
  }
  $("livesum").innerHTML = html;
  showEl($("livesum"), true);
}

function pollLive(){
  api("/api/status").then(paintLive).catch(function(){ /* keep polling */ });
}

function startLive(){
  liveSeen = 0;
  $("livelog").innerHTML = "";
  $("livecounts").innerHTML = "";
  showEl($("livesum"), false);
  $("livestate").textContent = "Starting the test\u2026";
  $("livepulse").className = "pulse";
  showEl($("live"), true);
  $("live").scrollIntoView({block: "start", behavior: "smooth"});
  pollLive();
  clearInterval(liveTimer);
  liveTimer = setInterval(pollLive, 1500);
}

function stopLive(){ clearInterval(liveTimer); liveTimer = null; }

$("livedone").addEventListener("click", function () {
  stopLive();
  showEl($("live"), false);
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
    if (e && e.needsLogin) { needsLogin("save to your library"); return; }
    toast("Could not save: " + e.message);
  }).then(function () { btn.innerHTML = old; btn.disabled = false; });
});

$("runbtn").addEventListener("click", function () {
  var c = counts();
  if (c.open && !confirm(c.open + " step(s) still need an answer. They will be skipped. Run anyway?")) return;

  var p = connection();
  if (!p.url)  { toast("Enter the BackOffice address first"); $("cnUrl").focus(); return; }
  if (!p.user) { toast("Enter the username the test should sign in with"); $("cnUser").focus(); return; }

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
    btn.innerHTML = old; btn.disabled = false;
    toast("Test started on " + (p.label || "the test server"));
    startLive();
  }).catch(function (e) {
    btn.innerHTML = old; btn.disabled = false;
    if (e && e.needsLogin) { needsLogin("run a test"); return; }
    fail("<b>Could not start the run.</b> " + esc(e.message));
  });
});

$("startover").addEventListener("click", function () {
  if (!confirm("Start over? Your converted test will be cleared.")) return;
  stopConvert(); stopLive(); showEl($("live"), false);
  S.file = null; S.yaml = ""; S.steps = []; S.todos = [];
  S.converted = false; S.total = 0; S.tests = 1;
  $("file").value = ""; $("paste").value = ""; $("describe").value = "";
  $("testname").value = ""; $("ticket").value = "";
  $("cnPass").value = "";
  setConn("", "not checked yet");
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
    prefillConnection();
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

/* ══════════════════════════════════════════════════════════════════════════
   Scenario / step editor  (was the separate /review page)

   Stage 3 used to be a stack of question cards — one per unrecognised step,
   each asking "which box does this mean?". That framing made the tool look
   like it was handing the work back, and it asked about rows that were never
   steps. The editor shows the file the way the spreadsheet is written:
   scenarios, each holding its steps, every one editable in place.
   ═══════════════════════════════════════════════════════════════════════ */


var ED_ACTIONS = [
  ["click","Click"], ["fill","Type into"], ["select","Choose from list"],
  ["check","Tick checkbox"], ["open_search","Open search panel"],
  ["assert_visible","Check it is shown"], ["assert_not_visible","Check it is hidden"],
  ["assert_no_errors","Check for no errors"], ["assert_rows_min","Check rows found"],
  ["screenshot","Take a screenshot"], ["todo","Needs input"]
];
var ED_ALIAS = { setup_db:"setup", setup_sec:"perm", rule:"rule", step:"step" };
var ED_TEXT  = { step:"Needs input", setup:"Setup", perm:"Permission", rule:"Rule" };

/* Pull the structured view for whatever the tester supplied. Upload goes
   through the importer; pasted or described tests arrive as YAML. */
/* Offer the workbook's sheets. Only one is imported at a time — a workbook is
   several independent suites, and merging them produces a scenario count that
   matches no sheet the tester can see. */
function edSheets(){
  if (!(S.method === "upload" && S.file)) { showEl($("sheetpick"), false); return; }
  var fd = new FormData(); fd.append("file", S.file);
  fetch("/api/sheets", {method:"POST", body:fd})
    .then(function(r){ return r.json(); })
    .then(function(d){
      var sheets = (d && d.sheets) || [];
      if (sheets.length < 2) { showEl($("sheetpick"), false); return; }
      if (!S.sheet) {
        var first = sheets.filter(function(s){ return s.looks_like_tests; })[0];
        S.sheet = first ? first.name : sheets[0].name;
      }
      $("splist").innerHTML = sheets.map(function(s){
        return '<button class="sp-btn'+(s.name===S.sheet?" on":"")+'"'+
               (s.looks_like_tests?"":" disabled")+' data-sheet="'+esc(s.name)+'">'+
               esc(s.name)+'<span class="n">'+s.rows+' rows</span></button>';
      }).join("");
      showEl($("sheetpick"), true);
    })
    .catch(function(){ showEl($("sheetpick"), false); });
}

$("splist").addEventListener("click", function(e){
  var b = e.target.closest(".sp-btn");
  if (!b || b.disabled) return;
  S.sheet = b.dataset.sheet;
  Array.prototype.forEach.call(this.children, function(x){
    x.classList.toggle("on", x === b); });
  edLoad();
});

function edLoad(){
  var done = function (d) {
    if (!d || d.error) return;
    ED.scenarios = d.scenarios || [];
    ED.cov = {lines: (d.summary||{}).lines || 0,
              covered: (d.summary||{}).lines_covered || 0};
    showEl($("edtools"), ED.scenarios.length > 0);
    edRender(); edCounts();
    /* saveLocal() already ran when the conversion returned, but edLoad is async
       and had not filled ED yet — so the draft was written with an empty
       structure and a reload fell back to the lossy YAML rebuild. */
    saveLocal();
  };
  if (S.method === "upload" && S.file) {
    var fd = new FormData();
    fd.append("file", S.file);
    fd.append("screen", S.screen || "yourscreen");
    if (S.sheet) fd.append("sheet", S.sheet);
    fetch("/api/import-structured", {method:"POST", body:fd})
      .then(function(r){ return r.json(); }).then(done).catch(function(){});
  } else {
    fetch("/api/yaml-to-structured", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({yaml: S.yaml, screen: S.screen || "yourscreen"})
    }).then(function(r){ return r.json(); }).then(done).catch(function(){});
  }
}

function edBadges(sc){
  var ready=0, needs=0, other=0;
  sc.steps.forEach(function(s){
    if (s.action !== "todo") ready++;
    else if ((s.kind || "step") === "step") needs++;
    else other++;
  });
  return {ready:ready, needs:needs, other:other};
}

function edBadgeHTML(c){
  return (c.ready ? '<span class="ib ready">'+c.ready+' ready</span>' : '') +
         (c.needs ? '<span class="ib needs">'+c.needs+' need input</span>' : '') +
         (c.other ? '<span class="ib rule">'+c.other+' not steps</span>' : '');
}

/* Grammar per action. A step reads as a sentence and each ⟨slot⟩ is a chip the
   tester clicks to edit. A hole in a sentence is self-evident, which is why
   these rows carry no "needs input" badge — the empty dashed chip IS the
   signal, and it does not compete with 160 identical-looking text boxes. */
var ED_GRAMMAR = {
  click:              ["target"],
  fill:               ["value", " into ", "target"],
  select:             ["value", " from ", "target"],
  check:              ["target"],
  open_search:        [],
  assert_visible:     ["target", " is on screen"],
  assert_not_visible: ["target", " is hidden"],
  assert_no_errors:   [],
  assert_rows_min:    ["target", " rows came back"],
  screenshot:         ["target"],
  todo:               ["target"]
};
var ED_HOLE = { target: "which field?", value: "what value?" };

function edChip(st, slot){
  var v = (slot === "value" ? st.value : st.target) || "";
  return '<button type="button" class="chip-s'+(v ? "" : " hole")+'" data-slot="'+slot+'">'+
         (v ? esc(v) : ED_HOLE[slot]) + '</button>';
}

function edRow(sc, st, i){
  var g = ED_GRAMMAR[st.action] || ED_GRAMMAR.todo;
  var body = g.map(function(part){
    return (part === "target" || part === "value") ? edChip(st, part) : esc(part);
  }).join("");
  var needs = st.action === "todo" || !st.target;
  var verb  = '<button type="button" class="chip-v" data-slot="action">'+
              esc(edVerbLabel(st.action))+'</button>';
  return '<div class="srow'+(needs?" needs":"")+(st.edited?" edited":"")+
         (st.skipped?" skipped":"")+'" data-sc="'+sc.id+'" data-st="'+st.id+'" tabindex="0">'+
    '<span class="srn">'+(i+1)+'</span>'+
    '<span class="ssent">'+verb+' '+body+'</span>'+
    (st.skipped ? '<span class="pill">Skipped</span>' : '')+
    '<button class="smenu" type="button" title="More">&#8943;</button>'+
  '</div>';
}

function edVerbLabel(a){
  var m = {click:"Click", fill:"Type", select:"Choose", check:"Tick",
           open_search:"Open search", assert_visible:"Check shown",
           assert_not_visible:"Check hidden", assert_no_errors:"Check no errors",
           assert_rows_min:"Check rows", screenshot:"Screenshot", todo:"?"};
  return m[a] || a;
}

function edCard(sc){
  var c = edBadges(sc);
  var pre = sc.steps.filter(function(s){
    return s.action==="todo" && (s.kind==="setup_db" || s.kind==="setup_sec"); });
  return '<div class="scen'+(sc.reviewed?" done":"")+(sc.open?" open":"")+
         '" data-sc="'+sc.id+'">'+
    '<div class="sc-head">'+
      '<i class="ti ti-chevron-right caret"></i>'+
      '<div class="sc-main">'+
        '<div class="sc-name">'+esc(sc.name)+'</div>'+
        '<div class="sc-meta">'+
          '<span class="sc-count">'+edStepCount(sc)+' steps</span>'+
          (c.needs ? '<span class="sc-need">· '+c.needs+
                     (c.needs===1?' needs':' need')+' you</span>' : '')+
          (sc.screen ? '<span class="sc-count">· '+esc(sc.screen)+'</span>' : '')+
        '</div>'+
      '</div>'+
      '<button class="okbtn" type="button" data-ok="'+sc.id+'">Looks right</button>'+
    '</div>'+
    '<div class="steps'+(sc.open?"":" hidden")+'">'+
      (pre.length ? edPre(sc, pre) : '')+
      edGroups(sc)+
      '<div class="scfoot">'+
        '<button class="addstep" type="button">+ Add a step at the end</button>'+
        '<details class="scraw" data-sc="'+sc.id+'">'+
          '<summary>Show this scenario as text</summary>'+
          '<p class="rawnote">Just this scenario, written the way the tool stores it. '+
            'Change it here and choose <b>Use this text</b> to apply it to this scenario only.</p>'+
          '<textarea class="yamlbox scy" spellcheck="false"></textarea>'+
          '<button class="abtn scuse" type="button">Use this text</button>'+
        '</details>'+
      '</div>'+
    '</div>'+
  '</div>';
}

/* Steps only — setup, permissions and notes are not steps and must not inflate
   the number the tester is asked to work through. */
function edStepCount(sc){
  return sc.steps.filter(function(s){
    return s.action!=="todo" || (s.kind||"step")==="step"; }).length;
}

function edPre(sc, items){
  return '<details class="pre"><summary>Before this test runs'+
    '<span class="pre-n">'+items.length+' item'+(items.length===1?'':'s')+'</span></summary>'+
    items.map(function(s){
      return '<div class="pre-row" data-sc="'+sc.id+'" data-st="'+s.id+'">'+
        '<span class="pre-k">'+(s.kind==="setup_db"?"Setup":"Permission")+'</span>'+
        '<span class="pre-t">'+esc(s.target)+'</span>'+
        '<button class="linkbtn" data-promote="'+s.id+'">It\u2019s a step</button>'+
      '</div>';
    }).join("")+'</details>';
}

/* One line of the tester's sheet = one block. Its `kind` decides everything, so
   "kept as a note" and "nothing came from this line" can never both appear —
   they are the same field. Amber means exactly one thing on this page: we asked
   a question and it is not answered yet. Nothing else may be coloured. */
var LINE_COPY = {
  setup:   "Database setup — run this yourself before the test. We don’t touch the database.",
  note:    "Kept as a note. This reads like a condition, not something to do.",
  heading: "This reads like a heading, so there’s nothing to click.",
  left_out:"Left out. The test ignores this line."
};

function edGroups(sc){
  var byLine = {};
  sc.steps.forEach(function(s){ (byLine[s.src] = byLine[s.src] || []).push(s); });
  var html = "", n = 0, qn = 0;
  var lines = sc.lines || [];
  var qTotal = lines.filter(function(l){ return l.kind === "question" && l.answer == null; }).length;

  lines.forEach(function(ln){
    if (ln.kind === "grouped") return;            // folded into the question above
    var mine = (byLine[ln.i] || []).filter(function(s){
      return !(s.action === "todo" && s.kind && s.kind !== "step"); });
    var body = "";

    if (ln.kind === "question" && ln.answer == null){
      qn++;
      body = edQuestion(sc, ln, qn, qTotal, mine.length);
    } else if (ln.kind === "question"){
      body = '<div class="lstat">' + esc(edAnswerText(ln)) +
             ' <button class="linkbtn" data-reopen="' + ln.i + '" data-sc="' + sc.id + '">Change</button></div>';
    } else if (mine.length){
      body = mine.map(function(s){ return edRow(sc, s, n++); }).join("");
    } else if (LINE_COPY[ln.kind]){
      body = '<div class="lstat">' + esc(LINE_COPY[ln.kind]) + '</div>';
    }

    html += '<div class="ln' + (ln.kind === "question" && ln.answer == null ? " ask" : "") +
            '" data-line="' + ln.i + '">' +
      '<div class="ln-src">' + esc(ln.text) + '</div>' + body + '</div>';
  });
  /* A step with no source line — added by hand, or applied from edited text —
     belongs to no sentence in the sheet. Rendering only what maps to a line
     made these invisible, which is worse than ugly: they still run. */
  var orphan = sc.steps.filter(function(s){
    var known = (sc.lines || []).some(function(l){ return l.i === s.src; });
    return !known && !(s.action === "todo" && s.kind && s.kind !== "step");
  });
  if (orphan.length){
    /* Pasted or described tests have no sheet at all, so there is nothing for a
       step to be "not in". Only call a step out as unattached when the scenario
       actually has source sentences to be unattached from. */
    var hasSheet = (sc.lines || []).length > 0;
    html += '<div class="ln'+(hasSheet ? " ln-added" : " ln-plain")+'">'+
      (hasSheet ? '<div class="ln-src added">' : '<div class="ln-src added" hidden>')+
        (orphan.some(function(s){ return s.origin === "hand"; })
          ? "Added by hand — not from a line in your sheet"
          : "Added by the tool — not in your sheet")+'</div>'+
      orphan.map(function(s){ return edRow(sc, s, n++); }).join("")+
    '</div>';
  }
  return html || '<div class="lstat">No steps yet.</div>';
}

/* Naming each option and labelling its own box is how a tester who has never
   seen an automation tool knows what is wanted. "Target" and "value" mean
   nothing to her; "the button or link labelled" does. */
function edTellEditor(sc, ln, i, total){
  return '<div class="qcard2">'+
    '<div class="qn">Question '+i+' of '+total+' · needs your answer</div>'+
    '<div class="qt">What should the test do here?</div>'+
    '<div class="tellopts" data-line="'+ln.i+'" data-sc="'+sc.id+'">'+
      edTellOpt("click",  "Click something",
                "Click the button or link labelled", ["target"]) +
      edTellOpt("fill",   "Type something",
                "Type", ["value","into the box labelled","target"]) +
      edTellOpt("assert_visible", "Check something is on screen",
                "Check this text is on screen", ["target"]) +
      '<label class="tellrow"><input type="radio" name="tell'+ln.i+'" value="none">'+
        '<span class="tellname">Nothing — leave this line out</span></label>'+
    '</div>'+
    '<div class="tellhelp">Write it exactly as it appears on the screen — '+
      'for example <b>Set Enterprise</b>.</div>'+
    '<div class="qbtns">'+
      '<button class="qb yes" data-tellsave="'+ln.i+'" data-sc="'+sc.id+'">Save</button>'+
      '<button class="qb no" data-tellcancel="'+ln.i+'" data-sc="'+sc.id+'">Cancel</button>'+
    '</div>'+
  '</div>';
}

function edTellOpt(action, name, lead, fields){
  var inputs = fields.map(function(f){
    return (f === "target" || f === "value")
      ? '<input class="tellin" data-f="'+f+'" placeholder="'+
        (f === "value" ? "what to type" : "what it says on screen")+'">'
      : '<span class="tellword">'+esc(f)+'</span>';
  }).join(" ");
  return '<label class="tellrow"><input type="radio" name="tellopt" value="'+action+'">'+
    '<span class="tellname">'+esc(name)+'</span>'+
    '<span class="tellform"><span class="tellword">'+esc(lead)+'</span> '+inputs+'</span>'+
  '</label>';
}

function edAnswerText(ln){
  if (ln.answer === "check") return "The test will check this text is on screen.";
  if (ln.answer === "told")  return "Done — you told us what to do here.";
  if (ln.answer === "note")  return "Kept as a note. The test won’t click anything here.";
  return "Answered.";
}

/* Every question has the same shape: what we think, then two plain answers.
   "No" is always safe — it means the test does nothing here — so a tester who
   cannot judge the technical reading can still answer truthfully. */
function edQuestion(sc, ln, i, total, stepCount){
  var q = {}, k = (ln.group ? ln.group.length + 1 : 1);
  if (ln.qtype === "expected-result"){
    q.text = stepCount
      ? "This sounds like what you expect to see, not something to do. We turned it into " +
        stepCount + (stepCount === 1 ? " click." : " clicks.")
      : "This sounds like what you expect to see. Should the test check it’s on screen?";
    q.yes  = stepCount ? "Check for it instead" : "Yes, check for it";
    q.no   = stepCount ? "Keep the " + stepCount + (stepCount === 1 ? " click" : " clicks")
                       : "No, keep it as a note";
    q.hint = "Not sure? Choose “" + q.yes + "” — that’s what it usually means.";
  } else if (ln.qtype === "settings-bullets"){
    q.text = k > 1
      ? "These " + k + " lines look like settings the test should check are on screen."
      : "This looks like a setting the test should check is on screen.";
    q.yes = k > 1 ? "Yes, check all " + k : "Yes, check for it";
    q.no  = k > 1 ? "No, keep all " + k + " as notes" : "No, keep it as a note";
  } else {
    q.text = "We couldn’t work out what the test should do with this line.";
    q.yes  = "Tell us what to do";
    q.no   = "Nothing to do here";
    q.tell = true;      // the yes branch opens an editor, it does not guess
  }
  if (ln.editing) return edTellEditor(sc, ln, i, total);
  var extra = (ln.group || []).map(function(gi){
    var g = (sc.lines || []).find(function(x){ return x.i === gi; });
    return g ? '<div class="ln-extra">' + esc(g.text) + '</div>' : '';
  }).join("");
  return '<div class="qcard2">' +
    '<div class="qn">Question ' + i + ' of ' + total + ' · needs your answer</div>' +
    extra +
    '<div class="qt">' + esc(q.text) + '</div>' +
    '<div class="qbtns">' +
      '<button class="qb yes" data-answer="check" data-line="' + ln.i + '" data-sc="' + sc.id + '">' + esc(q.yes) + '</button>' +
      '<button class="qb no"  data-answer="note"  data-line="' + ln.i + '" data-sc="' + sc.id + '">' + esc(q.no) + '</button>' +
    '</div>' +
    (q.hint ? '<div class="qhint">' + esc(q.hint) + '</div>' : '') +
  '</div>';
}
/* Replace a single scenario's DOM.

   Every action used to call edRender(), which rewrites #list wholesale. That
   threw away the open/closed state of every other card — state that lived only
   in the DOM — so answering one question collapsed the whole page and dropped
   focus. Scoped changes now repaint only the card they touched, and focus is
   put back on the equivalent element afterwards. */
function edCardRefresh(sc){
  var old = document.querySelector('.scen[data-sc="'+sc.id+'"]');
  if (!old) { edRender(); return; }
  var active = document.activeElement;
  var stId   = active && active.closest && active.closest(".srow")
             ? active.closest(".srow").dataset.st : null;
  var openDetails = [];
  old.querySelectorAll("details[open]").forEach(function(d){
    openDetails.push(d.className); });

  var tmp = document.createElement("div");
  tmp.innerHTML = edCard(sc);
  var fresh = tmp.firstElementChild;
  old.replaceWith(fresh);

  openDetails.forEach(function(cls){
    var d = fresh.querySelector("details." + cls.split(" ")[0]);
    if (d) d.open = true;
  });
  if (stId){
    var row = fresh.querySelector('.srow[data-st="'+stId+'"]');
    if (row) row.focus({preventScroll:true});
  }
}

function edRender(){
  var q = ($("q").value || "").toLowerCase();
  var only = $("onlyneeds").checked;
  var vis = ED.scenarios.filter(function(sc){
    if (ED.filter === "need" && !sc.steps.some(function(s){
      return s.action === "todo" && (s.kind || "step") === "step"; })) return false;
    if (ED.filter === "pre" && !sc.steps.some(function(s){
      return s.kind === "setup_db" || s.kind === "setup_sec"; })) return false;
    if (ED.filter === "notes" && !sc.steps.some(function(s){
      return s.kind === "rule"; })) return false;
    if (only && !sc.steps.some(function(s){
      return s.action === "todo" && (s.kind || "step") === "step"; })) return false;
    if (!q) return true;
    if ((sc.name+" "+(sc.group||"")).toLowerCase().indexOf(q) >= 0) return true;
    return sc.steps.some(function(s){
      return (s.target+" "+s.value).toLowerCase().indexOf(q) >= 0; });
  });
  $("list").innerHTML = vis.length ? vis.map(edCard).join("")
                                   : '<div class="empty">Nothing matches that filter.</div>';
}

function edCounts(){
  var ready=0, questions=0, notes=0;
  ED.scenarios.forEach(function(sc){
    sc.steps.forEach(function(s){ if (s.action !== "todo") ready++; });
    (sc.lines || []).forEach(function(l){
      if (l.kind === "question" && l.answer == null) questions++;
      else if (l.kind === "note" || l.kind === "heading" || l.kind === "setup") notes++;
    });
  });
  ED.questions = questions;

  $("verdicttxt").textContent = "Check the test we built from your test case";
  $("verdictsub").textContent = questions
    ? ready + " steps ready · " + questions + " question" + (questions===1?"":"s") +
      " for you · " + notes + " lines kept as notes"
    : ready + " steps ready · nothing needs you here · " + notes + " lines kept as notes";

  /* A count of questions is a promise the tester can verify. A percentage of a
     denominator she does not understand is noise, so there is no progress bar. */
  $("chips").innerHTML = questions
    ? '<button type="button" class="nextq" id="nextq">Answer the next question</button>'
    : '<span class="alldone">Nothing left to answer.</span>';
  edFileYaml();
  saveLocal();
}

function edChipBtn(key, n, label, cls){
  if (!n) return "";
  return '<button type="button" class="chip '+cls+(ED.filter===key?" on":"")+
         '" data-filter="'+key+'">'+esc(label)+'</button>';
}

/* The whole test file, rebuilt from the edited scenarios.

   Deliberately NOT two-way bound. A live binding means a stray paste silently
   discards every structured edit with no undo and no feedback until the run —
   so the box shows what will run, and "Use this instead" is the only way text
   typed here gets back into the model. */
var edYamlT;
function edFileYaml(){
  clearTimeout(edYamlT);
  edYamlT = setTimeout(function(){
    fetch("/api/structured-to-yaml", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({scenarios: ED.scenarios})
    })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || d.error) return;
      var box = $("filey");
      if (box) box.value = d.yaml || "";
      var steps = ED.scenarios.reduce(function(n, sc){
        return n + sc.steps.filter(function(s){ return s.action !== "todo"; }).length; }, 0);
      var sub = $("filesub");
      if (sub) sub.textContent = d.n_tests + " scenario" + (d.n_tests === 1 ? "" : "s") +
        " \u00b7 " + steps + " steps \u00b7 this is what will run";
      showEl($("filebar"), true);
    })
    .catch(function(){});
  }, 400);
}

function edFind(scId, stId){
  var sc = ED.scenarios.find(function(x){ return x.id === scId; });
  return [sc, sc && sc.steps.find(function(x){ return x.id === stId; })];
}

/* Card header toggle, delete and end-of-scenario add. Everything that touches a
   step now re-renders from the model rather than patching DOM in place — the
   rows are grouped by prose line, so a surgical patch would have to know which
   group a row belongs to for no benefit at this list size. */
document.addEventListener("click", function(e){
  if (!e.target.closest("#list")) return;

  var head = e.target.closest(".sc-head");
  if (head && !e.target.closest("[data-ok]")){
    var card = head.closest(".scen");
    var scT  = ED.scenarios.find(function(x){ return x.id === card.dataset.sc; });
    if (scT) scT.open = !scT.open;
    card.classList.toggle("open");
    card.querySelector(".steps").classList.toggle("hidden");
    return;
  }

  var menu = e.target.closest(".smenu");
  if (menu){
    var r = menu.closest(".srow");
    var p = edFind(r.dataset.sc, r.dataset.st);
    if (p[1]){ p[1].skipped = !p[1].skipped; p[1].edited = true; edCardRefresh(p[0]); edCounts(); }
    return;
  }

  var add = e.target.closest(".addstep");
  if (add){
    var c2 = add.closest(".scen");
    var sc = ED.scenarios.find(function(x){ return x.id === c2.dataset.sc; });
    if (!sc) return;
    sc.steps.push({id: sc.id+"_n"+Date.now(), action:"click", target:"", value:"",
                   src: (sc.lines && sc.lines.length ? sc.lines[sc.lines.length-1].i : -1),
                   origin:"tool", edited:true});
    edCardRefresh(sc); edCounts();
  }
});

/* Click a chip to edit just that chip, in place. The rest of the sentence stays
   text, so the tester's eye is never asked to scan a grid of live controls. */
document.addEventListener("click", function(e){
  if (!e.target.closest("#list")) return;

  var chip = e.target.closest(".chip-s, .chip-v");
  if (chip){
    var row = chip.closest(".srow");
    var pair = edFind(row.dataset.sc, row.dataset.st);
    if (!pair[1]) return;
    edEditChip(chip, pair[0], pair[1]);
    return;
  }

  var add = e.target.closest("[data-addhere]");
  if (add){
    var sc = ED.scenarios.find(function(x){ return x.id === add.dataset.sc; });
    if (!sc) return;
    sc.steps.push({id: sc.id+"_n"+Date.now(), action:"click", target:"", value:"",
                   src: parseInt(add.dataset.addhere,10), origin:"tool", edited:true});
    edCardRefresh(sc); edCounts();
    return;
  }

  var promote = e.target.closest("[data-promote]");
  if (promote){
    var r2 = promote.closest(".pre-row");
    var p2 = edFind(r2.dataset.sc, r2.dataset.st);
    if (p2[1]){ p2[1].kind = "step"; p2[1].edited = true; edCardRefresh(p2[0]); edCounts(); }
    return;
  }

  var ans = e.target.closest("[data-answer]");
  if (ans){
    var sca = ED.scenarios.find(function(x){ return x.id === ans.dataset.sc; });
    if (sca){
      var li = parseInt(ans.dataset.line, 10);
      var line = sca.lines.find(function(x){ return x.i === li; });
      if (line){
        line.answer = ans.dataset.answer;
        /* "No" is the safe answer: the line becomes a note and the test does
           nothing there. "Yes" turns it into an on-screen check. */
        var ids = [li].concat(line.group || []);
        sca.steps = sca.steps.filter(function(s){ return ids.indexOf(s.src) < 0; });
        if (ans.dataset.answer === "check"){
          ids.forEach(function(gi){
            var g = sca.lines.find(function(x){ return x.i === gi; }) || line;
            sca.steps.push({id: sca.id+"_q"+gi, action:"assert_visible",
                            target: g.text.replace(/^[-*•\s]+/, "").slice(0,80),
                            value:"", src: gi, origin:"tool", edited:true});
          });
        }
        sca.open = true;
        edCardRefresh(sca); edCounts();
      }
    }
    return;
  }

  var reopen = e.target.closest("[data-reopen]");
  if (reopen){
    var scr = ED.scenarios.find(function(x){ return x.id === reopen.dataset.sc; });
    if (scr){
      var lr = scr.lines.find(function(x){ return x.i === parseInt(reopen.dataset.line || reopen.dataset.reopen, 10); });
      if (lr){ lr.answer = null; edCardRefresh(scr); edCounts(); }
    }
    return;
  }

  var ok = e.target.closest("[data-ok]");
  if (ok){
    var s3 = ED.scenarios.find(function(x){ return x.id === ok.dataset.ok; });
    if (s3){ s3.reviewed = !s3.reviewed; edCardRefresh(s3); edCounts(); }
    return;
  }
});

function edEditChip(chip, sc, st){
  var slot = chip.dataset.slot;
  var cur  = slot === "action" ? st.action : (slot === "value" ? st.value : st.target);
  var el;
  if (slot === "action"){
    el = document.createElement("select");
    el.className = "chip-edit";
    el.innerHTML = ED_ACTIONS.map(function(a){
      return '<option value="'+a[0]+'"'+(a[0]===st.action?" selected":"")+">"+a[1]+"</option>";
    }).join("");
  } else {
    el = document.createElement("input");
    el.className = "chip-edit";
    el.value = cur || "";
    el.placeholder = ED_HOLE[slot] || "";
    el.size = Math.max(10, (cur || "").length + 2);
  }
  chip.replaceWith(el);
  el.focus();
  if (el.select) el.select();

  function commit(){
    var v = el.value;
    if (slot === "action") st.action = v;
    else if (slot === "value") st.value = v;
    else st.target = v;
    st.edited = true;
    /* Only this scenario changed. Rebuilding the whole list here is what
       collapsed every other card and threw focus to the top of the page in the
       middle of typing. */
    edCardRefresh(sc); edCounts();
    var row = document.querySelector('.srow[data-st="'+st.id+'"]');
    if (row) row.focus({preventScroll:true});
  }
  el.addEventListener("blur", commit);
  el.addEventListener("keydown", function(ev){
    if (ev.key === "Enter"){ ev.preventDefault(); el.blur(); }
    if (ev.key === "Escape"){
      el.removeEventListener("blur", commit);
      edCardRefresh(sc);
      var r = document.querySelector('.srow[data-st="'+st.id+'"]');
      if (r) r.focus({preventScroll:true});
    }
  });
  if (slot === "action") el.addEventListener("change", function(){ el.blur(); });
}

$("q").addEventListener("input", edRender);
$("onlyneeds").addEventListener("change", edRender);
$("expandall").addEventListener("click", function(){
  ED.scenarios.forEach(function(sc){ sc.open = true; });
  edRender();
});
$("collapseall").addEventListener("click", function(){
  ED.scenarios.forEach(function(sc){ sc.open = false; });
  edRender();
});


$("donebtn").addEventListener("click", function(){
  var btn = this, old = btn.textContent;
  btn.disabled = true; btn.textContent = "Saving\u2026";
  fetch("/api/structured-to-yaml", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({scenarios: ED.scenarios})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    /* Adopt the edited scenarios as the real test file, so the Run screen and
       the technical view both show what the tester just approved. */
    S.yaml = d.yaml || S.yaml;
    S.tests = d.n_tests || S.tests;
    S.dirty = false;
    $("techarea").value = S.yaml;
    saveLocal();
    btn.textContent = "Saved \u2713";
    toast(d.n_tests + " scenarios saved — ready to run");
    setTimeout(function(){ btn.disabled = false; btn.textContent = old; }, 2200);
  })
  .catch(function(){
    btn.disabled = false; btn.textContent = old;
    toast("Could not save the test file");
  });
});


document.addEventListener("click", function(e){
  var f = e.target.closest("[data-filter]");
  if (!f) return;
  ED.filter = (ED.filter === f.dataset.filter) ? null : f.dataset.filter;
  edRender(); edCounts();
});

/* n / p walk the steps that still need a human, opening whichever scenario
   holds the next one. This is the burn-down the spec asks for without a modal
   wizard that would be a second, worse copy of the list. */
document.addEventListener("keydown", function(e){
  if (e.key !== "n" && e.key !== "p") return;
  var t = e.target.tagName;
  if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
  var rows = Array.prototype.slice.call(document.querySelectorAll("#list .srow.needs"));
  if (!rows.length){
    document.querySelectorAll("#list .scen").forEach(function(c){
      c.classList.add("open"); c.querySelector(".steps").classList.remove("hidden"); });
    rows = Array.prototype.slice.call(document.querySelectorAll("#list .srow.needs"));
    if (!rows.length) return;
  }
  var cur = document.activeElement && document.activeElement.closest(".srow");
  var i = cur ? rows.indexOf(cur) : -1;
  var next = e.key === "n" ? rows[(i+1) % rows.length]
                           : rows[(i-1+rows.length) % rows.length];
  if (!next) return;
  var card = next.closest(".scen");
  card.classList.add("open");
  card.querySelector(".steps").classList.remove("hidden");
  next.focus();
  next.scrollIntoView({block:"center"});
  e.preventDefault();
});


document.addEventListener("click", function(e){
  if (!e.target.closest("#nextq")) return;
  var card = document.querySelector("#list .ln.ask");
  if (!card){
    document.querySelectorAll("#list .scen").forEach(function(c){
      c.classList.add("open"); c.querySelector(".steps").classList.remove("hidden"); });
    card = document.querySelector("#list .ln.ask");
  }
  if (!card) { window.scrollTo({top:0, behavior:"smooth"}); return; }
  var scen = card.closest(".scen");
  scen.classList.add("open");
  scen.querySelector(".steps").classList.remove("hidden");
  card.scrollIntoView({block:"center", behavior:"smooth"});
  var b = card.querySelector(".qb");
  if (b) b.focus();
});


/* Text typed into the raw box only reaches the model on an explicit press. */
document.addEventListener("click", function(e){
  if (!e.target.closest("#useraw")) return;
  var text = $("filey").value;
  fetch("/api/yaml-to-structured", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({yaml: text, screen: S.screen || "yourscreen"})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (!d || d.error){ toast(d && d.error ? d.error : "Could not read that text"); return; }
    ED.scenarios = d.scenarios || [];
    S.yaml = text;
    edRender(); edCounts();
    toast("Using the text you pasted");
  })
  .catch(function(){ toast("Could not read that text"); });
});

/* Per-scenario text view. Filled when opened rather than on every render — 13
   scenarios would otherwise mean 13 server round-trips on every keystroke. */
document.addEventListener("toggle", function(e){
  var d = e.target;
  if (!d.classList || !d.classList.contains("scraw") || !d.open) return;
  var sc = ED.scenarios.find(function(x){ return x.id === d.dataset.sc; });
  if (!sc) return;
  var box = d.querySelector(".scy");
  box.value = "Loading\u2026";
  fetch("/api/structured-to-yaml", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({scenarios: [sc]})
  })
  .then(function(r){ return r.json(); })
  .then(function(j){ box.value = j.yaml || ""; })
  .catch(function(){ box.value = "Could not build the text for this scenario."; });
}, true);

document.addEventListener("click", function(e){
  var btn = e.target.closest(".scuse");
  if (!btn) return;
  var d  = btn.closest(".scraw");
  var sc = ED.scenarios.find(function(x){ return x.id === d.dataset.sc; });
  if (!sc) return;
  fetch("/api/yaml-to-structured", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({yaml: d.querySelector(".scy").value, screen: sc.screen})
  })
  .then(function(r){ return r.json(); })
  .then(function(j){
    if (!j || j.error || !(j.scenarios || []).length){
      toast(j && j.error ? j.error : "Could not read that text"); return;
    }
    /* Text has no record of which sentence each step came from, so the sheet's
       lines are kept and the new steps are shown as added by hand rather than
       pretending to a provenance they no longer have. */
    var incoming = [];
    j.scenarios.forEach(function(x){ incoming = incoming.concat(x.steps || []); });
    sc.steps = incoming.map(function(s, i){
      return {id: sc.id + "_h" + i, action: s.action, target: s.target,
              value: s.value, src: -1, origin: "hand", edited: true};
    });
    edCardRefresh(sc); edCounts();
    toast("This scenario now uses the text you edited");
  })
  .catch(function(){ toast("Could not read that text"); });
});

/* Open / cancel / save the "tell us what to do" editor. */
document.addEventListener("click", function(e){
  var open = e.target.closest("[data-tell]");
  if (open){
    var sc = ED.scenarios.find(function(x){ return x.id === open.dataset.sc; });
    var ln = sc && sc.lines.find(function(x){ return x.i === parseInt(open.dataset.line,10); });
    if (ln){ ln.editing = true; edCardRefresh(sc); }
    return;
  }
  var cancel = e.target.closest("[data-tellcancel]");
  if (cancel){
    var sc2 = ED.scenarios.find(function(x){ return x.id === cancel.dataset.sc; });
    var ln2 = sc2 && sc2.lines.find(function(x){ return x.i === parseInt(cancel.dataset.tellcancel,10); });
    if (ln2){ ln2.editing = false; edCardRefresh(sc2); }
    return;
  }
  var save = e.target.closest("[data-tellsave]");
  if (!save) return;
  var sc3 = ED.scenarios.find(function(x){ return x.id === save.dataset.sc; });
  var li  = parseInt(save.dataset.tellsave, 10);
  var ln3 = sc3 && sc3.lines.find(function(x){ return x.i === li; });
  if (!ln3) return;
  var box = save.closest(".qcard2").querySelector(".tellopts");
  var picked = box.querySelector('input[type=radio]:checked');
  if (!picked){ toast("Choose one of the options first"); return; }

  sc3.steps = sc3.steps.filter(function(s){ return s.src !== li; });
  if (picked.value !== "none"){
    var row = picked.closest(".tellrow");
    var vals = {};
    row.querySelectorAll(".tellin").forEach(function(inp){ vals[inp.dataset.f] = inp.value.trim(); });
    if (!vals.target){ toast("Fill in what it says on screen"); return; }
    sc3.steps.push({id: sc3.id + "_t" + li, action: picked.value,
                    target: vals.target, value: vals.value || "",
                    src: li, origin: "sheet", edited: true});
  }
  ln3.editing = false;
  ln3.answer = picked.value === "none" ? "note" : "told";
  edCardRefresh(sc3); edCounts();
});
})();
