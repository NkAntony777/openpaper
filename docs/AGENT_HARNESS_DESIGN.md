# OpenDraft Agent Harness 设计方案

> 目标：把 OpenDraft 从"固定 19-agent 流水线"升级为"论文写作特化的 coding harness"——
> agent 通过 function calling 调用少量但扎实的工具，质量门失败时自主探索/补救，
> 并为 RSI（自我改进）留出种子。本方案基于对现有代码的逐行调查 + 对开源 harness 的调研。

状态：草案 v0.1（待评审） · 2026-02

---

## 0. 结论速览

**推荐路线：混合式 —— pi agent 作底座，OpenDraft 退到工具与技能层。**

```
┌─────────────────────────────────────────────────────────────┐
│  Driver（Python，新建 engine/harness/）                      │
│  预算熔断 · run_journal · 质量门策略 · 会话恢复               │
└──────────────┬──────────────────────────────────────────────┘
               │ spawn, JSONL over stdin/stdout
               ▼
┌─────────────────────────────────────────────────────────────┐
│  pi agent（TS/Bun，底座：loop + 上下文管理 + 工具执行）      │
│  read/write/edit + 4 个论文工具（extension 注册）            │
└──────────────┬──────────────────────────────────────────────┘
               │ tool call handler → spawn
               ▼
┌─────────────────────────────────────────────────────────────┐
│  opendraft CLI（Python，新增 `opendraft tool` 子命令）       │
│  search_literature / verify_claims / score_draft / …        │
│  （薄封装：CitationResearcher / FactCheckVerifier / …）       │
└──────────────┬──────────────────────────────────────────────┘
               ▼
        opendraft_output/   ← 唯一的"文件系统"与事实源
```

四个理由：

1. **哲学完全吻合**。OpenDraft 自己的规划文档（OPTIMIZATION_DIRECTIONS §11）已写明："opendraft 应该当工具被编排，不应该编排别人"。pi 恰好是那个"编排者"。
2. **省去 80% 的自研工作量**。agent loop、auto-compaction、会话持久化、工具参数校验（TypeBox/AJV）、TUI、headless 模式，pi 全部现成且 MIT。自研这些在 Python 里是数月工作量，且不会比它做得好。
3. **RPC 模式是一等公民，官方自带 Python 客户端示例**——Python 侧驱动没有 hacks。
4. **本机已装**（`E:\npm-global\pi` v0.84.3），PoC 零安装成本。

**工具不在多，在于 solid**：本方案只设计 8 个工具。选型依据见 §1 调研结论。

---

## 1. 设计依据（开源 harness 调研摘要）

| 证据 | 核心发现 | 来源 |
|---|---|---|
| Vercel 内部实验 | 把 agent 从 16 个工具砍到 2 个（ExecuteCommand + ExecuteSQL）：成功率 80%→100%，token -37%，速度 3.5x。"Every tool is a choice you're making for the model." | vercel.com/blog/we-removed-80-percent-of-our-agents-tools |
| 同上的对照实验 | 纯 bash 查结构化数据只有 52.7%，但"bash 探索 + 专用工具 + bash 复核"混合方案稳定 100%——**自验证行为是最有价值的涌现** | vercel.com/blog/testing-if-bash-is-all-you-need |
| mini-swe-agent（SWE-bench 团队） | ~100 行 Python + 只有 bash 一个工具，SWE-bench Verified >74%。线性 message history、无状态 action | github.com/SWE-agent/mini-swe-agent |
| Claude Code 逆向论文 | 核心循环 19 个无条件工具；deny 规则在模型看到之前过滤；上下文压缩五层渐进（裁剪→微压→摘要，摘要最后做）；Edit 用唯一文本匹配不用行号 | arxiv.org/abs/2604.14228 |
| Aider | diff/SKILL 格式四条原则（熟悉、简单、无行号、宽容应用）；repo map ~1k token 当"目录页"；git 即安全网 | aider.chat/docs/unified-diffs.html |
| Anthropic tool-use 文档 | 错误一律作为 is_error tool result 回灌，loop 永不中断；并行调用只用于无依赖只读操作 | docs.anthropic.com/en/docs/agents-and-tools/tool-use |
| Anthropic Building Effective Agents | evaluator-optimizer 模式：生成→评估→反馈→再生成，直到达标——这是质量门闭环的教科书原型 | anthropic.com/engineering/building-effective-agents |

**对论文写作域的推论**（写作 vs coding 的差异）：

1. 写作没有测试套件，gate 是 LLM 评估器 → 评分工具必须返回**结构化、带阈值的诊断**，而不是布尔值；
2. gate 失败后的 revise 决策**交还给模型**（evaluator-optimizer），harness 只设 round 上限和预算，不硬编码"低分就重写全文"；
3. coding 的 grep/glob 对应写作的**文献检索 + 研究资料阅读**；保留语义正确的 `search_literature`（对应 SQL），同时让 agent 能 `read_artifact` 复核（对应 bash 抽查）；
4. repo map 的对应物 = **AGENTS.md 形式的论文地图**（outline + brief + 引文账本指针），每轮低成本可见全文结构；
5. `write_section` 必须幂等（同输入同结果），gate→revise 循环才能安全重试；
6. 防幻觉引用的结构性方案：**引文必须经 search_literature 落库后才允许被 write_section 引用**（guardrail，见 §4）。

