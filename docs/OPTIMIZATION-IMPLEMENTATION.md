# opendraft 优化实施记录

**日期**: 2026-09-11
**对应规划**: [OPTIMIZATION_DIRECTIONS.md](OPTIMIZATION_DIRECTIONS.md) 全部 7 个方向
**状态**: 全部实施完成,86 个新单测通过,531 个旧测试回归无新增失败

---

## 实施总览

| 方向 | 状态 | 核心产出 |
|---|---|---|
| 优先级 1: ResearchBrief 结构化输入 | ✅ | `engine/research_brief.py` + `generate_draft(research_brief=..., brief_path=...)` |
| 优先级 2: 结构化检索查询 | ✅ | `phases/research.py: resolve_research_queries()` 三级优先(brief > blurb > 默认) |
| 优先级 3: custom_outline | ✅ | `phases/structure.py: outline_from_sections()`(跳过 LLM 大纲)+ compose 各章节按 spec 写作 + 自由章节 `_write_custom_sections` |
| 优先级 4: GT 协议 + forbidden_claims | ✅ | `phases/validate.py: match_forbidden_claims()` 关键词审计 + FactCheck 注入 GT 协议 |
| 优先级 5: venue 模板体系 | ✅ | `utils/venue_templates.py` + `templates/venues/{icwsm,www}/venue.md`(加 venue 只需加目录) |
| 优先级 6: baselines + ablation | ✅ | compose 的 Methodology/Results prompt 强制注入作者指定清单 |
| 7.1 PhaseResult | ✅ | `phases/results.py`,全部 6 个 phase 返回结构化结果 |
| 7.2 Phase-level API | ✅ | `engine/orchestration.py: run_phase / run_pipeline / get_pipeline_status / build_context` |
| 7.3 事件流协议 | ✅ | `engine/protocols.py: PhaseEvent / EventBus / JSONLineTracker / CallbackTracker / MultiTracker` |
| 7.4 headless + dry_run | ✅ | `generate_draft(headless=, dry_run=, events_path=)`;dry-run 零 LLM/零写盘返回计划 |
| 7.5 MCP/OpenAI schema | ✅ | `engine/schemas.py: get_mcp_tools_schema() / get_openai_function_schema()`(5 个工具) |

## 新公共 API 速览

```python
# 人类用法不变(generate_draft(topic=...) 原样可用)

# 专家用法:结构化研究简报(优先级:brief > blurb > topic)
from research_brief import ResearchBrief
brief = ResearchBrief.from_yaml("my_brief.yaml")   # 模板见 templates/brief.template.yaml
generate_draft(topic="...", research_brief=brief, venue_target="ICWSM")
# 或直接:generate_draft(topic="...", brief_path="my_brief.yaml")

# Agent 用法 1:分 phase 编排
from orchestration import PhaseName, build_context, run_phase, run_pipeline, get_pipeline_status
ctx = build_context(topic="...", research_brief=brief, output_dir=Path("out"))
result = run_phase(PhaseName.RESEARCH, ctx)        # PhaseResult(status/metrics/artifacts)
run_pipeline([PhaseName.STRUCTURE, PhaseName.COMPOSE], ctx)
get_pipeline_status(Path("out/checkpoint.json"))   # 不跑任何东西查进度

# Agent 用法 2:计划先行(dry-run,零 API 消耗)
plan = generate_draft(topic="...", research_brief=brief, dry_run=True)
# → List[PhaseResult],含每个 phase 的预计 LLM 调用数/产物路径/实际将用的检索查询

# Agent 用法 3:结构化事件流
generate_draft(topic="...", events_path=Path("events.jsonl"))
# → 每行一个 JSON 事件(phase_started/completed/failed、forbidden_claim_detected…)

# Agent 用法 4:把 opendraft 当 MCP 工具
from schemas import get_mcp_tools_schema
tools = get_mcp_tools_schema()   # opendraft_run_research / run_phase / get_pipeline_status /
                                 # generate_draft / list_venues
```

## 关键行为契约

1. **优先级**: `research_brief > blurb > topic`。brief.title 覆盖 topic;检索查询、大纲、写作 prompt、验证全部消费 brief 字段。
2. **custom_outline 直出大纲**: 给定 `custom_outline` / `brief.output_sections` 时,structure phase 跳过 Architect/Formatter 两次 LLM 调用,确定性渲染大纲;`role` 匹配标准槽位的 section 定制对应章节(字数/子节/风格),匹配不到的作为自由章节(如系统论文的 "Threat Model")单独写作并入正文。
3. **forbidden_claims 审计**: 纯文本关键词重叠匹配(≥60% 关键词命中且 ≥2 词),写 `qa_forbidden_claims.md`,发 `forbidden_claim_detected` 事件;不抛异常、不中断。
4. **dry_run**: 不建模型、不调 LLM、不写工作文件;research 计划里能看到将真实使用的检索查询列表。
5. **兼容性**: 所有新参数默认 None/False,不传时行为与改造前一致;旧 checkpoint(无新字段)可正常 restore。

## 新增/修改文件

**新增**: `engine/research_brief.py`、`engine/protocols.py`、`engine/orchestration.py`、`engine/schemas.py`、`engine/phases/results.py`、`engine/utils/venue_templates.py`、`templates/venues/{icwsm,www}/venue.md`、`templates/brief.template.yaml`(友念框架示例注释版)

**修改**: `engine/draft_generator.py`(签名 + dry-run/headless/events)、`engine/phases/{context,research,structure,compose,validate,citations}.py`(新字段消费 + PhaseResult)、`engine/utils/checkpoint.py`(brief/outline/venue 序列化,含 dict 健壮处理)

## 测试

新增 8 个测试文件 86 个用例(全部 mock,不碰 API):

- `tests/test_research_brief.py`(22)— 规范化/别名/roundtrip/prompt 渲染/role 推断
- `tests/test_protocols.py`(12)— 事件序列化/JSONL/回调/扇出容错
- `tests/test_research_queries.py`(7)— 三级查询优先
- `tests/test_outline_and_venues.py`(13)— 大纲直出/venue 块
- `tests/test_validate_forbidden.py`(7)— 禁止 claim 审计
- `tests/test_schemas_and_compose.py`(15)— schema 结构/compose 注入
- `tests/test_orchestration.py`(17)— run_phase/pipeline/status/dry-run/resume/失败不抛
- `tests/test_checkpoint_brief.py`(7)+ `tests/test_generate_draft_dry_run.py`(4)

**回归**: 全套 531 旧用例通过;6 个失败(`test_restore_folders_as_paths` 的 Windows 路径硬编码断言、`test_e2e_subprocess_kill.py` 5 个 mock 子进程的 sys.path 解析 bug)经 git worktree 在干净 HEAD 上复现,确认为**既有问题**,与本次改动无关。

## 下一步(未做)

- 用友念智能框架(`D:/tmp/younian/04_友念智能/03_论文与研究/`)跑一次真实端到端(规划文档"阶段 6",需 API 配额,约 20-30 分钟)
- Hermes 侧接线:把 `get_mcp_tools_schema()` 注册进 Hermes 的 MCP 工具清单
- 可选:修复两个既有测试 bug(e2e mock 的 `sys.path` 推导、checkpoint 的 POSIX 路径断言)
