/**
 * OpenDraft agent tools — pi extension (design doc docs/AGENT_HARNESS_DESIGN.md §6).
 *
 * Each tool is a stateless wrapper around the OpenDraft CLI:
 *   opendraft tool <name> --root <OPENDRAFT_ROOT> --args '<json>'
 * The CLI prints exactly one JSON envelope line on stdout:
 *   {"ok": true, "data": {...}}  |  {"ok": false, "error": str, "is_retryable": bool, "details": {...}}
 *
 * Error model: pi marks a tool result as failed (isError) ONLY when execute() throws —
 * a returned value never sets the flag, regardless of its properties. So ok=false envelopes
 * are re-thrown as Error with retry guidance, which is what feeds the failure back to the LLM
 * without ever interrupting the agent loop.
 *
 * OPENDRAFT_BIN: absolute path to the opendraft executable is preferred (the harness driver
 * sets it to the venv's opendraft.exe). Falls back to "opendraft" resolved on PATH.
 * OPENDRAFT_ROOT: the paper output directory (defaults to pi's cwd).
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type, type Static } from "typebox";

const TOOL_TIMEOUT_MS = 120_000;

type ToolName =
  | "read_artifact"
  | "write_section"
  | "score_draft"
  | "search_literature"
  | "verify_claims"
  | "revise_section"
  | "compile_draft";

type Envelope =
  | { ok: true; data: unknown }
  | { ok: false; error?: string; is_retryable?: boolean; details?: unknown };

/** Resolve the opendraft executable. Windows cannot spawn bare .cmd names without a shell,
 * so search PATH for the real binary (exe first). */
function opendraftCommand(): { cmd: string; shell: boolean } {
  const bin = process.env.OPENDRAFT_BIN ?? "opendraft";
  if (bin !== "opendraft") return { cmd: bin, shell: false };
  if (process.platform !== "win32") return { cmd: bin, shell: false };
  for (const dir of (process.env.PATH ?? "").split(delimiter)) {
    if (!dir) continue;
    for (const name of ["opendraft.exe", "opendraft.cmd", "opendraft.bat", "opendraft"]) {
      const full = dir + sep + name;
      if (existsSync(full)) {
        return { cmd: full, shell: name.endsWith(".cmd") || name.endsWith(".bat") };
      }
    }
  }
  return { cmd: bin, shell: false };
}

function lastNonEmptyLine(text: string): string {
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const trimmed = lines[i].trim();
    if (trimmed) return trimmed;
  }
  return "";
}

function tail(text: string, max = 500): string {
  const t = text.trim();
  return t.length > max ? `...${t.slice(-max)}` : t;
}

function formatFailure(toolName: string, env: Extract<Envelope, { ok: false }>): string {
  const retryable = env.is_retryable === true;
  const hint = retryable
    ? "This looks transient — retry the same call once or twice; if it keeps failing, " +
      "narrow the arguments (e.g. a more specific query) or move on and revisit later."
    : "This will not fix itself — correct the arguments or read the referenced artifacts " +
      "with read_artifact, then retry.";
  return [
    `${toolName} failed: ${env.error ?? "unknown error"}`,
    `retryable: ${retryable}`,
    `suggested next step: ${hint}`,
    env.details !== undefined ? `details: ${JSON.stringify(env.details)}` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

/** Stateless spawn of `opendraft tool <name> ...`; resolves with the compact JSON result
 * text for the LLM, or rejects (→ pi isError) on envelope failure / crash / timeout. */
async function runOpendraftTool(
  toolName: ToolName,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<string> {
  const root = process.env.OPENDRAFT_ROOT ?? process.cwd();
  const { cmd, shell } = opendraftCommand();
  const argv = ["tool", toolName, "--root", root, "--args", JSON.stringify(args ?? {})];

  return new Promise<string>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error(`${toolName}: aborted before start`));
      return;
    }
    const child = spawn(cmd, argv, { shell, windowsHide: true });
    let stdout = "";
    let stderr = "";
    let settled = false;

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      settled = true;
      reject(new Error(`${toolName}: timed out after ${TOOL_TIMEOUT_MS / 1000}s and was killed`));
    }, TOOL_TIMEOUT_MS);

    const onAbort = () => {
      child.kill("SIGKILL");
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`${toolName}: aborted`));
      }
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => (stdout += chunk));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => (stderr += chunk));

    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(
        new Error(
          `${toolName}: failed to start the opendraft CLI (${cmd}). ` +
            `Set OPENDRAFT_BIN to the absolute path of the opendraft executable. ` +
            `Cause: ${err.message}`,
        ),
      );
    });

    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);

      const line = lastNonEmptyLine(stdout);
      if (!line) {
        reject(
          new Error(
            `${toolName}: opendraft produced no output (exit ${code ?? "signal"}). ` +
              `stderr: ${tail(stderr) || "(empty)"}`,
          ),
        );
        return;
      }
      let envelope: Envelope;
      try {
        envelope = JSON.parse(line) as Envelope;
      } catch {
        reject(
          new Error(
            `${toolName}: last stdout line is not JSON: "${line.slice(0, 300)}". ` +
              `stderr: ${tail(stderr) || "(empty)"}`,
          ),
        );
        return;
      }
      if (envelope && envelope.ok === true) {
        // Compact single-line JSON keeps tool results token-cheap for the LLM.
        resolve(JSON.stringify(envelope.data ?? {}));
        return;
      }
      reject(new Error(formatFailure(toolName, (envelope ?? {}) as Extract<Envelope, { ok: false }>)));
    });
  });
}