---

## 2. 方案选型

| 维度 | A：pi 底座 + OpenDraft 工具层（推荐） | B：纯自研 Python loop | C：分期混合 |
|---|---|---|---|
| loop/compaction/会话 | pi 现成 | 全自研（数月） | 同 A，保留 B 退路 |
| 语言边界 | pi 扩展是 TS，但 handler 只 spawn Python CLI | 无 | 同 A |
| token 精确记账 | pi 会话级 best-effort；精确分阶段成本仍在 Python 侧 TokenTracker | 完全可控 | 同 A |
| 质量门/预算控制 | Driver 层承担（pi **无 max-steps 旋钮**，必须自建预算熔断） | 完全可控 | 同 A |
| 依赖风险 | pi 周更、churn 高；Windows 是 issue 重灾区 | 零外部依赖 | 同 A |
| 交付周期 | M1 PoC 1-2 天 | 2-3 个月起 | — |

**决策建议：走 C，按 A 实施。** 理由：工具层（§4）与 loop 无关，无论底座是谁都必须建——
所以第一步永远是"工具化"，它本身就有价值（让现有 6 个未接线 agent 和 phase API 可被任意 harness 使用）。
pi 若在某项验收上不达标（长任务稳定性、Windows 适配），工具层可平移给自研 loop，沉没成本极小。

---

## 3. pi 底座关键事实（调研核实，含来源）

仓库已迁移：`badlogic/pi-mono` → **earendil-works/pi**（104k stars，MIT，TypeScript/Bun，
2026-04 起归 Armin Ronacher 的 Earendil 公司；npm 包名 `@earendil-works/pi-*`）。

对本设计重要的事实：

- **内置工具**：默认 4 个（read/write/edit/bash，Windows 上 bash 换成 powershell）；
  `--tools read,grep,find,ls` 即只读模式。
- **扩展机制**：`~/.pi/agent/extensions/` 或项目 `.pi/extensions/` 下放 TypeScript 文件自动发现，
  免编译、`/reload` 热更新。`pi.registerTool({name, description, parameters: TypeBox, execute})`，
  **运行中可动态注册**；`pi.on("tool_call", ...)` 可拦截/阻止调用；`before_agent_start` 可改写系统 prompt；
  支持自定义 compaction 策略。50+ 官方扩展示例。
- **无头驱动三模式**：`pi -p "任务"`（print）、`pi --mode json`（JSONL 事件）、
  **`pi --mode rpc`（stdin/stdout JSONL 双向协议，官方文档自带 Python 客户端示例）**。
  `agent_settled` 是干净的任务完成信号；`get_session_stats` 返回 tokens/cost/contextUsage。
  会话文件是 append-only JSONL，可断点检查。
- **模型**：15+ provider，Google（AI Studio + Vertex）一等支持，`GEMINI_API_KEY` 直接可用。
  ⚠️ 内置目录（0.84.3）没有 `gemini-3-pro-preview` 精确 ID，有 `gemini-3.1-pro-preview`；
  解法：`models.json` 加自定义条目（官方有 Google AI Studio 模板）或直接换 3.1。
- **工具结果可携带嵌套 `usage`**：OpenDraft 工具内部烧的 Gemini token 能并进 pi 会话总账。
- **默认 YOLO、无审批、无 max-steps、无子代理、无 MCP 内置**——全部是"可用 extension 自建的 primitive"。
  MCP 有成熟社区适配器（nicobailon/pi-mcp-adapter），但我们推荐 extension 直连（少一层间接）。
- **已知坑**：auto-compaction 时机问题（历史上有 100% 不触发的 bug）；Windows git-bash 检测/
  PowerShell 是一Issue 重灾区（61 评论 open）；loop 跑飞会一直烧 token（→ Driver 预算熔断必须做）；
  无人值守官方建议容器化（containerization.md）。
- 生态：pi.dev 目录 5000+ 扩展包；**无 arXiv/Semantic Scholar 学术搜索 skill**（需自写，模式成熟）；
  最大非 coding 底座实证是 OpenClaw（pi SDK 嵌入的通用 assistant）。

---

## 4. 工具目录（8 个，loop 无关，A/B 路线共用）

设计原则（来自 §1）：原子、可组合、描述详尽（何时用/何时**不**用）、幂等、
错误回灌不中断、自带 guardrail、结果高信号。

统一约定：

- 所有工具返回统一 envelope：`{"ok": true, "data": {...}}` 或
  `{"ok": false, "error": "人话描述", "is_retryable": bool, "details": {...}}`。
  **失败永远是 tool result，不是异常**——模型自己决定重试/换路，loop 永不因工具失败退出。
