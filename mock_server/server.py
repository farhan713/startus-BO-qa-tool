#!/usr/bin/env python3
"""Mock Stratus BackOffice server for the demo.

Serves the same URLs and the same selector IDs as the real Stratus
BackOffice — so the demo runs end-to-end against this stand-in when the
real app isn't available.

Endpoints:
  /backoffice/login.jsp                     ← login form
  /backoffice/UserAuthenticationServlet.do  ← auth handler
  /backoffice/stratus?screenType=CustomerList       ← Customer List
  /backoffice/stratus?screenType=CustomerEntryDtl   ← New Customer detail
  /backoffice/stratus?screenType=CustomerEditDtl    ← Edit Customer detail

Start with:
    python mock_server/server.py
    # listens on http://localhost:8080
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8080
HOST = "localhost"

# Simple in-memory store of customers
CUSTOMERS = [
    {"id": 1, "first": "JOHN",  "last": "SMITH",   "company": "ACME",   "club": "CL001"},
    {"id": 2, "first": "JANE",  "last": "SMITH",   "company": "WIDGET", "club": "CL002"},
    {"id": 3, "first": "ROBERT","last": "SMITH",   "company": "ACME",   "club": "CL003"},
    {"id": 4, "first": "MARY",  "last": "JONES",   "company": "INIT",   "club": "CL004"},
    {"id": 5, "first": "PETER", "last": "WILSON",  "company": "FOO",    "club": "CL005"},
]

LOGGED_IN_USERS = set()


# ============================================================ HTML templates

LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>CELERANT BACK OFFICE LOGIN</title>
<style>
  body { font-family: Arial, sans-serif; background: #f3f3f3; padding: 80px; }
  #form-container { max-width: 420px; margin: 0 auto; background: white;
                    padding: 32px; border: 1px solid #ddd; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
  h1 { color: #1F4E79; font-size: 18px; margin: 0 0 24px 0; }
  input[type=text], input[type=password] { width: 100%; padding: 8px; margin: 8px 0;
                                            border: 1px solid #bbb; box-sizing: border-box; }
  .subBtn { background: #1F4E79; color: white; border: 0; padding: 10px 24px; cursor: pointer; }
  .cancelBtn { background: #ddd; border: 0; padding: 10px 24px; margin-left: 8px; cursor: pointer; }
  #errMsg h1 { color: red; font-size: 14px; }
</style></head><body>
<div id="form-container">
  <form id="loginform" method="post" action="/backoffice/UserAuthenticationServlet.do">
    <h1>CELERANT BACK OFFICE LOGIN</h1>
    <div id="errMsg" style="display:{ERR_DISPLAY}"><h1>{ERR_MSG}</h1></div>
    <input type="text" name="userid" id="userid" placeholder="Username" class="loginInput">
    <input type="password" name="passwd" id="passwd" placeholder="Password" class="loginInput">
    <input type="hidden" name="apps" id="apps" value="WRMS">
    <input type="submit" value="OK" id="btnLogin" class="subBtn">
    <input type="button" value="Clear" id="btnCancel" class="cancelBtn">
  </form>
</div>
</body></html>"""

