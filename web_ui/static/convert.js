/* ==========================================================================
   Stratus QA — Convert Test Cases
   Wires the screen to the real backend:
     POST /api/import-testcases    upload .xlsx  -> YAML + counts
     POST /api/nl-to-yaml          plain English -> YAML
     POST /api/modify-testcases    plain-English fix applied to the YAML
     GET  /api/profiles            saved server connections
     GET  /api/llm-status          is Gemini configured
     POST /api/scenarios           save to library
     POST /api/run                 execute the generated test
   ========================================================================== */
(function () {
"use strict";

// ------------------------------------------------------------------ state
var S = {
  source: "upload",      // upload | paste | describe
  file: null,
  yaml: "",
  screen: "",
  counts: {total: 0, translated: 0, tests: 0},
  steps: [],             // parsed for display
  todos: [],             // [{idx, text, state:'open'|'fixed'|'skipped'}]
  mode: "safe",
  useAI: true,
  profiles: [],
  yamlBackup: null,
  converted: false
};

// ------------------------------------------------------------------ tiny helpers
function $(id){ return document.getElementById(id); }
function qs(sel, root){ return (root||document).querySelector(sel); }
function qsa(sel, root){ return Array.prototype.slice.call((root||document).querySelectorAll(sel)); }
function show(el, yes){ if(el) el.classList.toggle("hidden", !yes); }

var toastTimer;
function toast(msg){
  var t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function(){ t.classList.remove("show"); }, 3200);
}

function banner(kind, html){
  $("banner-slot").innerHTML = '<div class="banner ' + kind + '">' + html + '</div>';
}
function clearBanner(){ $("banner-slot").innerHTML = ""; }

function esc(s){
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

function api(url, opts){
  opts = opts || {};
  opts.credentials = "same-origin";
  return fetch(url, opts).then(function (r) {
    if (r.status === 401) {
      banner("err", "<b>You are signed out.</b> Please <a href=\"/\">sign in</a> and come back to this page.");
      throw new Error("login required");
    }
    return r.json().catch(function(){ throw new Error("Server returned an unreadable response"); })
      .then(function (j) {
        if (!r.ok || j.error) throw new Error(j.error || ("Request failed (" + r.status + ")"));
        return j;
      });
  });
}

// ------------------------------------------------------------------ YAML (display only)
/* The backend emits yaml.safe_dump(default_flow_style=False), i.e. block style:
     tests:
     - screen: customerlist
       name: 'Edit Color'
       steps:
       - action: click
         target: New
   We parse it purely to DISPLAY steps + find `todo` ones. The YAML string
   returned by the server always stays the source of truth.                */
function unquote(v){
  v = (v == null ? "" : String(v)).trim();
  if (v.length > 1 && ((v[0] === "'" && v.slice(-1) === "'") || (v[0] === '"' && v.slice(-1) === '"'))) {
    v = v.slice(1, -1).replace(/''/g, "'");
  }
  return v;
}

function parseYaml(text){
  var steps = [], screen = "", cur = null;
  var stepsIndent = -1;                       // indent of the `steps:` key
  var lines = String(text || "").split(/\r?\n/);

  function indentOf(l){ return l.length - l.replace(/^\s+/, "").length; }
  function push(){ if (cur && Object.keys(cur).length) steps.push(cur); cur = null; }

  for (var i = 0; i < lines.length; i++) {
    var raw = lines[i];
    if (!raw.trim() || /^\s*#/.test(raw)) continue;
    var ind = indentOf(raw);

    // Inside a steps: block? In YAML the "-" items may sit at the SAME indent
    // as the `steps:` key, so only a non-item line at that indent (or anything
    // less indented, e.g. the next `- screen:`) ends the block.
    if (stepsIndent >= 0) {
      var isItem = /^\s*-\s/.test(raw);
      if (ind < stepsIndent || (ind === stepsIndent && !isItem)) { push(); stepsIndent = -1; }
    }

    if (stepsIndent < 0) {
      var mScreen = raw.match(/^\s*-?\s*screen:\s*(.+)$/);
      if (mScreen) { if (!screen) screen = unquote(mScreen[1]); continue; }
      var mSteps = raw.match(/^(\s*)steps:\s*$/);
      if (mSteps) { stepsIndent = mSteps[1].length; continue; }
      continue;                                // any other top-level key
    }

    // ---- inside the steps block ----
    var mItem = raw.match(/^\s*-\s*(\w+):\s*(.*)$/);   // "- action: click"
    if (mItem) { push(); cur = {}; cur[mItem[1]] = unquote(mItem[2]); continue; }

    var mKey = raw.match(/^\s*(\w+):\s*(.*)$/);         // "  target: New"
    if (mKey && cur) { cur[mKey[1]] = unquote(mKey[2]); continue; }
  }
  push();
  return {steps: steps, screen: screen};
}

/* Human sentence for a step, so a non-technical tester can proofread. */
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

/* A specific, plain-English question for an unclear step. */
function questionFor(text){
  var t = (text || "").toLowerCase();
  if (/\bdb\b|database|sql|table/.test(t))
    return "Should we check the database for this step, or is checking the screen enough?";
  if (/log ?in|login|sign ?in/.test(t))
    return "The tool signs in for you automatically — should we drop this step?";
  if (/not visible|should not|shouldn't/.test(t))
    return "What exactly should NOT be on the screen here?";
  if (/select|choose|dropdown|drop-down/.test(t))
    return "Which screen is this dropdown on, and what should we choose?";
  if (/verify|check|confirm|expect/.test(t))
    return "What should we look at on screen to confirm this worked?";
  return "We couldn't work this step out. Which screen and which box does it mean?";
}

// ------------------------------------------------------------------ stepper
function setStep(n){
  qsa("#stepper .st").forEach(function (el) {
    var s = Number(el.dataset.step);
    el.classList.toggle("on", s === n);
    el.classList.toggle("done", s < n);
    el.querySelector(".n").textContent = (s < n) ? "✓" : s;
  });
}

// ------------------------------------------------------------------ source picker
qsa(".src").forEach(function (card) {
  card.addEventListener("click", function () {
    S.source = card.dataset.src;
    qsa(".src").forEach(function (c) { c.classList.toggle("on", c === card); });
    show($("pane-upload"),   S.source === "upload");
    show($("pane-paste"),    S.source === "paste");
    show($("pane-describe"), S.source === "describe");
    refreshConvertBtn();
  });
});

// ------------------------------------------------------------------ file
$("browse").addEventListener("click", function (e) { e.preventDefault(); $("file").click(); });
$("drop").addEventListener("click", function () { $("file").click(); });
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
$("fremove").addEventListener("click", function () {
  S.file = null; $("file").value = ""; show($("filerow"), false); refreshConvertBtn(); setStep(1);
});

function takeFile(f){
  if (!/\.xlsx$/i.test(f.name)) { toast("Please choose an Excel .xlsx file"); return; }
  S.file = f;
  $("fname").textContent = f.name;
  $("fmeta").textContent = (f.size/1024).toFixed(0) + " KB · ready to convert";
  show($("filerow"), true);

  var base = f.name.replace(/\.xlsx$/i, "");
  if (!$("testname").value) $("testname").value = base;
  var tk = base.match(/STRAT[-_ ]?\d{4,6}/i);
  if (tk && !$("ticket").value) $("ticket").value = tk[0].toUpperCase().replace(/[_ ]/g, "-");

  refreshConvertBtn(); setStep(2);
  toast("File added — settings filled in for you");
}

// ------------------------------------------------------------------ settings
$("paste").addEventListener("input", function(){ refreshConvertBtn(); if(this.value.trim()) setStep(2); });
$("describe").addEventListener("input", function(){ refreshConvertBtn(); if(this.value.trim()) setStep(2); });

qsa("#mode button").forEach(function (b) {
  b.addEventListener("click", function () {
    S.mode = b.dataset.mode;
    qsa("#mode button").forEach(function (x) { x.classList.toggle("on", x === b); });
    $("modehint").textContent = (S.mode === "safe")
      ? "Practice run is selected for you. It clicks through the screens without saving anything, so it's always safe to try."
      : "Full run will create and edit practice data on the test server. The live store is still never touched.";
  });
});

qsa("#ai button").forEach(function (b) {
  b.addEventListener("click", function () {
    S.useAI = b.dataset.ai === "1";
    qsa("#ai button").forEach(function (x) { x.classList.toggle("on", x === b); });
  });
});

function refreshConvertBtn(){
  var ok = (S.source === "upload"   && !!S.file)
        || (S.source === "paste"    && $("paste").value.trim().length > 10)
        || (S.source === "describe" && $("describe").value.trim().length > 10);
  $("convert").disabled = !ok;
}

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
      sel.innerHTML = '<option value="">No saved server — add one on the Run page</option>';
      return;
    }
    S.profiles.forEach(function (p, i) {
      var o = document.createElement("option");
      o.value = String(i);
      o.textContent = p.name + (p.url ? "  ·  " + p.url : "");
      sel.appendChild(o);
    });
  }).catch(function(){
    $("server").innerHTML = '<option value="">Could not load servers</option>';
  });

  api("/api/llm-status").then(function (j) {
    var on = j && (j.available || j.configured || j.enabled);
    $("aihint").textContent = on
      ? "AI is configured. It only steps in for steps the rules can't work out."
      : "No AI key configured — the tool will use its rule engine only. That still handles most steps.";
    if (!on) {
      S.useAI = false;
      qsa("#ai button").forEach(function (x) { x.classList.toggle("on", x.dataset.ai === "0"); });
    }
  }).catch(function(){ $("aihint").textContent = "AI status unknown — the rule engine will be used."; });
}

