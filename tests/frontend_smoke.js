/*
 * Frontend smoke test — loads static/index.html + static/app.js in a real DOM
 * (jsdom) with the backend HTTP API mocked, then checks that the UI wires up,
 * renders the capabilities from /api/commands, sends commands, and explains
 * voice failures correctly (including the embedded-preview case).
 *
 * Optional tooling: this is not part of the Python test suite because it needs
 * Node + jsdom. Run it when you change the frontend:
 *
 *     cd tests && npm install jsdom && node frontend_smoke.js
 *
 * The capability fixture is generated from the backend and kept honest by
 * tests/test_backend.py::FixtureTests, so the two can't drift apart.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HERE = __dirname;
const REPO = path.resolve(HERE, "..");
const FIXTURE = path.join(HERE, "fixtures", "capabilities.json");

let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch (_error) {
  console.log("jsdom is not installed — run `npm install jsdom` first. Skipping.");
  process.exit(0);
}

const html = fs.readFileSync(path.join(REPO, "static", "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(REPO, "static", "app.js"), "utf8");
const CAPABILITIES = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));

const problems = [];
const dom = new JSDOM(html, { url: "https://8000-example.e2b.app/", pretendToBeVisual: true });
const { window } = dom;
window.addEventListener("error", (event) => problems.push(`window error: ${event.message}`));

// A SpeechRecognition stub that fails exactly like an embedded preview does.
class FakeRecognition {
  start() { setTimeout(() => this.onerror({ error: "not-allowed" }), 5); }
  stop() { if (this.onend) this.onend(); }
}
window.SpeechRecognition = FakeRecognition;

const calls = [];
let lastSession = null;
window.fetch = async (url, options = {}) => {
  calls.push({ url, body: options.body });
  const json = (data) => ({ ok: true, status: 200, json: async () => data });
  if (url === "/api/health") return json({ status: "ok", assistant: "Alexa" });
  if (url === "/api/commands") return json(CAPABILITIES);
  if (url === "/api/reset") return json({ status: "ok" });
  if (url === "/api/command") {
    const payload = JSON.parse(options.body);
    const command = payload.command;
    lastSession = payload.session;
    if (/open youtube/.test(command)) {
      return json({ command, session: payload.session, reply: "Opening YouTube.", action: { label: "Open YouTube", url: "https://www.youtube.com" }, status: "action", should_continue: true });
    }
    if (/^tell me a joke$/.test(command)) {
      return json({ command, session: payload.session, reply: "Why did the Python programmer need glasses? Because they couldn't C.", action: null, status: "chat", should_continue: true });
    }
    if (/^open it$/.test(command)) {
      return json({ command, session: payload.session, reply: "Opening that link.", action: { label: "Open YouTube", url: "https://www.youtube.com" }, status: "followup", should_continue: true });
    }
    return json({ command, session: payload.session, reply: "I'm not sure what to do with that yet.", action: null, status: "chat", should_continue: true });
  }
  return { ok: false, status: 404, json: async () => ({}) };
};

// Run app.js as if the page were embedded: window.self !== window.top.
const fakeTop = { previewHost: true };
let sandbox;
sandbox = new Proxy(window, {
  has: (target, key) => Reflect.has(target, key) || ["top", "self", "window"].includes(key),
  get(target, key) {
    if (key === "top") return fakeTop;
    if (key === "self" || key === "window") return sandbox;
    return Reflect.get(target, key);
  },
  set: (target, key, value) => Reflect.set(target, key, value),
});

const context = vm.createContext(sandbox);
try {
  vm.runInContext(appJs, context, { filename: "app.js" });
} catch (error) {
  problems.push(`app.js threw: ${error.message}`);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  await wait(250);
  const doc = window.document;
  const check = (name, condition) => {
    console.log(`${condition ? "PASS" : "FAIL"}  ${name}`);
    if (!condition) problems.push(name);
  };
  const click = (selector) => doc.querySelector(selector).dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  check("backend health shows online", doc.getElementById("health-dot").classList.contains("online"));
  check("quick launch rendered", doc.querySelectorAll("#rail-launch .launch-item").length === 6);
  check("capability cards rendered", doc.querySelectorAll("#cap-grid .cap-card").length >= 3);
  check("capability buttons rendered", doc.querySelectorAll("#cap-grid .cap-item").length >= 10);
  check("suggestion chips rendered", doc.querySelectorAll("#suggestion-row .suggestion-chip").length === 3);
  check("embedded frame detected", doc.getElementById("voice-badge").textContent.includes("may be blocked"));

  const input = doc.getElementById("command-input");
  input.value = "open youtube";
  doc.getElementById("command-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(150);
  check("command produced two bubbles", doc.querySelectorAll("#conversation .message").length === 2);
  const link = doc.querySelector("#conversation .message-action");
  check("action link uses https", !!link && link.getAttribute("href").startsWith("https://"));
  check("count badge updated", doc.getElementById("count-badge").textContent === "1");

  click("#cap-grid .cap-item");
  await wait(150);
  check("capability card sent a command", calls.filter((call) => call.url === "/api/command").length === 2);

  click("#mic-button");
  await wait(120);
  check("voice failure explained", !doc.getElementById("status-fix").hidden);
  check("blames the embedded preview", /embedded preview/i.test(doc.getElementById("status-fix-text").textContent));

  click("#voice-check");
  await wait(120);
  check("diagnostics rendered", doc.querySelectorAll("#check-list li").length >= 5);
  check("diagnostics mention the frame limit", /frame/i.test(doc.getElementById("check-list").textContent));
  check("diagnostics report the error", /not-allowed/i.test(doc.getElementById("check-list").textContent));

  // normal conversation: a plain sentence gets a prose reply, not a dead end
  input.value = "tell me a joke";
  doc.getElementById("command-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(150);
  const chatReply = doc.querySelector("#conversation .message-assistant:last-of-type .message-text");
  check("conversational command gets a prose reply", !!chatReply && chatReply.textContent.length > 10);
  check("chat status is labelled for the user", doc.querySelector("#conversation .message-assistant:last-of-type .tag").textContent === "chat");
  check("commands carry a session id", typeof lastSession === "string" && lastSession.length > 0);

  // follow-up memory: "open it" reuses the earlier link
  input.value = "open it";
  doc.getElementById("command-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(150);
  const followLink = doc.querySelector("#conversation .message-assistant:last-of-type .message-action");
  check("follow-up reuses the remembered link", !!followLink && followLink.getAttribute("href") === "https://www.youtube.com");
  check("follow-up status is labelled", doc.querySelector("#conversation .message-assistant:last-of-type .tag").textContent === "follow-up");

  click("#clear-log");
  await wait(80);
  check("clear empties the conversation", doc.querySelectorAll("#conversation .message").length === 0);
  check("clear also resets the session server-side", calls.some((call) => call.url === "/api/reset"));

  console.log("\n--- captured problems ---");
  console.log(problems.length ? problems.join("\n") : "none");
  process.exit(problems.length ? 1 : 0);
})();