CUSTOMER_LIST_HTML = """<!DOCTYPE html>
<html><head><title>Customer List — Stratus BackOffice (MOCK)</title>
<style>
  body { font-family: Arial, sans-serif; padding: 16px; background: #fafafa; }
  .screen-title-header { color: #1F4E79; margin: 0 0 8px 0; }
  .topNav, .bottomNav { padding: 8px 0; }
  button, .btn { padding: 6px 14px; margin: 0 4px; cursor: pointer; border: 1px solid #1F4E79;
                  background: white; color: #1F4E79; border-radius: 3px; }
  .btn-danger { background: #d9534f; color: white; border-color: #d9534f; }
  .btn-link { background: transparent; border: 0; color: #1F4E79; }
  .dropdown-menu { display: none; position: absolute; background: white; border: 1px solid #ccc;
                    padding: 8px; box-shadow: 0 2px 6px rgba(0,0,0,.15); min-width: 160px; }
  .dropdown-menu.open { display: block; }
  .dropdown-item { display: block; width: 100%; text-align: left; border: 0; background: transparent;
                    padding: 6px 12px; cursor: pointer; }
  .dropdown-item:hover { background: #eef; }
  #cel-tabs { display: none; background: white; padding: 16px; margin: 8px 0; border: 1px solid #ddd; }
  #cel-tabs.open { display: block; }
  .form-group { display: inline-block; margin: 8px 16px 8px 0; }
  .form-control, .selectDrop { padding: 6px; border: 1px solid #bbb; }
  table.ui-jqgrid-btable { width: 100%; border-collapse: collapse; background: white;
                            margin-top: 8px; border: 1px solid #ccc; }
  table.ui-jqgrid-btable th, table.ui-jqgrid-btable td { padding: 8px; border: 1px solid #eee; text-align: left; }
  table.ui-jqgrid-btable th { background: #1F4E79; color: white; }
  tr.jqgrow { cursor: pointer; }
  tr.jqgrow:hover { background: #f0f6ff; }
  tr.jqgrow.selected { background: #d9e8ff; }
</style>
<script>
  function toggleCriteria(show) {
    var c = document.getElementById('cel-tabs');
    var s = document.getElementById('ShowCriteria');
    var h = document.getElementById('HideCriteria');
    if (show) { c.classList.add('open'); s.style.display='none'; h.style.display='inline-block'; }
    else      { c.classList.remove('open'); s.style.display='inline-block'; h.style.display='none'; }
  }
  function toggleAction() {
    document.getElementById('actionButtonMenu').classList.toggle('open');
  }
  function selectRow(el) {
    document.querySelectorAll('tr.jqgrow').forEach(function(r){r.classList.remove('selected');});
    el.classList.add('selected');
  }
  function search() {
    var lastName = document.getElementById('LastName').value.trim().toUpperCase();
    var rows = document.querySelectorAll('tr.jqgrow');
    rows.forEach(function(r) {
      var lname = r.getAttribute('data-last') || '';
      r.style.display = (!lastName || lname.indexOf(lastName) !== -1) ? '' : 'none';
    });
  }
  function reset_() {
    document.getElementById('LastName').value = '';
    document.getElementById('FirstName').value = '';
    document.getElementById('Email').value = '';
    document.querySelectorAll('tr.jqgrow').forEach(function(r){ r.style.display = ''; });
  }
  function go(url) { window.location.href = url; }
  document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('ShowCriteria').onclick = function(){ toggleCriteria(true); };
    document.getElementById('HideCriteria').onclick = function(){ toggleCriteria(false); };
    document.getElementById('Search').onclick   = function(){ search(); };
    document.getElementById('Reset').onclick    = function(){ reset_(); };
    document.getElementById('New').onclick      = function(){ go('/backoffice/stratus?screenType=CustomerEntryDtl'); };
    document.querySelector('.dropdown-toggle').onclick = function(e){ e.preventDefault(); toggleAction(); };
    document.getElementById('Edit').onclick     = function(){
      var sel = document.querySelector('tr.jqgrow.selected');
      var id = sel ? sel.getAttribute('data-id') : '1';
      go('/backoffice/stratus?screenType=CustomerEditDtl&CUSTOMER_ID=' + id);
    };
    document.getElementById('Delete').onclick   = function(){ alert('Delete confirmed (mock)'); };
    document.getElementById('PrintList').onclick = function(){ alert('Print List initiated (mock)'); };
    document.getElementById('Close').onclick    = function(){ go('/backoffice/'); };
  });
</script>
</head>
<body>
<h4 class="screen-title-header">Customers (MOCK STRATUS)</h4>
<hr>

<div class="topNav" id="topNav">
  <button type="button" id="ShowCriteria" class="btn btn-link">Show Search Criteria ▶</button>
  <button type="button" id="HideCriteria" class="btn btn-link" style="display:none">Hide Search Criteria ▼</button>
  <button type="button" id="New" class="btn">New</button>
  <div style="display:inline-block; position:relative;">
    <a href="#" class="btn dropdown-toggle action-btn">Action ▾</a>
    <ul id="actionButtonMenu" class="dropdown-menu">
      <li><button id="Edit" class="dropdown-item">Edit</button></li>
      <li><button id="Delete" class="dropdown-item">Delete</button></li>
      <li><button id="PrintList" class="dropdown-item">Print List</button></li>
    </ul>
  </div>
  <button type="button" id="Close" class="btn btn-danger">Close</button>
</div>

<div class="cel-tabs row" id="cel-tabs">
  <form id="componentFormId">
    <div class="form-group"><label>Last Name</label><br><input id="LastName" name="Last Name" class="form-control"></div>
    <div class="form-group"><label>First Name</label><br><input id="FirstName" name="First Name" class="form-control"></div>
    <div class="form-group"><label>Email</label><br><input id="Email" class="form-control"></div>
    <div class="form-group"><label>Company</label><br><input id="Company" class="form-control"></div>
    <div class="form-group"><label>Phone</label><br><input id="Phone" class="form-control"></div>
    <div class="form-group"><label>Club ID</label><br><input id="ClubID" class="form-control"></div>
    <div class="form-group"><label>Zip</label><br><input id="Zip" class="form-control"></div>
    <div class="form-group"><label>Middle Name</label><br><input id="MiddleName" class="form-control"></div>
    <div class="form-group"><label>Store</label><br>
      <select id="STORE" class="selectDrop"><option></option><option>001</option><option>002</option></select>
    </div>
  </form>
</div>

<table class="ui-jqgrid-btable">
  <thead><tr><th>ID</th><th>First</th><th>Last</th><th>Company</th><th>Club</th></tr></thead>
  <tbody>
    {ROWS}
  </tbody>
</table>

<div class="bottomNav" id="searchBtnNav">
  <button id="Search" type="button" class="btn">Search</button>
  <button id="Reset" type="reset" class="btn">Reset</button>
</div>

</body></html>"""