- 所有工具通过 `opendraft tool <name> --args '<json>'` 暴露（CLI 即契约，进程边界即隔离）；
  pi 扩展（TS，~150 行）只做 registerTool + spawn + 透传 envelope。
- 全部读写以 `opendraft_output/` 为根，路径参数不得越出该目录（Driver 校验）。

### T1 `read_artifact` — 读（探索与复核原语）

```
参数: {path: string, offset?: int, limit?: int}   # limit 默认 ~4000 字符（ACI 截断原则）
```

- 读 `research/papers/*.md`、`research/combined_research.md`、`research/research_gaps.md`、
  `drafts/00_formatted_outline.md`、`research/bibliography.json`、`drafts/citation_summary.md`、
  已写章节、各 `qa_*.md`。
- 对应 coding 的 Read+grep：**让 agent 自己导航研究语料，而不是把 3000 字符截断片段硬编码进 prompt**
  （现状：`compose.py:293-332` 的 user_input 拼装）。只读、无副作用、天然可并行。

### T2 `write_section` — 写（核心创作原语）

```
参数: {section: enum[introduction|literature_review|methodology|results|discussion|conclusion|appendices|custom],
       content: string,                       # 全文量、幂等覆盖写
       citations_used: string[]}              # 本节引用的 cite_XXX 列表
```

- **幂等全量写**（同输入同结果），落盘 `drafts/NN_<name>.md` + 更新 `checkpoint.json` 中对应字段。
  Aider 教训：写作场景全文重写是常态，diff 原语意义有限；但要宽容——content 允许只给"替换段"
  时退化为 search/replace（唯一文本匹配，不用行号）。
- **Guardrails（写前 linter，SWE-agent 原则）**，不过则拒绝并回灌具体违规项：
  1. 反懒惰：字数 ≥ 该节 `word_targets` 的 70%；无 `[expand]`/`TBD`/占位符（对应 NEW_ISSUES_DEC2025 三个 ticket）；
  2. **引用白名单**：`citations_used` 必须是 `bibliography.json` 里已存在的 ID——
     从结构上消灭幻觉引用（现状 LLM 兜底生成引文已被禁用，`agent_runner.py:931`，
     但写作模型仍可能编造 cite_XXX，此处兜底）；
  3. `{cite_MISSING:...}` 检出：放行但标记，交给 T7 补研而非留在正文。
- 每次写入前自动留版本快照 `drafts/.snapshots/NN_<name>__<ts>.md`（Aider 的 git 安全网等价物）。

### T3 `search_literature` — 文献检索（语义正确的领域原语）

```
参数: {query: string, min_results?: int=5, fields?: [...]}
返回: {new_citations: [{id, title, authors, year, abstract?, url}], total_in_db: int}
```

- 薄封装 `CitationResearcher.research_citation`（`engine/utils/api_citations/orchestrator.py:319`，
  现成多供应商级联 + QueryRouter + 并行）。
- **副作用：新引文追加进 `bibliography.json` 并重新生成 `citation_summary.md`**（经
  `add_citations_batch` 去重，`citation_database.py:487`），分配新 cite_ID——
  这样 T2 的白名单立即生效，"检索→可引用"闭环成立。
- 明确不做：LLM 凭空生成引文（维持现状禁用决策）。

### T4 `verify_claims` — 事实核查

```
参数: {claims: [{claim, section?, line?}], max_workers?: int=10}
返回: {verdicts: [{claim, verdict: SUPPORTED|CONTRADICTED|INSUFFICIENT, confidence,
                   wrong_part?, correct_value?, evidence_snippet, source_url?}]}
```

- 薄封装 `FactCheckVerifier.verify_claims`（`engine/utils/factcheck_verifier.py:164`，
  现成 web-grounded 取证 + judge + wrong_part/correct_value 对）。
- 只读、可并行（一次核查多条 claim）。`wrong_part/correct_value` 就是喂给 T6 的结构化修订材料——
  "核查→修订"工具链的数据形状是现成的，缺的只是胶水。

### T5 `score_draft` — 质量评分（gate 原语）

```
参数: {scope: enum[section|full], section?: enum, strict?: bool=false}
返回: {total: int/100, breakdown: {word_count, citations, completeness, structure},
       issues: [{section, metric, actual, target, severity}], passed: bool}
```

- **需要重构**：现有 `run_quality_gate(ctx)`（`engine/utils/quality_gate.py:228`）吃整个
  DraftContext、且 `issues` 是非结构化字符串列表（如 `"Body short: 3200 words (target: 10000)"`）。
  重构为吃文本 + 返回结构化 issues——这是本方案对现有代码的唯一硬性改造（改动面小：
  四个 `_score_*` 函数抽成纯函数即可）。
- 只读、幂等、无副作用，gate 前后任意次调用安全。

### T6 `revise_section` — 定向修订

```
参数: {section: enum, instructions: string, find_replace?: [{find, replace}]}
```