// ------------------------------------------------------------------ convert
var progTimers = [];
function progress(step, pct, text){
  ["p1","p2","p3","p4"].forEach(function (id, i) {
    var el = $(id), n = i + 1;
    el.className = "prow" + (n < step ? " done" : n === step ? " doing" : "");
    el.querySelector(".ic").textContent = n < step ? "✓" : n;
  });
  $("bar").style.width = pct + "%";
  if (text) $("ptxt").textContent = text;
}

$("convert").addEventListener("click", function () {
  clearBanner();
  show($("stage-input"), false);
  show($("stage-result"), false);
  show($("stage-progress"), true);
  setStep(3);

  progress(1, 12, "Reading your steps…");
  progTimers.forEach(clearTimeout);
  progTimers = [
    setTimeout(function(){ progress(2, 38, "Understanding what you wrote…"); }, 700),
    setTimeout(function(){ progress(3, 66, "Matching steps to Stratus screens…"); }, 1500),
    setTimeout(function(){ progress(4, 88, "Building your automatic test…"); }, 2400)
  ];

  var req;
  if (S.source === "upload") {
    var fd = new FormData();
    fd.append("file", S.file);
    fd.append("use_llm", S.useAI ? "1" : "0");
    req = api("/api/import-testcases", {method: "POST", body: fd});
  } else {
    var text = (S.source === "paste" ? $("paste").value : $("describe").value).trim();
    req = api("/api/nl-to-yaml", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({prompt: text, use_llm: S.useAI, safe_mode: S.mode === "safe"})
    });
  }

  req.then(function (j) {
    progTimers.forEach(clearTimeout);
    progress(5, 100, "Done!");
    setTimeout(function(){ renderResult(j); }, 350);
  }).catch(function (err) {
    progTimers.forEach(clearTimeout);
    show($("stage-progress"), false);
    show($("stage-input"), true);
    setStep(2);
    banner("err", "<b>We couldn't convert that.</b> " + esc(err.message) +
      "<br>Check the file is a normal Excel test-case sheet, or try the example file.");
  });
});