CUSTOMER_DETAIL_HTML = """<!DOCTYPE html>
<html><head><title>Customer Detail — Stratus BackOffice (MOCK)</title>
<style>
  body { font-family: Arial, sans-serif; padding: 16px; background: #fafafa; }
  h2 { color: #1F4E79; }
  fieldset { border: 1px solid #ddd; padding: 16px; margin-bottom: 16px; background: white; }
  legend { color: #1F4E79; font-weight: bold; }
  .form-group { display: inline-block; margin: 8px 16px 8px 0; min-width: 200px; }
  .form-control, .selectDrop { padding: 6px; border: 1px solid #bbb; width: 200px; }
  button { padding: 8px 24px; margin: 4px; border: 1px solid #1F4E79; background: white;
            color: #1F4E79; cursor: pointer; border-radius: 3px; }
  .save-btn { background: #1F4E79; color: white; }
  #errorMsg { color: red; margin: 8px 0; display: none; }
</style>
<script>
  function save() {
    var fn = document.getElementById('firstName').value.trim();
    var ln = document.getElementById('lastName').value.trim();
    var err = document.getElementById('errorMsg');
    if (!fn || !ln) {
      err.textContent = 'First and Last name are required';
      err.style.display = 'block';
      return;
    }
    // pretend to save, then return to list
    setTimeout(function(){ window.location.href = '/backoffice/stratus?screenType=CustomerList&saved=1'; }, 400);
  }
  function cancel() { window.location.href = '/backoffice/stratus?screenType=CustomerList'; }
  document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('Save').onclick = save;
    document.getElementById('Cancel').onclick = cancel;
  });
</script>
</head>
<body>
<h2>Customer Detail (MOCK STRATUS) — {MODE}</h2>

<form id="componentFormId">
  <fieldset>
    <legend>Customer Name</legend>
    <div class="form-group"><label>First Name</label><br><input id="firstName" name="First Name:" class="form-control" value="{FIRST}"></div>
    <div class="form-group"><label>Middle Name</label><br><input id="middleName" name="Middle Name:" class="form-control"></div>
    <div class="form-group"><label>Last Name</label><br><input id="lastName" name="Last Name:" class="form-control" value="{LAST}"></div>
    <div class="form-group"><label>Company</label><br><input id="company" name="Company" class="form-control" value="{COMPANY}"></div>
    <div class="form-group"><label>Club ID</label><br><input id="clubID" name="Club ID:" class="form-control" value="{CLUB}"></div>
    <div class="form-group"><label>Cust Type</label><br>
      <select id="custType" class="selectDrop"><option></option><option>Regular</option><option>VIP</option></select>
    </div>
  </fieldset>

  <div id="errorMsg" class="alert-danger"></div>

  <button type="button" id="Save" class="save-btn">Save</button>
  <button type="button" id="Cancel">Cancel</button>
</form>
</body></html>"""


