// Offline smoke test for engine/harness/assets/opendraft-tools.ts — the only
// production-critical surface with no Python-side coverage. Loads the real extension
// with a mocked ExtensionAPI and exercises: tool registration, shell-free spawn with
// OPENDRAFT_BOOTSTRAP argv construction, envelope parsing (ok + error-rethrow model),
// .cmd/.bat rejection, abort-before-start, the compile_draft TUI gate, and the
// session_before_compact handler (default + extension-summary paths).
//
// Run: node tests/ts_extension_smoke.mjs   (from the repo root; Windows paths assumed)

import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PI_NM = "E:\\npm-global\\node_modules";
const NESTED = join(PI_NM, "@earendil-works", "pi-coding-agent", "node_modules");

const py = join(REPO, ".venv", "Scripts", "python.exe");

let passed = 0;
let failed = 0;
function check(name, cond, extra = "") {
  if (cond) { passed++; console.log(`ok   ${name}`); }
  else { failed++; console.log(`FAIL ${name}${extra ? " — " + extra : ""}`); }
}

// ---- sandbox with junctioned deps so the extension's bare imports resolve
const box = mkdtempSync(join(tmpdir(), "od-ts-smoke-"));
mkdirSync(join(box, "node_modules", "@earendil-works"), { recursive: true });
symlinkSync(join(PI_NM, "@earendil-works", "pi-coding-agent"),
  join(box, "node_modules", "@earendil-works", "pi-coding-agent"), "junction");
symlinkSync(join(NESTED, "@earendil-works", "pi-ai"),
  join(box, "node_modules", "@earendil-works", "pi-ai"), "junction");
symlinkSync(join(NESTED, "typebox"), join(box, "node_modules", "typebox"), "junction");
cpSync(join(REPO, "engine", "harness", "assets", "opendraft-tools.ts"),
  join(box, "opendraft-tools.ts"));
mkdirSync(join(box, "root"), { recursive: true });

// ---- mock ExtensionAPI
const tools = new Map();
const handlers = new Map();
const mockPi = {
  registerTool(spec) { tools.set(spec.name, spec); },
  on(event, handler) { handlers.set(event, handler); },
};

const mod = await import(pathToFileURL(join(box, "opendraft-tools.ts")).href);
mod.default(mockPi);

check("nine tools registered", tools.size === 9,
  `got ${tools.size}: ${[...tools.keys()].join(",")}`);
for (const t of ["read_artifact", "write_section", "score_draft", "search_literature",
                 "verify_claims", "revise_section", "compile_draft",
                 "write_outline", "manage_claims"]) {
  check(`tool ${t}`, tools.has(t));
}
check("session_before_compact handler registered", handlers.has("session_before_compact"));
check("tool_call (compile gate) handler registered", handlers.has("tool_call"));

// ---- env plumbing
process.env.OPENDRAFT_ROOT = join(box, "root");
const BOOTSTRAP = "import json,sys;print(json.dumps({'ok':True,'data':{'via':'bootstrap'}}))";
process.env.OPENDRAFT_BIN = py;
process.env.OPENDRAFT_BOOTSTRAP = BOOTSTRAP;

// happy path: python.exe + bootstrap argv, envelope parsed
{
  const res = await tools.get("read_artifact").execute("c1", { path: "x.md" }, undefined);
  const text = res.content.map((c) => c.text).join("");
  check("bootstrap spawn returns envelope text", text.includes('"via":"bootstrap"'), text.slice(0, 200));
}

// error model: ok:false envelopes are re-thrown so pi marks the tool result failed
{
  process.env.OPENDRAFT_BOOTSTRAP =
    "import json;print(json.dumps({'ok':False,'error':'boom','is_retryable':True}))";
  let msg = "";
  try { await tools.get("read_artifact").execute("c2", {}, undefined); }
  catch (e) { msg = e.message; }
  check("ok:false envelope rethrown", msg.includes("boom"), msg);
  process.env.OPENDRAFT_BOOTSTRAP = BOOTSTRAP;
}

// .cmd/.bat rejection with actionable guidance
{
  process.env.OPENDRAFT_BIN = "C:\\tmp\\opendraft.cmd";
  let msg = "";
  try { await tools.get("read_artifact").execute("c3", {}, undefined); }
  catch (e) { msg = e.message; }
  check(".cmd rejected with guidance", msg.includes("batch wrapper") && msg.includes("OPENDRAFT_BOOTSTRAP"), msg);
  process.env.OPENDRAFT_BIN = py;
}

// abort before start
{
  const ctl = new AbortController();
  ctl.abort();
  let msg = "";
  try { await tools.get("read_artifact").execute("c4", {}, ctl.signal); }
  catch (e) { msg = e.message; }
  check("abort before start rejects", msg.includes("aborted"), msg);
}

// compile_draft gate: headless allows, TUI-with-decline blocks
{
  const gate = handlers.get("tool_call");
  const r1 = await gate({ toolName: "compile_draft" }, { mode: "rpc", hasUI: false });
  check("headless compile_draft allowed", r1 === undefined);
  const r2 = await gate({ toolName: "compile_draft" },
    { mode: "tui", hasUI: true, ui: { confirm: async () => false } });
  check("TUI decline blocks compile_draft", r2 && r2.block === true);
  const r3 = await gate({ toolName: "read_artifact" }, { mode: "tui", hasUI: true });
  check("other tools pass the gate", r3 === undefined);
}

// compaction handler: no model -> default path; model+registry -> MUST-PRESERVE summary
{
  const h = handlers.get("session_before_compact");
  const event = {
    preparation: {
      messagesToSummarize: [],
      previousSummary: "prev",
      firstKeptEntryId: "id1",
      tokensBefore: 123,
    },
    signal: new AbortController().signal,
  };
  const r1 = await h(event, { model: undefined });
  check("no model -> undefined (pi default)", r1 === undefined);
  let seenPrompt = "";
  const ctx = {
    model: { id: "m" },
    modelRegistry: {
      complete: async (_m, context) => {
        seenPrompt = context.messages[0].content[0].text;
        return { content: [{ type: "text", text: "PAPER SUMMARY" }], usage: { total: 1 } };
      },
    },
  };
  const r2 = await h(event, ctx);
  check("extension compaction returned", r2 && r2.compaction && r2.compaction.summary === "PAPER SUMMARY");
  check("compaction keeps firstKeptEntryId/tokensBefore",
    r2 && r2.compaction.firstKeptEntryId === "id1" && r2.compaction.tokensBefore === 123);
  check("MUST-PRESERVE mentions AGENTS.md + ledger + section_status",
    seenPrompt.includes("AGENTS.md") && seenPrompt.includes("claims.jsonl")
    && seenPrompt.includes("section_status.json"));
  const r3 = await h(event, {
    model: { id: "m" },
    modelRegistry: { complete: async () => { throw new Error("api down"); } },
  });
  check("registry failure -> undefined (never blocks compaction)", r3 === undefined);
}

rmSync(box, { recursive: true, force: true });
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
