// node tests/js/terminal.test.js — the live terminal's ANSI renderer and scroll detection (terminal.js).
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../herdr_attention/web/terminal.js"), "utf8");
const sandbox = { module: { exports: {} }, document: { addEventListener() {} } };
vm.runInNewContext(source, sandbox);
const T = sandbox.module.exports;
const E = "\x1b[";
let passed = 0;
function test(name, fn) { try { fn(); passed++; } catch (e) { console.error(`FAIL ${name}`); throw e; } }
const one = (text) => T.ansiLines(text).lines[0];

test("plain text is escaped, never markup", () => {
  const l = one(`<img src=x onerror=alert(1)> & "q"`);
  assert.equal(l.html, "&lt;img src=x onerror=alert(1)&gt; &amp; &quot;q&quot;");
  assert.equal(l.plain, `<img src=x onerror=alert(1)> & "q"`);
});

test("truecolor foreground and background, as Claude Code and Codex send them", () => {
  const l = one(`${E}38;2;238;238;238m${E}48;2;68;68;68m› New${E}0m tail`);
  assert.equal(l.html, '<span style="color:rgb(238,238,238);background:rgb(68,68,68)">› New</span> tail');
  assert.equal(l.plain, "› New tail");
});

test("colon form 38:2::r:g:b", () => {
  assert.match(one(`${E}38:2::1:2:3mx`).html, /color:rgb\(1,2,3\)/);
});

test("basic colours map to the Tokyo Night palette", () => {
  assert.match(one(`${E}31mred`).html, /color:#f7768e/);
  assert.match(one(`${E}92mgreen`).html, /color:#9fe044/);
  assert.match(one(`${E}44mbg`).html, /background:#7aa2f7/);
});

test("256 colours: cube and grey ramp", () => {
  assert.match(one(`${E}38;5;196mx`).html, /color:rgb\(255,0,0\)/);
  assert.match(one(`${E}38;5;244mx`).html, /color:rgb\(128,128,128\)/);
  assert.match(one(`${E}38;5;4mx`).html, /color:#7aa2f7/);
});

test("bold, dim, italic, underline, strike, inverse and their resets", () => {
  const l = one(`${E}1;3;4;9mA${E}22;23;24;29mB${E}2mC${E}0m${E}7;31mD`);
  assert.match(l.html, /<span style="font-weight:700;font-style:italic;text-decoration:underline line-through">A<\/span>/);
  assert.match(l.html, />B</);
  assert.ok(!/style="[^"]*">B/.test(l.html), "B has no style left");
  assert.match(l.html, /<span style="opacity:.62">C<\/span>/);
  assert.match(l.html, /<span style="color:var\(--term-bg\);background:#f7768e">D<\/span>/);
});

test("styles carry over a line end, a reset ends them", () => {
  const { lines } = T.ansiLines(`${E}32mone\ntwo${E}0m\nthree`);
  assert.match(lines[1].html, /color:#9ece6a">two/);
  assert.equal(lines[2].html, "three");
});

test("box drawing and wide characters pass through untouched", () => {
  const l = one(`╭─ 日本語 ─╮ ✻ ⏺`);
  assert.equal(l.plain, "╭─ 日本語 ─╮ ✻ ⏺");
  assert.equal(l.html, "╭─ 日本語 ─╮ ✻ ⏺");
});

test("a stray escape byte is dropped", () => {
  assert.equal(one(`a\x1bb`).plain, "ab");
});

test("scrolledBy finds how far the screen moved", () => {
  const prev = ["l1", "l2", "l3", "l4", "l5", "> prompt"];
  const next = ["l3", "l4", "l5", "l6", "l7", "> prompt"];
  assert.equal(T.scrolledBy(prev, next), 2);
  assert.equal(T.scrolledBy(prev, prev), 0);
  assert.equal(T.scrolledBy(prev, ["x", "y", "z"]), 0);
  assert.equal(T.scrolledBy(["", "", "a"], ["", "a", "b"]), 0, "needs real content to match");
});

test("v0.9.8.8: file paths on the screen become viewer chips, escaped and only with the contract pattern", () => {
  const contract = JSON.parse(fs.readFileSync(path.join(__dirname, "../../herdr_attention/ui_contract.json"), "utf8"));
  const box = { module: { exports: {} }, document: { addEventListener() {} }, UI: { files: contract.files } };
  vm.runInNewContext(source, box);
  const line = box.module.exports.ansiLines(`${E}32mWrote docs/working-styles.md${E}0m & <b>~/.ssh/x.md</b> ../up.md https://h/a.md`).lines[0];
  assert.match(line.html, /<span class="file-chip term-file" role="button" tabindex="0" data-file="docs\/working-styles.md">docs\/working-styles.md<\/span>/);
  assert.equal((line.html.match(/data-file=/g) || []).length, 1, line.html);
  assert.ok(line.html.includes("&lt;b&gt;"), "text stays escaped");
  assert.equal(line.plain, "Wrote docs/working-styles.md & <b>~/.ssh/x.md</b> ../up.md https://h/a.md");
  assert.equal(T.termFiles("a/b.md"), "a/b.md", "no contract (unit tests of terminal.js alone): unchanged");
});

console.log(`terminal.test.js: ${passed} passed`);