# ============================================================ handler

class MockHandler(BaseHTTPRequestHandler):
    # Quieter logs — one line per request
    def log_message(self, fmt, *args):
        print(f"[mock] {self.command} {self.path}")

    # ----------------------------------------------------- helpers

    def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, target: str) -> None:
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def _customer_list_html(self) -> str:
        rows_html = ""
        for c in CUSTOMERS:
            rows_html += (
                f'<tr class="jqgrow" data-id="{c["id"]}" data-last="{c["last"]}" '
                f'onclick="selectRow(this)">'
                f'<td>{c["id"]}</td><td>{c["first"]}</td><td>{c["last"]}</td>'
                f'<td>{c["company"]}</td><td>{c["club"]}</td></tr>'
            )
        return CUSTOMER_LIST_HTML.replace("{ROWS}", rows_html)

    def _customer_detail_html(self, mode: str, cid: int | None = None) -> str:
        if cid:
            c = next((x for x in CUSTOMERS if x["id"] == cid), None)
        else:
            c = None
        return (CUSTOMER_DETAIL_HTML
                .replace("{MODE}", mode)
                .replace("{FIRST}",   c["first"]   if c else "")
                .replace("{LAST}",    c["last"]    if c else "")
                .replace("{COMPANY}", c["company"] if c else "")
                .replace("{CLUB}",    c["club"]    if c else ""))

    # ----------------------------------------------------- routing

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/backoffice/", "/backoffice"):
            return self._send(200, "<html><body><h2>Mock Stratus (main)</h2>"
                                   "<p><a href='/backoffice/login.jsp'>Login</a></p></body></html>")

        if u.path == "/backoffice/login.jsp":
            html = LOGIN_HTML.replace("{ERR_DISPLAY}", "none").replace("{ERR_MSG}", "")
            return self._send(200, html)

        if u.path == "/backoffice/stratus":
            screen = (q.get("screenType") or [""])[0]
            if screen == "CustomerList":
                return self._send(200, self._customer_list_html())
            if screen == "CustomerEntryDtl":
                return self._send(200, self._customer_detail_html("New"))
            if screen == "CustomerEditDtl":
                cid = int((q.get("CUSTOMER_ID") or ["1"])[0])
                return self._send(200, self._customer_detail_html("Edit", cid))
            return self._send(404, "<h1>Unknown screen</h1>")

        return self._send(404, f"<h1>404 — {u.path}</h1>")

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        params = parse_qs(body)

        if u.path == "/backoffice/UserAuthenticationServlet.do":
            user = (params.get("userid") or [""])[0]
            pw   = (params.get("passwd") or [""])[0]
            # Accept anything non-empty as valid for the demo
            if user and pw:
                LOGGED_IN_USERS.add(user)
                return self._redirect("/backoffice/stratus?screenType=CustomerList")
            return self._redirect("/backoffice/login.jsp?msg=fail")

        return self._send(404, f"<h1>404 POST — {u.path}</h1>")


def serve() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), MockHandler)
    print(f"  [mock-stratus] listening on http://{HOST}:{PORT}")
    print(f"  [mock-stratus] try: http://{HOST}:{PORT}/backoffice/login.jsp")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  [mock-stratus] shutting down")
        srv.shutdown()


if __name__ == "__main__":
    serve()