- 薄封装 `call_gemini_revise`（`engine/utils/revise.py:89`，现成熔断器/circuit breaker）。
- 优先消费 T4 的 `wrong_part/correct_value`（find_replace 直通），其次自由指令。
- 修订后自动重新落盘 + 快照；可选链式自动重打分（T5）由模型决定，不在工具内硬编码。

### T7 `compile_draft` — 编译导出（确定性，无 LLM）

```
参数: {format: enum[md|pdf|docx|all], fix_missing_citations?: bool=true}
```

- 包装现有 compile phase（`engine/phases/compile.py`），含 `{cite_MISSING}` 自动补研
  （`citation_compiler.py:55`）。
- **顺手修已知 bug**：`generate_reference_list` 在 `compile_citations` 之前调用
  （`engine/phases/compile.py:342-344` 附近），补研到的新引文不进参考文献列表——
  顺序应调换。
- 确定性、幂等（覆盖 exports/）。

### T8 `finish` — 交付前自评（loop 的终点协议）

```
参数: {final_scores: 来自 T5, qa_reports_read: string[], notes?: string}
```

- 语义终点：agent 声明完成。Driver 在此做最终验收（见 §5）。
- 防"分数不够也交差"：Driver 校验最后一次 T5 全篇 ≥ 阈值 + FactCheck 无未处理 CONTRADICTED，
  否则拒绝 finish 并回灌差距清单（is_error 语义）。

### 工具 → 现有代码映射

| 工具 | 封装来源 | 需新建 |
|---|---|---|
| T1 read_artifact | `draft_output` 布局已是契约（`orchestration.py:243-272` 的 `_planned_artifacts`） | 路径校验+截断 |
| T2 write_section | compose 各 `_write_X`（`compose.py:212-901`）的落盘语义 | guardrails、快照、checkpoint 同步 |
| T3 search_literature | `CitationResearcher` + `add_citations_batch` | CLI 包装 + summary 重建 |
| T4 verify_claims | `FactCheckVerifier.verify_claims` | CLI 包装 |
| T5 score_draft | `quality_gate.py` 四个 `_score_*` | **重构：去 DraftContext 依赖 + 结构化 issues** |
| T6 revise_section | `call_gemini_revise` | CLI 包装 + find_replace 直通 |
| T7 compile_draft | compile phase + `CitationCompiler` | 修补研顺序 bug |
| T8 finish | — | Driver 验收逻辑 |

---

## 5. Agent Loop 与质量门闭环

### 5.1 写章节标准循环（evaluator-optimizer）

```
pi session（每节一个，或全篇一个按 token 预算决定）：
  AGENTS.md（论文地图，见 §7）+ 该节任务（SectionSpec：target_words、required_subsections、
  specific_citations、writing_style_hint —— ResearchBrief 已支持，research_brief.py:27-77）

  loop:
    模型自由组合工具：read_artifact（读研究笔记/邻节尾部/outline）→
                     search_literature（补证据）→
                     write_section（写）→
                     score_draft（自查）→
                     revise_section / search_literature（针对 issues 补救）→
                     verify_claims（关键 claim 抽查——自验证涌现）→ …
    直至模型调 finish
```

关键点：**补救策略由模型选**（evaluator-optimizer），harness 只给结构化反馈和边界。
`score_draft` 返回的 `issues: [{section, metric, actual, target}]` 就是模型的"编译器报错"——
"citations 不足 → 去 search_literature"、"completeness 缺子节 → revise_section 补"是它能自己做的推理。

### 5.2 Driver 验收与熔断（Python，`engine/harness/driver.py`）

pi **没有 max-steps**，所以以下由 Driver 承担（RPC 轮询，5-10s 间隔）：

| 熔断条件 | 动作 |
|---|---|
| `get_session_stats.cost` > 本节/全篇预算 | 发 steer 消息要求收尾 → 超时则 terminate + 用最近快照 |
| turns > N（默认 40/节） | 同上 |
| wall clock > 预算 | 同上 |
| finish 时最终 gate 不达标 | 拒绝 finish（回灌差距清单），给 2 次机会后升级人工/终止 |
| tool envelope 连续 is_retryable 失败 > 5 | 终止本节，保留快照，标记 FAILED |

验收通过即更新 `checkpoint.json`（复用 `save_checkpoint`，`utils/checkpoint.py:44`），
pipeline 状态机天然续接——**旧流水线的 resume 体系与新 harness 不冲突**。

### 5.3 与现有 pipeline 的关系

- Phase 1-2（research/structure）：第一期仍跑确定性流水线（质量稳定、便宜），产物就是
  agent 的初始环境（research/ + outline + bibliography）。
- Phase 3-3.5（compose/validate）：被 agent loop 取代——这是本次改造的核心收益区
  （现状 gate 开环、QA advisory、写完就交货，见质量门调查报告）。