// ------------------------------------------------------------------ result
function renderResult(j){
  S.yaml = j.yaml || "";
  S.screen = j.screen || (j.screens && j.screens[0]) || "";
  S.converted = true;

  var parsed = parseYaml(S.yaml);
  S.steps = parsed.steps;
  if (!S.screen) S.screen = parsed.screen;

  var total = j.n_steps_total || S.steps.length;
  var translated = (typeof j.n_steps_translated === "number")
    ? j.n_steps_translated
    : S.steps.filter(function (s) { return (s.action || "") !== "todo"; }).length;

  S.counts = {total: total, translated: translated, tests: j.n_tests || 1};

  S.todos = [];
  S.steps.forEach(function (s, i) {
    if ((s.action || "") === "todo") S.todos.push({idx: i, text: s.target || "", state: "open"});
  });

  show($("stage-progress"), false);
  show($("stage-result"), true);
  paintVerdict();
  paintQuestions();
  paintSteps();
  $("code").value = S.yaml;
  toast("Converted — " + translated + " of " + total + " steps ready");
}

function paintVerdict(){
  var open = S.todos.filter(function (t) { return t.state === "open"; }).length;
  var skipped = S.todos.filter(function (t) { return t.state === "skipped"; }).length;
  var ready = S.counts.total - open - skipped;

  $("verdict").classList.toggle("warn", open > 0);
  $("vmark").textContent = open > 0 ? "?" : "✓";

  if (open > 0) {
    $("vtitle").textContent = "Done! We understood " + ready + " of your " + S.counts.total + " steps.";
    $("vsub").textContent = open + (open === 1 ? " step needs" : " steps need") + " a quick answer from you — see below.";
  } else if (skipped > 0) {
    $("vtitle").textContent = "Ready — " + ready + " steps will run.";
    $("vsub").textContent = skipped + " step" + (skipped === 1 ? "" : "s") + " skipped by you; they'll be listed in the report.";
  } else {
    $("vtitle").textContent = "Perfect — all " + S.counts.total + " steps are ready to run!";
    $("vsub").textContent = "Press “Run the test now” whenever you like.";
  }

  var chips = '<span class="chip ok">✓ ' + ready + " ready</span>";
  if (open) chips += '<span class="chip warn">? ' + open + " need you</span>";
  if (skipped) chips += '<span class="chip mut">' + skipped + " skipped</span>";
  if (S.counts.tests > 1) chips += '<span class="chip mut">' + S.counts.tests + " test cases</span>";
  $("vchips").innerHTML = chips;
  $("accLabel").textContent = "See the " + ready + " steps we understood";
}

