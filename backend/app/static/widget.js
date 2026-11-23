/* AI Sales Executive chat widget. Vanilla JS, no dependencies.
   Embed: <script src="https://YOUR-API/api/v1/public/widget.js" data-api="https://YOUR-API" data-title="Chat with us" async></script> */
(function () {
  "use strict";
  var script = document.currentScript;
  var API = ((script && script.getAttribute("data-api")) || "").replace(/\/$/, "");
  if (!API) { console.warn("chat widget: data-api is missing"); return; }
  var TITLE = (script && script.getAttribute("data-title")) || "Chat with us";
  var KEY = "ase_chat_v1";
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function load() { try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch (e) { return null; } }
  function save(v) { try { sessionStorage.setItem(KEY, JSON.stringify(v)); } catch (e) {} }

  var css = [
    ".ase-b{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;background:#12151A;box-shadow:0 8px 30px rgba(0,0,0,.4);z-index:2147483000;display:flex;align-items:center;justify-content:center}",
    ".ase-o{width:22px;height:22px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#b6ffe0,#6EE7B7 55%,#1f9d72);box-shadow:0 0 14px rgba(110,231,183,.55)}",
    ".ase-o.idle{animation:ase-br 4s ease-in-out infinite}.ase-o.think{animation:ase-th 1.2s linear infinite}.ase-o.talk{animation:ase-br .6s ease-in-out infinite}",
    "@keyframes ase-br{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}@keyframes ase-th{0%{transform:scale(.9) rotate(0)}100%{transform:scale(.9) rotate(360deg)}}",
    ".ase-p{position:fixed;right:20px;bottom:88px;width:360px;max-width:calc(100vw - 24px);height:520px;max-height:calc(100vh - 110px);background:#0B0D10;color:#E8EAED;border:1px solid rgba(255,255,255,.08);border-radius:16px;display:none;flex-direction:column;overflow:hidden;font:14px/1.45 Inter,system-ui,sans-serif;z-index:2147483000;box-shadow:0 20px 60px rgba(0,0,0,.5)}",
    ".ase-p.open{display:flex}.ase-p.anim{animation:ase-in .24s cubic-bezier(.05,.7,.1,1)}@keyframes ase-in{from{opacity:0;transform:translateY(12px) scale(.98)}to{opacity:1;transform:none}}",
    ".ase-h{display:flex;align-items:center;gap:10px;padding:12px 14px;background:#12151A;border-bottom:1px solid rgba(255,255,255,.08)}.ase-h b{font-weight:600}.ase-h small{display:block;color:#6EE7B7;font-size:11px}",
    ".ase-x{margin-left:auto;background:none;border:0;color:#9aa1ab;font-size:20px;cursor:pointer}",
    ".ase-m{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}",
    ".ase-g{max-width:82%;padding:9px 12px;border-radius:14px;white-space:pre-wrap;word-wrap:break-word}.ase-g.me{align-self:flex-end;background:#6EE7B7;color:#06231a}.ase-g.ai{align-self:flex-start;background:#181C23}",
    ".ase-f{display:flex;gap:8px;padding:10px;border-top:1px solid rgba(255,255,255,.08);background:#12151A}",
    ".ase-f input{flex:1;background:#0B0D10;color:#E8EAED;border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:9px 11px;font:inherit}",
    ".ase-f button{background:#6EE7B7;color:#06231a;border:0;border-radius:10px;padding:0 14px;font-weight:600;cursor:pointer}.ase-f button:disabled{opacity:.5;cursor:default}",
    ".ase-n{color:#9aa1ab;font-size:12px;text-align:center;padding:4px}"
  ].join("");
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  var bubble = el("button", "ase-b"); bubble.setAttribute("aria-label", TITLE);
  var orbB = el("div", "ase-o idle"); bubble.appendChild(orbB);
  var panel = el("div", "ase-p"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", TITLE);
  var head = el("div", "ase-h"); var orb = el("div", "ase-o idle");
  var ht = el("div"); ht.appendChild(el("b", "", TITLE)); ht.appendChild(el("small", "", "AI assistant"));
  var close = el("button", "ase-x", "×"); close.setAttribute("aria-label", "Close chat");
  head.appendChild(orb); head.appendChild(ht); head.appendChild(close);
  var msgs = el("div", "ase-m"); msgs.setAttribute("aria-live", "polite");
  var form = el("form", "ase-f"); var input = el("input"); input.maxLength = 2000; var send = el("button", "", "Send"); send.type = "submit";
  form.appendChild(input); form.appendChild(send);
  panel.appendChild(head); panel.appendChild(msgs); panel.appendChild(form);
  document.body.appendChild(bubble); document.body.appendChild(panel);

  var state = load(); var needName = !state; var busy = false;
  function orbState(s) { [orb, orbB].forEach(function (o) { o.className = "ase-o " + (reduced ? "" : s); }); }
  function add(who, text) { var d = el("div", "ase-g " + who, text); msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d; }
  function note(t) { msgs.appendChild(el("div", "ase-n", t)); }
  function headers() { return { "Content-Type": "application/json", "X-Session-Token": state.token }; }

  function setBusy(b) { busy = b; send.disabled = b; }
  function open() {
    panel.classList.add("open"); if (!reduced) panel.classList.add("anim");
    if (!msgs.childNodes.length) {
      if (needName) { add("ai", "Hello! I am an AI assistant. What is your name?"); }
      else { restore(); }
    }
    input.focus();
  }
  bubble.addEventListener("click", function () { panel.classList.contains("open") ? panel.classList.remove("open") : open(); });
  close.addEventListener("click", function () { panel.classList.remove("open"); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) orbState("paused"); else orbState("idle"); });

  function restore() {
    fetch(API + "/api/v1/public/chat/sessions/" + state.id + "/history", { headers: headers() })
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (j) { (j.data || []).forEach(function (m) { add(m.direction === "inbound" ? "me" : "ai", m.body); }); })
      .catch(function () { sessionStorage.removeItem(KEY); state = null; needName = true; msgs.innerHTML = ""; add("ai", "Hello! I am an AI assistant. What is your name?"); });
  }

  function startSession(name) {
    return fetch(API + "/api/v1/public/chat/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name, page_url: location.href }) })
      .then(function (r) { if (!r.ok) throw new Error("start"); return r.json(); })
      .then(function (j) { state = { id: j.data.conversation_id, token: j.data.session_token, cursor: j.data.stream_cursor }; save(state); needName = false; });
  }

  function stream(cursor) {
    var bubbleEl = null; orbState("think");
    return fetch(API + "/api/v1/public/chat/sessions/" + state.id + "/stream?after=" + cursor, { headers: headers() }).then(function (resp) {
      var reader = resp.body.getReader(); var dec = new TextDecoder(); var buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var parts = buf.split("\n\n"); buf = parts.pop();
          for (var i = 0; i < parts.length; i++) {
            var ev = /event: (\w+)/.exec(parts[i]); var data = /data: (.*)/.exec(parts[i]);
            if (!ev || !data) continue; var d = JSON.parse(data[1]);
            if (ev[1] === "chunk") { orbState("talk"); bubbleEl = bubbleEl || add("ai", ""); bubbleEl.textContent += d.text; msgs.scrollTop = msgs.scrollHeight; state.cursor = (state.cursor || 0) + 1; }
            if (ev[1] === "done") { orbState("idle"); save(state); return; }
            if (ev[1] === "timeout") { orbState("idle"); note("A team member will get back to you."); return; }
          }
          return pump();
        });
      }
      return pump();
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault(); var text = input.value.trim(); if (!text || busy) return; input.value = ""; add("me", text); setBusy(true);
    var begin = needName ? startSession(text).then(function () { return "My name is " + text; }) : Promise.resolve(text);
    begin.then(function (body) {
      return fetch(API + "/api/v1/public/chat/sessions/" + state.id + "/messages", { method: "POST", headers: headers(), body: JSON.stringify({ text: body }) })
        .then(function (r) { if (!r.ok) throw new Error("send"); return r.json(); })
        .then(function (j) { return stream(j.data.stream_cursor); });
    }).catch(function () { orbState("idle"); note("Sorry, something went wrong. Please try again."); }).then(function () { setBusy(false); });
  });
})();