- Phase 4-5（compile/export）：保留为 T7，agent 显式调用。
- 远期：research 也可 agent 化（deep research planner 已是"LLM 规划→代码执行"的种子，
  `engine/utils/deep_research.py:66`），届时 outline 也能在写作中被 agent 修订——但不一期做。

---

## 6. pi 扩展与 Driver 骨架（设计级伪代码）

pi 扩展（`.pi/extensions/opendraft-tools.ts`，~150 行）：

```typescript
// 每个工具 = registerTool + spawn("opendraft", ["tool", name, "--args", json])
pi.registerTool({
  name: "search_literature",
  description: "Search academic databases (Semantic Scholar/Crossref/OpenAlex...) and add
                found papers to the citation database. Newly found citations get cite_XXX ids
                and become citable by write_section. Use BEFORE citing, never invent citations.",
  parameters: Type.Object({ query: Type.String({ minLength: 3 }),
                            min_results: Type.Optional(Type.Integer({ default: 5 })) }),
  execute: async (args) => runOpenDraftTool("search_literature", args),   // spawn + parse envelope
  promptGuidelines: "Prefer specific queries with author/year/keyword. If results are thin,
                     rephrase rather than lowering min_results.",
});
// 同模式注册 write_section / score_draft / verify_claims / revise_section / read_artifact
// （execute 均是无状态 subprocess.run —— mini-swe-agent 原则，稳定性之本）
```

Driver（`engine/harness/driver.py`，核心 ~200 行）：

```python
proc = subprocess.Popen(["pi", "--mode", "rpc", "--tools", "read,grep,find,ls",
                         "--append-system-prompt", system_prompt_path],
                        stdin=PIPE, stdout=PIPE, text=True)
send({"type": "prompt", "text": section_task})          # 每节一个 prompt
for line in proc.stdout:                                 # JSONL 事件流
    ev = json.loads(line)
    if ev["type"] == "tool_execution_start": journal.log(ev)     # run_journal
    if ev["type"] == "message_update": track_usage(ev)
    if budget.exceeded(): steer_or_terminate(proc); break
    if ev["type"] == "agent_settled": finalize(ev); break
stats = rpc("get_session_stats")                         # tokens/cost/contextUsage
save_checkpoint(ctx, "compose", output_root)
```

要点：系统提示注入用 `SYSTEM.md`/`--append-system-prompt`（论文写作指令 + 工具使用纪律）；
项目规则用 `AGENTS.md`（论文地图）；`--tools` 白名单默认**不含 bash**——agent 无 shell，
风险面收至极小（pandoc 等需要时由 T7 在 Python 侧做）。

---

## 7. 上下文工程

1. **AGENTS.md = 论文版 repo map**（Aider 原则）：每次 session 启动自动生成为
   `opendraft_output/AGENTS.md`——topic、outline 摘要（每节 2 行）、bibliography 统计
   （N 条，按主题分桶）、已写章节清单 + 各自字数/得分、写作纪律（引用规则、字数目标、
   语言）、**工具使用协议**（"检索先于引用"、"写完必自查打分"）。≤1k token。
2. **按需注入，拒绝 upfront 灌满**（learn-claude-code s05 原则）：现状 compose 把
   scribe_output 截 3000 字符 + formatter_output 截 2000 字符硬塞每节 prompt
   （`compose.py:293-332`）；改为 agent 用 read_artifact 自己取——它更清楚自己这节需要什么。
3. **compaction 定制**（pi 支持扩展接管）：摘要时保 outline + bibliography 主题桶 +
   已写章节首尾各 500 字，其余可压。中间产物全部在盘上（这正是 OpenDraft checkpoint
   体系的价值——**文件系统是 source of truth，context 只是缓存**）。
4. **session 切分**：每节一个 session（线性历史最稳、最易调试、compaction 风险最小），
   节间靠 AGENTS.md + 文件续接；全篇终审一个短 session（读 qa 报告 + 全局 score + finish）。
5. 工具结果截断（ACI 原则）：read_artifact 默认 ~4k 字符/次；search_literature 返回
   摘要 + ID，摘要全文用 read_artifact 取。

---

## 8. 权限与安全

| 面 | 措施 |
|---|---|
| shell 执行 | 默认无 bash 工具；需要时 `--tools read,...,bash` + `pi.on("tool_call")` 拦截白名单（只允许 pandoc/wordcount 等） |
| 幻觉引用 | T2 引用白名单 guardrail（结构性方案，不靠 prompt 祈祷） |
| 用户内容覆盖 | write_section 每次写前快照 `drafts/.snapshots/`；自定义节覆盖需 finish 级确认 |
| 无人值守 | `--no-approve` + `defaultProjectTrust: "never"` + 官方建议容器化（长任务上 WSL/Linux 容器，pi 的 Windows issue 较多） |
| 成本失控 | Driver 预算熔断（§5.2）——pi 无 max-steps，这是**必须**不是可选 |
| 供应链 | pi 周更：锁版本（`pi update` 手动），CI 钉版本号 |

---

## 9. 记忆与 RSI 种子（明确边界的一期实现）