function paintQuestions(){
  var open = S.todos.filter(function (t) { return t.state !== "hidden"; });
  show($("questions-wrap"), S.todos.length > 0);
  var nOpen = S.todos.filter(function (t) { return t.state === "open"; }).length;
  $("qhead").textContent = nOpen ? ("Help us with these " + nOpen + " steps") : "All questions answered";

  var host = $("questions");
  host.innerHTML = "";
  S.todos.forEach(function (t, i) {
    var d = document.createElement("div");
    d.className = "qcard" + (t.state === "fixed" ? " fixed" : t.state === "skipped" ? " skipped" : "");
    if (t.state === "open") {
      d.innerHTML =
        '<div class="said"><b>Your step said:</b> “' + esc(t.text) + '”</div>' +
        '<div class="ask">? ' + esc(questionFor(t.text)) + '</div>' +
        '<div class="row">' +
          '<input class="inp" placeholder="Answer in your own words, e.g. “it\'s the Brand dropdown on the product screen”">' +
          '<button class="btn primary sm">That fixes it</button>' +
        '</div>' +
        '<button class="skiplink">Skip for now — run the test without this step</button>';
      var input = qs("input", d), btn = qs("button.btn", d), skip = qs(".skiplink", d);
      btn.addEventListener("click", function(){ applyFix(i, input.value, btn); });
      input.addEventListener("keydown", function(e){ if (e.key === "Enter") btn.click(); });
      skip.addEventListener("click", function(){ t.state = "skipped"; paintQuestions(); paintVerdict(); });
    } else if (t.state === "fixed") {
      d.innerHTML = '<div class="said"><b>Your step said:</b> “' + esc(t.text) + '”</div>' +
                    '<div class="after">✓ Fixed — we updated the test from your answer</div>';
    } else {
      d.innerHTML = '<div class="said"><b>Your step said:</b> “' + esc(t.text) + '”</div>' +
                    '<div class="after">Skipped — the test will run without this step</div>';
    }
    host.appendChild(d);
  });
}