const sectionEnum = StringEnum([
  "introduction",
  "literature_review",
  "methodology",
  "results",
  "discussion",
  "conclusion",
  "appendices",
] as const);

const readArtifactSchema = Type.Object({
  path: Type.String({
    minLength: 1,
    description:
      "Path relative to the output root, e.g. 'research/combined_research.md', " +
      "'drafts/00_formatted_outline.md', 'research/bibliography.json'.",
  }),
  offset: Type.Optional(
    Type.Integer({ description: "Character offset to start reading from (for paging). Default 0." }),
  ),
  limit: Type.Optional(
    Type.Integer({
      description: "Max characters to return. Default 4000, hard cap 20000.",
    }),
  ),
});

const writeSectionSchema = Type.Object({
  section: StringEnum([
    "introduction",
    "literature_review",
    "methodology",
    "results",
    "discussion",
    "conclusion",
    "appendices",
    "custom",
  ] as const),
  content: Type.String({ minLength: 1, description: "Full markdown content of the section." }),
  citations_used: Type.Optional(
    Type.Array(Type.String(), {
      description:
        "cite_XXX ids cited in this section. Each must exist in research/bibliography.json " +
        "(checked against the file, not just this list).",
    }),
  ),
  slug: Type.Optional(
    Type.String({
      description:
        "Required for section='custom': filename slug, e.g. 'related_work'. " +
        "Writes to drafts/custom_sections/custom_<slug>.md.",
    }),
  ),
});

const scoreDraftSchema = Type.Object({
  scope: Type.Optional(
    StringEnum(["section", "full"] as const),
  ),
  section: Type.Optional(sectionEnum),
});

const searchLiteratureSchema = Type.Object({
  query: Type.String({
    minLength: 3,
    description:
      "Search query — prefer specific author/year/keyword phrasing. If results are thin, " +
      "rephrase rather than lowering min_results.",
  }),
  min_results: Type.Optional(
    Type.Integer({ default: 5, description: "Desired minimum number of new citations (default 5)." }),
  ),
});

const verifyClaimsSchema = Type.Object({
  claims: Type.Array(
    Type.Object({
      claim: Type.String({ minLength: 1 }),
      section: Type.Optional(Type.String()),
      line: Type.Optional(Type.String()),
    }),
    {
      description:
        "Claims to verify (each must have a non-empty 'claim'; 'section'/'line' are optional " +
        "labels echoed back).",
    },
  ),
  max_workers: Type.Optional(
    Type.Integer({ default: 10, description: "Max parallel verification threads (default 10)." }),
  ),
});

const reviseSectionSchema = Type.Object({
  section: sectionEnum,
  instructions: Type.String({
    minLength: 1,
    description: "Revision instructions for the LLM (used whenever find_replace is absent or has misses).",
  }),
  find_replace: Type.Optional(
    Type.Array(
      Type.Object({
        find: Type.String({ minLength: 1 }),
        replace: Type.String(),
      }),
      {
        description:
          "Optional exact-match replacements. A pair is applied only if its 'find' occurs exactly " +
          "once; otherwise it lands in missed_replacements and is passed to the LLM.",
      },
    ),
  ),
});

const compileDraftSchema = Type.Object({
  format: Type.Optional(
    StringEnum(["md", "pdf", "docx", "all"] as const),
  ),
});

type ReadArtifactArgs = Static<typeof readArtifactSchema>;
type WriteSectionArgs = Static<typeof writeSectionSchema>;
type ScoreDraftArgs = Static<typeof scoreDraftSchema>;
type SearchLiteratureArgs = Static<typeof searchLiteratureSchema>;
type VerifyClaimsArgs = Static<typeof verifyClaimsSchema>;
type ReviseSectionArgs = Static<typeof reviseSectionSchema>;
type CompileDraftArgs = Static<typeof compileDraftSchema>;