一期只做"可回看的记忆"，不做自我改写：

1. **run_journal.jsonl**（Driver 写，append-only）：每节 {section, tool_calls 序列,
   score 轨迹, issues 历史, 最终分, 耗时/token/cost}。一次 run 就是一条完整 trajectory——
   mini-swe-agent 原则：trajectory 即调试日志即训练数据。
2. **run 后蒸馏**：脚本把 journal 与最终 qa 报告对照，生成 `lessons.md`（如
   "methodology 节两次因 citation density 不足返工 → 该节写作前先检索 ≥8 篇"），
   人工确认后并入 `templates/lessons/`。
3. **下次注入**：同类 topic/venue 的 lessons 在生成 AGENTS.md 时注入——**跨 run 的
   经验闭环成立，但每处改进都有人审**。
4. 明确不做（v1）：agent 自改 prompt、自动注册新工具、跨 run 自动调参。这些是 RSI
  的后续档位，先有可靠的 journal 数据再谈自动化。

---

## 10. 迁移路线图

| 里程碑 | 内容 | 验收标准 | 估时 |
|---|---|---|---|
| **M0 工具化**（与底座选型无关，先行） | ① `opendraft tool` 子命令框架 + T1-T7 CLI 包装；② quality_gate 重构（去 ctx 依赖、结构化 issues）；③ 修 compile 补研顺序 bug；④ 补 3 个 Critical ticket 的回归测试 | 每个工具可 CLI 独立调用；`tests/test_tools_*.py` 契约测试过 | 3-5 天 |
| **M1 PoC** | pi RPC 驱动 + T2/T3/T5 三个工具 + 单节写作闭环（AGENTS.md 自动生成） | 给一个 topic + 已有 research/，agent 自主写出 literature_review 节，score_draft ≥ 70，全程无人工干预，预算熔断生效 | 1-2 天（M0 后） |
| **M2 写作全 agent 化** | 全部 8 工具接线；compose+validate 被 loop 取代；快照/验收/finish 协议 | 3 个不同 topic 端到端：最终 quality ≥75、FactCheck 无未处理 CONTRADICTED、{cite_MISSING} 为 0；同等质量下 token ≤ 旧流水线 120% | 1-2 周 |
| **M3 核查-修订闭环强化** | T4→T6 链路、verify 抽查涌现行为、forbidden_claims 接入 finish 验收 | CONTRADICTED claim 100% 被处理（修订或删除有据可查） | **完成（2026-09-12）** |
| **M4 记忆与评估** | run_journal + lessons 蒸馏 + eval suite（N topic 黄金集，分数阈值门禁） | eval suite 进 CI；两次 run 可见 lessons 注入效果 | **完成（2026-09-12）** |

**回退策略**：M1 验收若暴露 pi 硬伤（长任务稳定性/Windows/compaction），工具层平移给
自研 Python loop（B 路线），M0 投入全部保留。

---

## 11. 测试与评估

- **工具契约测试**（每个工具）：schema 校验、路径越界拒绝、guardrail 触发案例
  （假 cite_XXX / 占位符 / 字数不足）、幂等性（同输入两次执行结果一致）、envelope 错误模型。
- **loop 测试**（mock）：mock pi RPC 事件流 + 录制的 model 响应，验证 Driver 的
  预算熔断、finish 拒绝、快照恢复。
- **黄金样本回归**：固定 2-3 个 topic 的 research/ 快照，对比新旧 compose 产出分数。
- **eval suite**：≥10 topic × （质量分、FactCheck 清洁率、引用真实率、token 成本、
  返工轮数）五维指标，进 CI 门禁。
- **防回归**：NEW_ISSUES_DEC2025 三个 ticket（规划泄漏/元数据泄漏/cite_MISSING）的
  检测器进 T2 guardrail。

---

## 12. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| pi 无 max-steps，跑飞烧 token | Driver 预算熔断（§5.2）+ 每节预算独立 |
| pi compaction 丢写作细节 | 产物全落盘 + 定制 compaction 保 outline/bibliography（§7.3） |
| pi Windows 适配差 | Driver/长任务跑 WSL 或容器；CI 用 Linux |
| `gemini-3-pro-preview` 不在 pi 模型目录 | models.json 自定义条目，或迁 gemini-3.1-pro-preview |
| pi 周更 churn | 锁版本 + 升级走 eval suite 回归 |
| TS/Python 双边调试成本 | 契约 = CLI envelope，单侧可独立测 |
| 写作质量不可硬验证 | gate 阈值 + 人工抽检 + eval suite 趋势，不承诺一次到位 |
| 现有 19 个 prompt 的处置 | Crafter 类 prompt 转为 SYSTEM.md 写作纪律 + AGENTS.md；Skeptic/Verifier/Referee 等 6 个未接线 agent 作为 T4/T5 的评测风格 prompt 来源，逐步并入 |

---

## 附录 A：现有代码关键锚点（调查核实）