function applyFix(i, answer, btn){
  answer = (answer || "").trim();
  if (!answer) { toast("Type a quick answer first — your own words are fine"); return; }
  var old = btn.innerHTML;
  btn.innerHTML = '<span class="spin"></span> Applying';
  btn.disabled = true;

  var t = S.todos[i];
  var before = S.yaml;

  /* Send the tester's own words. The rule engine understands plain
     instructions ("add a screenshot after each click", "search for SMITH");
     anything more free-form needs the AI, which we report honestly below. */
  api("/api/modify-testcases", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      yaml: S.yaml, screen: S.screen, prompt: answer, use_llm: S.useAI
    })
  }).then(function (j) {
    var changed = (j.yaml || "") !== before && !!j.yaml;
    if (changed) {
      S.yaml = j.yaml;
      $("code").value = S.yaml;
      S.steps = parseYaml(S.yaml).steps;
    }

    var stillTodo = S.steps.some(function (s) {
      return (s.action || "") === "todo" && (s.target || "") === t.text;
    });

    if (!stillTodo) {
      t.state = "fixed";
      toast("Step fixed ✓ your answer was applied");
    } else if (changed) {
      t.state = "fixed";               // the tester's instruction did change the test
      toast("Applied your instruction — check the steps below");
    } else {
      var why = (j.llm_used === false && j.llm_error)
        ? j.llm_error
        : "we couldn't turn that into a test step automatically";
      toast("Not applied — " + why);
      banner("warn",
        "<b>“" + esc(answer) + "” wasn't applied.</b> The rule engine understands direct " +
        "instructions (e.g. <i>“add a screenshot after each click”</i>, <i>“search for SMITH instead”</i>). " +
        "For free-form answers, switch on AI assistance — that needs <code>GEMINI_API_KEY</code> in the " +
        "server's <code>.env</code>. You can also skip this step, or edit the file directly below.");
      btn.innerHTML = old; btn.disabled = false;
    }
    paintQuestions(); paintVerdict(); paintSteps();
  }).catch(function (err) {
    btn.innerHTML = old; btn.disabled = false;
    toast("Could not apply that: " + err.message);
  });
}

function paintSteps(){
  var host = $("accbody");
  host.innerHTML = "";
  S.steps.forEach(function (s, i) {
    if ((s.action || "") === "todo") return;
    var r = document.createElement("div");
    r.className = "steprow";
    r.innerHTML = '<span class="ix">' + (i + 1) + '</span><span class="tick">✓</span><span>' + humanise(s) + "</span>";
    host.appendChild(r);
  });
  if (!host.children.length) {
    host.innerHTML = '<div class="steprow"><span>No steps were understood yet — answer the questions above.</span></div>';
  }
}

$("acc").addEventListener("click", function () {
  var open = !$("accbody").classList.contains("hidden");
  show($("accbody"), open ? false : true);
});