export default function (pi: ExtensionAPI) {
  const makeExecute =
    <T>(name: ToolName) =>
    async (_toolCallId: string, params: T, signal: AbortSignal | undefined) => {
      const text = await runOpendraftTool(name, (params ?? {}) as Record<string, unknown>, signal);
      return { content: [{ type: "text" as const, text }], details: {} };
    };

  pi.registerTool({
    name: "read_artifact",
    label: "Read artifact",
    description:
      "Read a file from the paper's output directory: research notes (research/papers/*.md, " +
      "research/combined_research.md, research/research_gaps.md), the outline " +
      "(drafts/00_formatted_outline.md), the citation ledger (research/bibliography.json, " +
      "drafts/citation_summary.md), already-written section drafts, and QA reports. " +
      "Use BEFORE writing to ground the section in the real research material (never write from " +
      "memory), to check exactly which cite_XXX ids exist before citing them, and to review what " +
      "neighboring sections contain. Read-only and side-effect free — safe to call in parallel. " +
      "Do NOT use it for files outside the output directory (rejected), and do NOT use it to " +
      "write anything.",
    promptSnippet: "Read research notes, outline, bibliography, or drafts from the output directory",
    promptGuidelines: [
      "Use read_artifact before writing anything: read the research notes for this section, the outline, and the bibliography section of AGENTS.md first.",
      "Use read_artifact on research/bibliography.json to confirm a cite_XXX id exists before putting it in write_section's citations_used.",
      "Use read_artifact with offset to page through long files instead of reading them whole.",
    ],
    parameters: readArtifactSchema,
    execute: makeExecute<ReadArtifactArgs>("read_artifact"),
  });

  pi.registerTool({
    name: "write_section",
    label: "Write section",
    description:
      "Write (or fully rewrite) one paper section as markdown. Idempotent: same input produces " +
      "the same file, so quality-gate revise loops are safe to retry. Guardrails reject lazy or " +
      "unsupported output: sections far below the word target, TODO/[INSERT]/[expand] " +
      "placeholders, or cite_XXX ids missing from research/bibliography.json — the rejection " +
      "message lists the exact violations to fix. A version snapshot is kept before every " +
      "overwrite. Use for the initial full write of a section and for wholesale rewrites. " +
      "Do NOT use it for small targeted edits (use revise_section) and do NOT cite any id that " +
      "search_literature has not returned.",
    promptSnippet: "Write or fully rewrite one paper section (guardrailed, idempotent)",
    promptGuidelines: [
      "Use write_section with the COMPLETE section markdown in 'content' — never fragments, never 'continue in next call'.",
      "Use write_section only with cite_XXX ids that search_literature returned or that read_artifact confirmed in research/bibliography.json.",
      "After every write_section call, immediately run score_draft (scope=section) and fix the reported issues before moving on.",
    ],
    parameters: writeSectionSchema,
    execute: makeExecute<WriteSectionArgs>("write_section"),
  });

  pi.registerTool({
    name: "score_draft",
    label: "Score draft",
    description:
      "Score draft quality (0-100) with a structured issues list [{section, metric, actual, " +
      "target, severity}] covering word count, citations, completeness, and structure. " +
      "Read-only and idempotent — call it any number of times, it never changes anything. " +
      "Use after EVERY write_section to self-check, and again after fixes to confirm they worked. " +
      "The issues list is the fix backlog: word_count short → expand via revise_section; " +
      "citations thin → search_literature then rewrite; completeness → add the missing " +
      "subsections. Do NOT treat a passing score as permission to skip verify_claims on hard " +
      "factual claims.",
    promptSnippet: "Score the draft and get structured issues to fix (read-only gate)",
    promptGuidelines: [
      "Use score_draft after every write_section and after every batch of revise_section fixes, until no high-severity issues remain.",
      "Use score_draft's issues (metric/actual/target) to choose the remedy: revise_section for wording and structure, search_literature for thin evidence.",
      "Use score_draft with scope=full only when the whole paper is written.",
    ],
    parameters: scoreDraftSchema,
    execute: makeExecute<ScoreDraftArgs>("score_draft"),
  });

  pi.registerTool({
    name: "search_literature",
    label: "Search literature",
    description:
      "Search academic databases (Crossref, OpenAlex, Semantic Scholar, Google-grounded web " +
      "search) for REAL papers matching a query, and ADD them to research/bibliography.json so " +
      "they immediately become citable — newly found citations get cite_XXX ids that " +
      "write_section's whitelist accepts. Returns the new citations with titles/authors/year. " +
      "Use BEFORE citing anything that is not already in the bibliography, when score_draft " +
      "reports thin citations, or when research gaps need filling. Do NOT use it to re-fetch " +
      "sources already in the ledger, and never invent or hand-type citations instead.",
    promptSnippet: "Search academic databases and add found papers to the citation ledger",
    promptGuidelines: [
      "Use search_literature BEFORE citing any source that is not already in research/bibliography.json — citing first and searching later is a guardrail violation.",
      "Use search_literature with specific author/year/keyword queries; if results are thin, rephrase the query instead of lowering min_results.",
      "Only cite cite_XXX ids that search_literature (or read_artifact on the bibliography) confirmed exist.",
    ],
    parameters: searchLiteratureSchema,
    execute: makeExecute<SearchLiteratureArgs>("search_literature"),
  });

  pi.registerTool({
    name: "verify_claims",
    label: "Verify claims",
    description:
      "Fact-check draft claims against live web evidence. Each claim gets a verdict " +
      "SUPPORTED / CONTRADICTED / INSUFFICIENT with confidence, an evidence snippet and — for " +
      "CONTRADICTED claims — a wrong_part/correct_value pair that can be fed straight into " +
      "revise_section's find_replace. Use on key factual or quantitative claims after a section " +
      "is written (names, dates, numbers, causal claims). Read-only and parallelizable: batch " +
      "several claims in one call. Do NOT use it for style or completeness judgment — that is " +
      "score_draft's job.",
    promptSnippet: "Fact-check draft claims against web evidence (SUPPORTED/CONTRADICTED/...)",
    promptGuidelines: [
      "Use verify_claims on the section's key factual claims after score_draft passes, before considering the section done.",
      "Use verify_claims' CONTRADICTED wrong_part/correct_value pairs as revise_section find_replace entries for deterministic fixes.",
      "Use verify_claims in batches: one call with several claims is cheaper than many calls with one.",
    ],
    parameters: verifyClaimsSchema,
    execute: makeExecute<VerifyClaimsArgs>("verify_claims"),
  });

  pi.registerTool({
    name: "revise_section",
    label: "Revise section",
    description:
      "Revise one already-written section (must exist — write it with write_section first). " +
      "Two modes: (1) find_replace pairs for deterministic unique-match edits — this consumes " +
      "verify_claims' wrong_part/correct_value output directly; pairs whose 'find' text is not " +
      "unique land in missed_replacements and are passed to the LLM instead. (2) Free-form " +
      "instructions for an LLM revision pass. A version snapshot is kept automatically. Use for " +
      "targeted fixes driven by score_draft issues or verify_claims verdicts. Do NOT use it to " +
      "write a section from scratch and do NOT expect it to add new citations — search first " +
      "with search_literature, then rewrite.",
    promptSnippet: "Targeted revision of an existing section (find_replace or LLM instructions)",
    promptGuidelines: [
      "Use revise_section with find_replace for precise fixes (verify_claims wrong_part/correct_value, single-sentence rewrites).",
      "Use revise_section with instructions for broader rewrites (fix a score_draft metric across the whole section).",
      "Use revise_section only on sections that exist; after revising, run score_draft again to confirm the fix.",
    ],
    parameters: reviseSectionSchema,
    execute: makeExecute<ReviseSectionArgs>("revise_section"),
  });

  pi.registerTool({
    name: "compile_draft",
    label: "Compile draft",
    description:
      "Compile and export the finished paper (markdown, PDF, DOCX) and regenerate the reference " +
      "list — deterministic, no LLM. May run {cite_MISSING} citation backfill research as a side " +
      "effect. Prerequisites: every section written via write_section, research/bibliography.json " +
      "present, export toolchain available. Use ONCE at the very end, after all sections pass " +
      "score_draft and key claims are verified. Do NOT use it mid-writing — exports overwrite " +
      "each other and a half-written draft produces a half-empty paper.",
    promptSnippet: "Compile and export the finished paper (deterministic, run once at the end)",
    promptGuidelines: [
      "Use compile_draft only after all planned sections are written and self-checked with score_draft.",
      "Use compile_draft as the last tool call of the run; report the exported paths in the final message.",
    ],
    parameters: compileDraftSchema,
    execute: makeExecute<CompileDraftArgs>("compile_draft"),
  });

  // Headless runs (RPC mode, driven by the OpenDraft PiDriver) cannot ask a human, so
  // compile_draft is allowed through. In an interactive TUI, ask first — exports cost time
  // and trigger citation backfill.
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "compile_draft") return;
    if (ctx.mode === "rpc" || !ctx.hasUI) return;
    const confirmed = await ctx.ui.confirm(
      "Compile draft?",
      "compile_draft exports the paper (PDF/DOCX) and may trigger citation backfill research. Run it now?",
    );
    if (!confirmed) {
      return { block: true, reason: "compile_draft cancelled by user" };
    }
  });
}