| 事实 | 位置 |
|---|---|
| run_agent 单次调用、无消息历史、function_call 抛异常 | `engine/utils/agent_runner.py:193, 242, 269, 317-336` |
| 全仓库无人传 validators，skip_validation=True 默认 | `engine/opendraft/cli.py:662,1283` |
| Gemini wrapper config 透传，加 tools 参数 ~5 行 | `engine/utils/gemini_client.py:99-126` |
| OpenAI/Groq wrapper 已预留 Part(function_call=) 字段 | `engine/utils/openai_client.py:44-48` |
| CitationResearcher 多供应商级联 | `engine/utils/api_citations/orchestrator.py:319-587` |
| FactCheckVerifier.verify_claims + wrong_part/correct_value | `engine/utils/factcheck_verifier.py:164, 320-328` |
| quality_gate 耦合 DraftContext、issues 非结构化 | `engine/utils/quality_gate.py:15-24, 228` |
| call_gemini_revise 文本进出 + circuit breaker | `engine/utils/revise.py:89` |
| {cite_MISSING} 自动补研 | `engine/utils/citation_compiler.py:55-183` |
| compile 补研顺序 bug（补研结果不进参考文献列表） | `engine/phases/compile.py:342-344` |
| phase-as-tool API 已写码未接线、未提交 | `engine/orchestration.py`, `engine/protocols.py`, `engine/schemas.py`（git dirty） |
| checkpoint/resume 体系 | `engine/utils/checkpoint.py:44,130,151,230` |
| ResearchBrief/SectionSpec 已支持 per-section 规格 | `engine/research_brief.py:27-77` |
| 6 个验证 agent 有 prompt 未接线 | `engine/prompts/04_validate/`, `engine/prompts/05_refine/` |

## 附录 B：主要调研来源

- pi：github.com/earendil-works/pi（文档：usage/extensions/rpc/json/compaction/security/containerization.md）、pi.dev、mariozechner.at/posts/2025-11-30-pi-coding-agent
- 极简 harness：github.com/SWE-agent/mini-swe-agent、vercel.com/blog/we-removed-80-percent-of-our-agents-tools、vercel.com/blog/testing-if-bash-is-all-you-need
- Claude Code：arxiv.org/abs/2604.14228（Dive into Claude Code）
- Aider：aider.chat/docs/unified-diffs.html、aider.chat/docs/repomap.html
- 模式：anthropic.com/engineering/building-effective-agents（evaluator-optimizer）、Anthropic tool-use 文档（并行调用/错误回灌）

---

## 附录 C：M1 进度快照（2026-02 收工）

**M0 已完成**：7 工具 + `opendraft tool` CLI + quality_gate 结构化重构 + compile 补研 bug 修复，
64 个新契约测试，全量 595 passed（6 个失败为既有环境问题，与改动无关，已在干净 HEAD 对照确认）。

**M1 已探明的事实**：
- pi 0.84.3 本机可用：`E:\npm-global\pi.cmd`（Git Bash 的 PATH 不传给 .venv 子进程，Driver 必须用绝对路径）
- pi 目录确认有原生 MiniMax provider（`minimax.json`，模型含 `MiniMax-M3`，anthropic-messages API，
  期望 env：`MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY`）
- **key 策略**：`engine/.env` 的 `OPENAI_API_KEY`（len=125）+ `OPENAI_BASE_URL=https://api.minimaxi.com/v1`
  —— 即 MiniMax 的 OpenAI 兼容端点。Driver 注入 pi 环境时把该值映射为 `MINIMAX_API_KEY`，
  PoC 模型用 `minimax/MiniMax-M3`。若 anthropic-messages 端点不认该 key，回退方案：
  models.json 自定义 openai 兼容条目指回 api.minimaxi.com/v1。
- pi 原生 MiniMax 目录还有 `minimax-cn`（国内端点，需 `MINIMAX_CN_API_KEY`）
- 冒烟脚本缺陷（明日修）：`stdout.readline()` 无输出时会无限阻塞，deadline 检查失效——
  需改用轮询/select 或独立计时线程强杀；另 Python stdout 管道模式要 `flush=True`

**M1 已写代码**：`engine/harness/__init__.py`、`engine/harness/paper_map.py`（AGENTS.md 论文地图生成器，纯读离线）。
pi 扩展、Driver、PoC 运行、离线测试：明日继续。

**M1 PoC 结果（2026-09-12）**：PASS。MiniMax M3 自主写 literature_review：1233 词、12/12 引用、
49 次工具调用、$0.087、215 秒、5 项验收全绿（scripts/run_poc.py 可重跑）。
三个真实发现及修复：① prompt 不强制自查回路——agent 用 bash/grep 手工复刻了 score_draft 的检查
而从未调用 score_draft/revise_section → Driver 侧增加 finalize 验收（重同步 checkpoint + score_draft）；
② `--tools` allowlist 实际没拦住 bash（pi 语义如此）→ 增加 `--exclude-tools bash,powershell`；
③ pi 原生 write 可绕过 guardrail 与 checkpoint 同步 → finalize 时按磁盘重同步。
注意：工具面 JSON 契约写死"失败返回 envelope"，pi 侧 isError 只能 throw（extensions.md 明确）。