// ------------------------------------------------------------------ code panel
$("codetoggle").addEventListener("click", function () {
  var hidden = $("codewrap").classList.contains("hidden");
  show($("codewrap"), hidden);
  this.textContent = hidden ? "Hide the technical file" : "Show & edit the technical file (for developers)";
});
$("cedit").addEventListener("click", function () {
  S.yamlBackup = $("code").value;
  $("code").removeAttribute("readonly");
  $("code").focus();
  $("codestate").textContent = "Editing"; $("codestate").classList.add("edit");
  show($("cedit"), false); show($("csave"), true); show($("cundo"), true);
});
function endEdit(){
  $("code").setAttribute("readonly", "readonly");
  $("codestate").textContent = "View only"; $("codestate").classList.remove("edit");
  show($("cedit"), true); show($("csave"), false); show($("cundo"), false);
}
$("csave").addEventListener("click", function () {
  S.yaml = $("code").value;
  var parsed = parseYaml(S.yaml);
  S.steps = parsed.steps;
  S.todos = [];
  S.steps.forEach(function (s, i) {
    if ((s.action || "") === "todo") S.todos.push({idx: i, text: s.target || "", state: "open"});
  });
  S.counts.total = S.steps.length;
  S.counts.translated = S.steps.length - S.todos.length;
  endEdit(); paintVerdict(); paintQuestions(); paintSteps();
  toast("Changes saved — this file will be used when you press Run");
});
$("cundo").addEventListener("click", function () {
  if (S.yamlBackup != null) { $("code").value = S.yamlBackup; S.yaml = S.yamlBackup; }
  endEdit(); toast("Changes undone");
});

// ------------------------------------------------------------------ finish actions
$("download").addEventListener("click", function () {
  var name = ($("testname").value.trim() || "stratus-test").replace(/[^\w.-]+/g, "_") + ".yaml";
  var blob = new Blob([S.yaml], {type: "text/yaml"});
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  toast("Downloaded " + name);
});

$("save").addEventListener("click", function () {
  var btn = this, old = btn.textContent;
  var title = $("testname").value.trim() || "Untitled test";
  var id = ($("ticket").value.trim() || title).replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  if (!id) { toast("Give the test a name first"); return; }

  btn.textContent = "Saving…"; btn.disabled = true;
  api("/api/scenarios", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      id: id, title: title, yaml: S.yaml, screen: S.screen,
      description: $("ticket").value.trim() ? ("Ticket " + $("ticket").value.trim()) : "",
      tags: $("ticket").value.trim() ? [$("ticket").value.trim()] : [],
      overwrite: true
    })
  }).then(function () {
    toast("Saved to your library as “" + title + "”");
  }).catch(function (e) {
    toast("Could not save: " + e.message);
  }).then(function(){ btn.textContent = old; btn.disabled = false; });
});

$("run").addEventListener("click", function () {
  var open = S.todos.filter(function (t) { return t.state === "open"; }).length;
  if (open && !confirm(open + " step(s) still need an answer. They will be skipped. Run anyway?")) return;

  var p = S.profiles[Number($("server").value)] || {};
  if (!p.url) { toast("Pick a server first (add one on the Run page)"); return; }

  var btn = this, old = btn.textContent;
  btn.textContent = "Starting…"; btn.disabled = true;

  api("/api/run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      url: p.url, user: p.user || "", password: p.password || "",
      machine_id: p.machine_id || "",
      screen: S.screen || "customer",
      single_mode: true,
      single_screenname: S.screen || "",
      single_safe: S.mode === "safe",
      read_only: S.mode === "safe",
      custom_tests_yaml: S.yaml
    })
  }).then(function () {
    toast("Test started — opening the live run view…");
    setTimeout(function(){ window.location.href = "/"; }, 900);
  }).catch(function (e) {
    btn.textContent = old; btn.disabled = false;
    banner("err", "<b>Could not start the run.</b> " + esc(e.message));
  });
});

$("startover").addEventListener("click", function () {
  if (!confirm("Start over? Your converted test will be cleared.")) return;
  S.file = null; S.yaml = ""; S.steps = []; S.todos = []; S.converted = false;
  $("file").value = ""; $("paste").value = ""; $("describe").value = "";
  $("testname").value = ""; $("ticket").value = "";
  show($("filerow"), false);
  show($("stage-result"), false); show($("stage-progress"), false); show($("stage-input"), true);
  clearBanner(); refreshConvertBtn(); setStep(1);
});

// stepper back-navigation (completed steps only)
qsa("#stepper .st").forEach(function (el) {
  el.addEventListener("click", function () {
    if (!el.classList.contains("done")) return;
    show($("stage-result"), false); show($("stage-progress"), false); show($("stage-input"), true);
    setStep(Number(el.dataset.step));
  });
});

boot();
})();