**M2 PoC 结果（2026-09-12）**：全局 review 实战 PASS。素材 = PoC 真实 agent 写的文献综述 + 手写引言/方法学
（故意埋 3 处跨节矛盾）。review 会话（$0.012，5 turns）产出 8 条 global_issues：
3/3 预埋全捕获（引言承诺迭代检索 vs 方法学 single-shot；NRQA 术语未引入即使用；BM25-exclusive vs
文献综述密集检索结论矛盾），另 5 条涌现发现（outline 承诺 Contriever 未交付、文献综述引用链格式风险、
"问题命名但系统不解决"的悬置叙述、引用与主张不匹配、指标命名不一致）。
scripts/review_demo_setup.py 可复现。非线性把控闭环：review → global_issues.md → 定向 revise。

**M2 收官结果（2026-09-12）**：`harness paper` 端到端 PASS——6/6 节（MiniMax M3 自主写作）、
review 7 issues、5 个 fix 会话全部修复、full score 100、总成本 $0.51、零 warning。
run_paper 编排器：plan→逐节(resume 跳过已 passed)→review→parse global_issues 按 scope 定向 fix→
full 验收→可选 compile；PaperBudget envelope（阶段子预算+总预算，耗尽跳过并警告）。
两个 driver 修复（均有回归测试）：queue.Empty 超时 tick 曾杀死静默>5s 的会话（抽为
_queue_line_source）；OPENDRAFT_BIN 确定性解析（当时指向仓库 shim，见下）。

---

## 附录 C1：Windows spawn 定案（2026-09-12，取代附录 C 的"已知回归"）

实测推翻了两个直觉修法：`.cmd` 用 `shell:false` 触发 Node 的 CVE-2024-27980 EINVAL；
用 `shell:true` 则 Node 把 argv 无引号拼接（DEP0190），cmd.exe 又剥反斜杠、把 JSON
引号当切换符，三层引用规则（CreateProcess / cmd / C 运行时）互相冲突——**任何经过
cmd.exe 的路径都无法可靠转发 JSON 参数**。定案：扩展一律无 shell spawn；Windows 下
`.cmd/.bat` 直接报错并给出指引。驱动解析顺序：`opendraft.exe`（pip 控制台脚本）→
venv `python.exe` + `OPENDRAFT_BOOTSTRAP`（`-c` 引导片段）→ PATH。真实 pi 会话验证：
`read_artifact` tool_execution_start/end 均 ok。

---

## 附录 D：M3 / M4 收官（2026-09-12）

**M3 核查–修订闭环**

- T4→T6：`verify_claims` / `manage_claims action=verify` 对 CONTRADICTED 返回 `find_replace`
  （`wrong_part` → `correct_value`），直接喂给 `revise_section`。
- 主张账本 `manage_claims`：`record` / `list` / `verify` / `resolve`。`resolve` 证据检查——
  `deleted` 要求主张原文已不在节内，`revised` 要求 `wrong_part`（或主张原文）已消失。
- Driver finish 门（`harness/acceptance.py`，T8 语义）：未处理 CONTRADICTED、
  `research_brief.forbidden_claims` 关键词命中、未知 `cite_XXX`、残留 `{cite_MISSING}`
  一律拒绝。`run_paper` 与 `harness section` 都跑此门。
- `write_outline`（结构工具）+ 论文感知 compaction（`session_before_compact` MUST-PRESERVE
  AGENTS.md / ledger / section_status）仍保留。
- Windows 定案（见附录 C1）：无 shell spawn；`.cmd/.bat` 显式拒绝；
  `python.exe + OPENDRAFT_BOOTSTRAP` 或 `opendraft.exe`。

**M4 记忆与评估**

- `opendraft harness distill`：四条离线启发式（短稿拒绝、工具连续失败、指标残留、
  多轮 fix）写出 `lessons_proposed.md`，人工晋升到 `lessons/approved/` 或仓库
  `templates/lessons/`。
- 下次 run：`PiDriver.prepare()` 把 `templates/lessons/*.md` 种子进
  `<root>/lessons/approved/`（不覆盖已有文件），`paper_map` 注入 AGENTS.md
  `## Lessons learned`。
- Eval suite：`tests/eval_gold/{clean_mini,dirty_mini}` + `manifest.json` 五维指标
  （质量分、FactCheck 清洁率、引用真实率、token 成本、返工轮数）+ finish 门。
  `opendraft harness eval --root` 可单目录跑；CI `quality.yml` 跑
  `tests/test_eval_suite.py::test_eval_gold_set_gates`。
- 两次 run 注入：distill → 人工晋升 templates → 第二次 `seed_approved_lessons` +
  `write_paper_map` 可见 lesson（离线测试 `test_two_run_lessons_inject_into_second_paper_map`）。

