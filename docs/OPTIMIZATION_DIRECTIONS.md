# opendraft 优化方向文档

**作者**: Hermes Agent  
**日期**: 2026-09-11  
**触发场景**: 用户用友念智能的"近距离感知 → 社会互动"论文框架评估 opendraft，发现当前接口无法表达完整研究意图

---

## 0. 现状摘要

opendraft 当前 `generate_draft()` 主入口只暴露一个必填参数 `topic`，其他全部默认。**用户只能输入论文标题，研究规划、方法选择、baseline、ablation、假设等"研究意图"完全由 opendraft 内部自动决定**。

这一设计哲学对"探索性写作"友好，但对"已有完整研究计划的论文写作"是根本性约束——**用户的研究输入颗粒度和 opendraft 的处理颗粒度不匹配**。

## 1. 用户侧能力 vs opendraft 当前能力（对比表）

下表对照"完整研究论文写作需要的用户输入"与"opendraft 当前接口/实现能接收的输入"：

| 用户研究意图输入 | opendraft 当前支持 | 缺口 | 触发示例 |
|---|---|---|---|
| **论文标题** | ✅ `topic` 必填 | 无 | "From Proximity to Interaction: Learning Social Dynamics from Spatiotemporal Human Trajectories" |
| **研究领域 / Focus** | ⚠️ `blurb` 可选（自由文本，会拼到 topic 后） | 不是结构化字段，仅作为 prompt 上下文 | 友念框架第 24 章"一句话研究定义"目前只能塞进 blurb |
| **语言** | ✅ `language` 默认 `"en"` | 无 | en-US / de / zh-CN |
| **学术层级** | ✅ `academic_level` 默认 `"master"` | 无 | bachelor / master / phd / research_paper |
| **引用样式** | ✅ `citation_style` 默认 `"apa"` | 无 | apa / ieee |
| **输出模式** | ✅ `output_type` 默认 `"full"` | expose/full 二元，缺中间档 | research_paper / thesis / survey |
| **目标 venue** | ❌ 不支持 | 用户必须在 BP/cover letter 后续自己处理 | 友念框架第 23.7 章目标 venue (ICWSM/WWW/KDD) 无法传达 |
| **核心 Research Question** | ❌ 不支持 | opendraft 自动从 topic 推断 | 友念框架第 2 章 RQ1（接近→互动语义）无法独立传入 |
| **研究假设 (H1..Hn)** | ❌ 不支持 | opendraft 自动推断 | 友念框架第 20 章 H1-H5 完全无法传入 |
| **任务定义 (Task 1/2/3)** | ❌ 不支持 | opendraft 用 hardcoded 6 section 模板 | 友念框架第 5/6/7 章 Interaction Recognition / Future Prediction / Group Formation 无法指定 |
| **方法框架 / 创新点** | ❌ 不支持 | opendraft 默认套 TGN/GAT/GraphSAGE | 友念框架第 12-14 章 3 个 innovation（Interaction State Transition / Local-Global Context / Social Persistence）无法传入 |
| **Baseline 列表** | ❌ 不支持 | opendraft 自动选 baseline | 友念框架第 15 章 4 组 baseline（Rule/ML/Seq/TG）无法指定 |
| **Ablation 维度** | ❌ 不支持 | opendraft 自动决定 ablation | 友念框架第 16 章 5 项 ablation（w/o Duration/Relative Motion/History/Global Context/Spatial Context）无法指定 |
| **评估指标** | ⚠️ 内部硬编码（Precision/Recall/F1/AUROC） | 不能指定 K=几、不能新增指标 | 友念框架第 17 章 Hits@K/MRR/AUPRC 优先级无法传达 |
| **数据切分策略** | ❌ 不支持 | opendraft 默认随机拆 | 友念框架第 18 章 Temporal/User/Scene split 重要性无法传达 |
| **Ground Truth 协议** | ❌ 不支持 | 完全自动 | 友念框架第 19 章 8 种 GT 来源（碰一碰/组队/问卷/视频）无法指定 |
| **已知限制 / 不要 claim 的清单** | ❌ 不支持 | opendraft 不展示边界 | 友念框架第 21 章 5 项"不要 claim"无法告知 |
| **AutoReach 文献检索问题清单** | ⚠️ `topic` + `blurb` 间接传 | 结构化检索字段缺失 | 友念框架第 22 章 7 大类检索问题只能散在 blurb |
| **Author/Advisor/Institution** | ✅ 全可选 | 仅用于 cover page | — |

## 2. 当前实现里的"半自动化"接口（值得注意）

| 接口 | 当前行为 | 改进空间 |
|---|---|---|
| `topic` | 必填 | 应升级为结构化对象，可包含子字段 |
| `blurb` | 自由文本，会拼到 topic_context 后面 | 应升级为 `research_brief`（结构化 brief） |
| `resume_from` | checkpoint 路径，可断点续跑 | 已经是较成熟的"用户控制"接口，可作扩展参考 |
| `tracker` / `streamer` | 进度回调 | 已有事件订阅模式，可作扩展 API 参考 |

## 3. 优化方向（按优先级排序）

### 优先级 1：扩展 `generate_draft()` 签名，支持结构化研究输入

**改动位置**：`engine/draft_generator.py:486-530`

**新增参数**（建议命名）：
```python
def generate_draft(
    topic: str,
    # ... existing params ...
    research_brief: Optional[ResearchBrief] = None,  # 新增
    ...
)

@dataclass
class ResearchBrief:
    """结构化研究简报，覆盖完整研究意图"""
    title: str                              # 论文标题
    core_question: str                      # 一句话研究问题
    research_questions: List[str]           # RQ1, RQ2, ...
    hypotheses: List[Hypothesis]            # H1, H2, ...
    tasks: List[ResearchTask]               # Task 1: Recognition, ...
    innovations: List[str]                  # 创新点 1, 2, 3
    baselines: List[str]                    # baseline 列表
    ablation_dims: List[str]                # 消融维度
    metrics: List[MetricSpec]               # 评估指标
    split_strategy: str                     # temporal/user/scene
    ground_truth_protocol: str              # GT 来源
    negative_claims: List[str]              # 不要 claim 的清单
    venue_target: Optional[str]             # ICWSM/WWW/KDD/...
    additional_context: str                 # 自由文本补充
```

**实现要点**：
- `research_brief` 优先级高于 `topic` + `blurb`，若提供则完全替代之
- 若提供 brief，从 brief 直接生成 outline（不再走 LLM 推断 outline）
- 若 brief 与 `topic` 同时提供，brief 必须包含 `title`，否则抛错

**预期收益**：
- 用户可以精确控制论文的研究结构，避免 opendraft 自动选择"通用方法"（如套 TGN/GAT）
- 与你已写好的友念框架（4 份 md, 24 章, 1157 行）完美对齐

### 优先级 2：让 `phases/research.py` 接受结构化查询清单

**改动位置**：`engine/phases/research.py:35-45`

**当前代码**：
```python
topic_context = ctx.topic
if ctx.blurb:
    topic_context = f"{ctx.topic}\n\nFocus/Context: {ctx.blurb}"

queries = [
    f"{ctx.topic} fundamentals and background",
    f"{ctx.topic} current state of research",
    f"{ctx.topic} methodology and approaches",
    f"{ctx.topic} applications and case studies",
    f"{ctx.topic} challenges and limitations",
    f"{ctx.topic} future directions and implications",
]
```

**优化后**：
```python
queries = []

# 优先级 1: 用户提供的结构化查询（来自 research_brief.literature_search_questions）
if ctx.research_brief and ctx.research_brief.literature_search_questions:
    queries = ctx.research_brief.literature_search_questions
# 优先级 2: 用户提供的 blurb，扩展为查询
elif ctx.blurb:
    queries = derive_queries_from_blurb(ctx.blurb)
# 优先级 3: 默认 6 个查询
else:
    queries = DEFAULT_RESEARCH_QUERIES
```

**预期收益**：可执行友念框架第 22 章"AutoReach 需要重点检索的问题"（7 大类、20+ 个具体问题）

### 优先级 3：让 `phases/compose.py` 接受结构化 section 列表

**改动位置**：`engine/phases/compose.py`（6 个 chapter 函数）

**当前硬编码**：
- Chapter 1: Introduction
- Chapter 2.1: Literature Review
- Chapter 2.2: Methodology
- Chapter 2.3: Analysis & Results
- Chapter 2.4: Discussion
- Chapter 3: Conclusion

**优化方向**：
- 提供 `custom_outline: List[SectionSpec]` 参数
- 每个 `SectionSpec` 包含：title / target_words / required_subsections / specific_citations / writing_style_hint
- 若提供，compose phase 按用户指定顺序和字数写

**预期收益**：支持非标准论文结构（如系统论文的 "Threat Model / Design / Implementation"、EMNLP 的 "Task / Dataset / Method / Results"）

### 优先级 4：扩展 `phases/validate.py` 的 FactCheck 接受 GT 协议

**改动位置**：`engine/phases/validate.py` + `utils/factcheck_verifier.py`

**当前行为**：FactCheck 抽 claim → 搜网络 → LLM 比对 → SUPPORTED / CONTRADICTED / INSUFFICIENT

**优化方向**：
- 接受 `ground_truth_protocol: str`，告诉 verifier 哪些类型的 claim 是"硬 GT"必须 100% 验证
- 接受 `forbidden_claims: List[str]`，verify 这些 claim 不在论文中出现（如友念框架第 21 章的 5 项不要 claim）

**预期收益**：避免 LLM 写出"已验证 A/B 推荐效果"等无依据声明

### 优先级 5：扩展 prompts 模板体系

**改动位置**：`engine/prompts/03_compose/*.md` + `engine/prompts/04_validate/*.md`

**当前实现**：`prompts/03_compose/` 下有固定章节 prompt 模板

**优化方向**：
- 引入 `templates/{venue}/compose/` 目录（如 `templates/icwsm/compose/intro.md`）
- 引入 `templates/{academic_level}/` 层次（bachelor/master/phd/research_paper）
- 让 `academic_level="research_paper"` 自动选用最严格的 prompt 集

**预期收益**：不同 venue 审稿口味差异大（AAAI vs WWW vs KDD），模板可大幅提升相关性

### 优先级 6：用户决定 baseline + ablation 的 UI/API

**改动位置**：`utils/agent_runner.py` + 新增 `utils/research_plan.py`

**当前行为**：opendraft 自动选 baseline（如 logistic regression + TGN）

**优化方向**：
- 提供 `custom_baselines: List[BaselineSpec]`
- 提供 `custom_ablation: List[AblationSpec]`
- compose phase 写 Methods 时按用户列表生成 Methods 章节的 baseline / ablation 子节

**预期收益**：用户的 baseline 选择直接进入论文初稿，无需后期人工改 Methods

## 4. 不应优化的方向（明确边界）

| 项 | 不优化的理由 |
|---|---|
| 删掉 `topic` 必填 | 必须有一个标题字符串作为输入锚点 |
| 让 opendraft 接管所有研究意图 | 会失去"专家快速验证完整想法"的价值 |
| 把接口改成 GUI | 违背 opendraft 的 CLI 设计哲学 |
| 让 LLM 自动生成 Research Brief | 用户的研究判断不可被 LLM 替代（见友念框架第 8 章："哪些时空信号真正构成社会互动？这本身就具有研究价值"） |

## 5. 实施路径建议

| 阶段 | 工作量 | 依赖 |
|---|---|---|
| **阶段 1**：定义 `ResearchBrief` dataclass + `generate_draft()` 接受 brief 参数（仅解析，不消费） | 2-3 小时 | 无 |
| **阶段 2**：`phases/research.py` 接受 `literature_search_questions` | 1-2 小时 | 阶段 1 |
| **阶段 3**：`phases/compose.py` 接受 `custom_outline` | 4-6 小时 | 阶段 1 |
| **阶段 4**：`phases/validate.py` FactCheck 接受 GT protocol + forbidden_claims | 2-3 小时 | 阶段 1 |
| **阶段 5**：写 `templates/{venue}/` 目录（先做 ICWSM + WWW 两个） | 4-6 小时 | 无 |
| **阶段 6**：把友念智能框架作为第一个真实测试用例，跑端到端 | 1 小时 | 全部 |

**总计**：约 15-22 小时开发 + 1 小时测试。

## 6. 兼容性策略

- 所有新参数**默认 None**，不传则完全保持当前行为
- `topic` + `blurb` 路径继续可用
- `ResearchBrief` 路径是新增，不是替换
- 已有论文（expose 测试产物）继续可读

## 7. 验证指标

优化后应该能验证：

| 指标 | 优化前 | 优化后目标 |
|---|---|---|
| 用户研究意图传达完整度 | ~10%（仅 title） | ~90%+（含 RQ/Hypothesis/Task/Innovation） |
| Methods 章节与用户框架一致性 | 自动选择，可能不符 | 100% 来自 custom_outline |
| Baseline 数量 | 1-2 自动选 | 用户指定 N 个 |
| Ablation 维度 | 自动 2-3 项 | 用户指定 N 项 |
| Claim 边界把控 | 无 | 严格遵守 forbidden_claims |
| GT 协议可追溯性 | 无 | 论文中标注 GT 来源 |

## 8. 与友念智能论文框架的对应关系

友念智能的 4 份 md（`03_论文与研究/`）是**第一个真实使用场景**，可作为优化方向的 acceptance test：

| 友念框架章节 | 对应优化方向 |
|---|---|
| 02 章 核心科学问题 | 优先级 1: research_brief.core_question |
| 03-04 章 Spatiotemporal Interaction Events / Temporal Social Graph | 优先级 1: research_brief.innovations |
| 05-07 章 Task 1/2/3 | 优先级 1: research_brief.tasks |
| 12-14 章 Innovation 1/2/3 | 优先级 1: research_brief.innovations |
| 15 章 Baselines | 优先级 6: custom_baselines |
| 16 章 Ablation | 优先级 6: custom_ablation |
| 17 章 Evaluation 指标 | 优先级 1: research_brief.metrics |
| 18 章 Dataset Split | 优先级 1: research_brief.split_strategy |
| 19 章 Ground Truth | 优先级 4: ground_truth_protocol |
| 21 章 Don't claim 清单 | 优先级 4: forbidden_claims |
| 22 章 AutoReach 检索清单 | 优先级 2: literature_search_questions |
| 23 章 Venue Fit | 优先级 5: venue_target |

**所有友念框架的研究意图均可被 ResearchBrief 表达**——这是优化方向的核心论据。

---

## 9. 风险与限制

| 风险 | 缓解 |
|---|---|
| LLM 忽略 ResearchBrief 的某些字段 | 在 prompt 里加 strict instruction + 跑完做 post-check |
| 结构化 brief 写起来麻烦（用户嫌烦） | 提供 `brief.yaml` 模板 + 自动从 markdown 提取关键章节的解析器 |
| 新参数让 `generate_draft()` 签名膨胀 | 把 brief 聚合成单一 `research_brief` 参数，不增加参数数量 |
| 与 `topic` + `blurb` 路径冲突 | 明确 priority：brief > blurb > topic alone |

## 10. 结论

opendraft 的极简接口是**对探索性写作友好的设计**，但**对"专家已有完整研究计划"的场景是根本性约束**。

友念智能的论文框架（4 份 md, 24 章, 1157 行）展示了用户研究输入的真实复杂度——远超一个 `topic` 字符串能承载的信息量。

**优先级 1 的优化（结构化 `ResearchBrief`）能在不破坏现有接口的前提下**，让 opendraft 从"标题 → 论文"升级到"研究计划 → 论文"，这是该项目最有价值的下一步。

---

## 11. 优化方向 7: Agent 友好（架构层面）

**问题**：当前 `generate_draft()` 是"工具调用式 API"——一调到底、不可中断、不可观测、不可分步。这对**人类**够用（一站式跑完拿 PDF），对**自动化 agent / 多 agent 编排 / 上游 pipeline** 是糟糕的接口。

### 11.1 当前痛点 vs Agent 真实需求

| 维度 | 当前 opendraft | Agent 真实需求 |
|---|---|---|
| **可中断** | ❌ 一调 60 分钟无反馈 | ✅ 中途暂停 / 查询进度 / 决定继续 |
| **可观测** | ❌ 进度 print 到 stdout，agent 看不到 | ✅ 结构化事件流（callback / queue / SSE） |
| **可调整** | ❌ 跑起来后改不了任何东西 | ✅ 跑完 research 后 agent 看引用列表决定换 5 篇 |
| **可分步** | ❌ 7 个 phase 必须一口气跑完 | ✅ 暴露 `run_phase(name, ctx)`，agent 选跑哪几个 |
| **可恢复** | ✅ `resume_from=checkpoint.json`（已有，但未文档化） | ✅ checkpoint 显式成为一等公民 |
| **副作用边界** | ❌ 强制写 10+ 文件到 output_dir | ✅ `dry_run=True` 只产 JSON 不写文件 |
| **错误可恢复** | ⚠️ 单 phase 重试已有（`run_phase_with_retry`），但接口不对 agent 暴露 | ✅ phase-level exception 类型稳定 + agent 可决定"忽略 / 重试 / 终止" |
| **输出可解析** | ⚠️ PDF/DOCX 是给人看的 | ✅ 每 phase 产 JSON sidecar，agent 直接读 |
| **可组合** | ❌ 不能嵌入 multi-agent 工作流 | ✅ headless 模式 + 标准化 JSON contract |

### 11.2 当前实现的"半成品"骨架（值得复用）

代码层面，opendraft **已经有** agent 友好的部分组件，但**没规范化、没文档化、没暴露成 public API**：

| 组件 | 现状 | 改造方向 |
|---|---|---|
| `ctx.tracker` | 回调对象，5+ 方法（`log_activity` / `update_phase` / `mark_failed` / `set_local_progress_path` 等） | ✅ 契约文档化；提供 `ProgressTracker` Protocol；提供 `JSONFileTracker` / `StdoutTracker` 内置实现 |
| `ctx.streamer` | 回调对象，多个 `stream_*` 方法 | ✅ 同上，文档化 Protocol + 提供 `NullStreamer` |
| `save_checkpoint` / `load_checkpoint` | 写在 `engine/utils/checkpoint.py` | ✅ 暴露成 public：`opendraft.checkpoint.save(ctx, phase)` / `opendraft.checkpoint.load(path)` |
| `run_phase_with_retry` | 包在 main flow 里 | ✅ 拆出 `engine.orchestration.run_phase(name, ctx)` 作为 public API |
| phase 函数（`run_research_phase` 等） | 7 个，签名零散 | ✅ 标准化为 `(ctx: DraftContext) -> PhaseResult`，返回 dataclass 而不是 None |

### 11.3 改造方向（按子优先级排序）

#### 子优先级 7.1：Phase 作为一等公民（Phase as First-Class Object）

```python
# 当前
def run_research_phase(ctx: DraftContext) -> None:  # 副作用通过 ctx 传出
    ...

# 改造后
def run_research_phase(ctx: DraftContext) -> PhaseResult:
    """Returns structured result, not None"""
    return PhaseResult(
        phase="research",
        status="success",
        artifacts={"papers_md": [...], "citations_json": [...]},
        metrics={"queries_run": 60, "papers_found": 17},
        duration_seconds=312.5,
    )
```

**好处**：
- Agent 可以单独调用 `run_research_phase(ctx)`，拿到结构化结果
- 每个 phase 的副作用通过 `ctx` 累积，但返回值提供"本 phase 自己的产出"
- 错误返回 `PhaseResult(status="failed", error=...)`，不抛异常让 agent 决定怎么处理

#### 子优先级 7.2：暴露 Phase-level Public API

```python
# 新增: engine/orchestration.py
from enum import Enum

class PhaseName(str, Enum):
    RESEARCH = "research"
    STRUCTURE = "structure"
    CITATIONS = "citations"
    COMPOSE = "compose"
    VALIDATE = "validate"
    COMPILE = "compile"

def run_phase(
    phase: PhaseName,
    ctx: DraftContext,
    *,
    resume: bool = True,        # 自动从 checkpoint 恢复
    save_checkpoint: bool = True,
    on_event: Optional[Callable[[PhaseEvent], None]] = None,
) -> PhaseResult:
    """Run a single phase, with full agent-friendly controls."""

def run_pipeline(
    phases: List[PhaseName],  # agent 决定跑哪几个
    ctx: DraftContext,
    **kwargs,
) -> List[PhaseResult]:
    """Run selected phases in order."""

def get_pipeline_status(checkpoint_path: Path) -> PipelineStatus:
    """Inspect what's been done so far without running anything."""
    return PipelineStatus(
        completed_phases=[...],
        current_phase=None,
        total_elapsed_seconds=...,
        next_phase_to_run=PhaseName.STRUCTURE,
    )
```

**好处**：
- Agent 可以 `run_phase(PhaseName.RESEARCH)` 单独跑 research
- Agent 可以 `get_pipeline_status(checkpoint_path)` 不跑任何东西就查到状态
- Agent 可以决定"跳过 citations / 只跑 compose / 重新跑 validate"

#### 子优先级 7.3：标准化 ProgressTracker Protocol

```python
# 新增: engine/protocols.py
from typing import Protocol
from dataclasses import dataclass
from enum import Enum

class PhaseEventType(str, Enum):
    PHASE_STARTED = "phase_started"
    PHASE_PROGRESS = "phase_progress"  # 0-100
    PHASE_COMPLETED = "phase_completed"
    LLM_API_CALL = "llm_api_call"
    CITATION_FOUND = "citation_found"
    VALIDATION_ISSUE = "validation_issue"
    ERROR = "error"

@dataclass
class PhaseEvent:
    type: PhaseEventType
    phase: PhaseName
    timestamp: float
    progress_percent: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

class ProgressTracker(Protocol):
    def on_event(self, event: PhaseEvent) -> None: ...

# 内置实现
class JSONLineTracker:
    """Write each event as one JSON line to a file - perfect for agent consumption"""
    def __init__(self, path: Path): ...
    def on_event(self, event: PhaseEvent) -> None:
        with open(self.path, "a") as f:
            f.write(event.model_dump_json() + "\n")

class CallbackTracker:
    """Forward events to user-provided callback"""
    def __init__(self, callback: Callable[[PhaseEvent], None]): ...

class MultiTracker:
    """Fan out to multiple trackers (e.g., JSONLine + UI + Callback)"""
    def __init__(self, trackers: List[ProgressTracker]): ...
```

**好处**：
- Agent 拿到 `events.jsonl`，每行一个 JSON event，可以 `tail -f` 或 `jq` 查询
- 进度 / 错误 / LLM 调用 / 引用发现 全部结构化
- 不再需要解析 stdout

#### 子优先级 7.4：Headless + Dry-Run 模式

```python
def generate_draft(
    topic: str,
    # ... existing params ...
    headless: bool = False,         # 新增：禁用所有 print()，只输出 JSON
    dry_run: bool = False,          # 新增：不写任何文件，只返回 PhaseResult list
    events_path: Optional[Path] = None,  # 新增：事件流写到指定文件
    **kwargs,
):
    ...
```

**好处**：
- Agent 在 CI / Docker 里跑不需要关心 ANSI 颜色 / 进度条
- `dry_run=True` 让 agent 可以"试跑"看会调用哪些 API、预计多少 token，**不实际消耗 LLM 配额**
- `events_path` 让 agent 把事件流重定向到任何地方

#### 子优先级 7.5：MCP / OpenAI Function Calling Schema 导出

```python
# 新增: engine/schemas.py
def get_mcp_tools_schema() -> List[Dict]:
    """Export opendraft phases as MCP-compatible tool definitions"""
    return [
        {
            "name": "opendraft_run_research",
            "description": "Run literature research for a topic",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "language": {"type": "string", "default": "en"},
                    "max_papers": {"type": "integer", "default": 30},
                    "venue_filter": {"type": "string"},
                },
                "required": ["topic"]
            }
        },
        ...
    ]

def get_openai_function_schema() -> List[Dict]:
    """Same but in OpenAI function-calling format"""
    ...
```

**好处**：
- 任何支持 MCP 的 agent（Claude Desktop / Cursor / Hermes / OpenAI Agents）可以**直接把 opendraft 的 7 个 phase 当 7 个 tool 用**
- agent 可以："先 opendraft_run_research 拿到 papers.json，再让另一个 agent 决定换哪几篇，再 opendraft_run_compose 只写 intro"
- 这是真正的"agent 友好"——opendraft 成为 agent 工具箱的一员

### 11.4 验证场景：Hermes Agent 作为 opendraft 的上游

**最直接的真实验证**——把 opendraft 暴露成 Hermes 能调用的工具：

```
用户: "用 opendraft 写一篇关于 X 的论文，但只要 Related Work 和 Methods 两个 section"

Hermes Agent 编排:
  1. opendraft_run_research(topic="X") → 拿到 papers list
  2. [Hermes 自己] 评估 papers，丢弃低质量的 5 篇
  3. opendraft_run_compose(
       topic="X",
       sections=["related_work", "methodology"]
     ) → 拿到 2 个 md
  4. [Hermes 自己] 把 md 拼起来，输出最终结果
```

**当前 opendraft 做不到**——`generate_draft()` 一次性跑 7 个 phase，没有"只跑 compose 的两个 section"接口。

### 11.5 工作量估算

| 子优先级 | 工作量 | 依赖 |
|---|---|---|
| 7.1 PhaseResult dataclass | 2 小时 | 无 |
| 7.2 Phase-level public API | 4-6 小时 | 7.1 |
| 7.3 ProgressTracker Protocol | 3-4 小时 | 7.1 |
| 7.4 Headless + Dry-Run | 2 小时 | 7.1, 7.3 |
| 7.5 MCP / Function schema | 2-3 小时 | 7.2 |
| **总计** | **13-17 小时** | — |

### 11.6 与优先级 1 的协同

**两个优化方向可以并行做**：

- 优先级 1（ResearchBrief）改的是 opendraft **吃什么**（输入）
- 优先级 7（Agent 友好）改的是 opendraft **怎么被调用**（API 形态）

两者**没有依赖关系**——

- Agent 可以用 `generate_draft(research_brief=...)` 跑带完整研究计划的论文
- 人类也可以用 `topic` 跑极简论文
- 同一个 `generate_draft()` 函数两种用法都支持

**两个一起做能产生复合价值**：opendraft 从"CLI 工具"变成"agent 可组合的研究流水线组件"。

### 11.7 不要做的方向

| 项 | 不做的理由 |
|---|---|
| 强制所有 agent 必须通过 MCP 调用 | 保持 CLI 直接调用为 first-class |
| 在 opendraft 里内置 multi-agent 编排 | opendraft 应该当工具被编排，不应该编排别人 |
| 把 generate_draft() 改成 async | 当前 synchronous 简化模型，async 增加心智负担（agent 调用层处理） |
| 完全用 SDK（pip install opendraft）替换当前 monorepo 结构 | 部署形态改动太大，独立优化方向 |

---

## 12. 优先级总览（最终建议）

| 优先级 | 方向 | 工作量 | 与友念场景匹配度 |
|---|---|---|---|
| **1** | ResearchBrief 结构化输入 | 15-22h | 高（12 项映射） |
| **7** | Agent 友好（Phase-as-First-Class + MCP） | 13-17h | 中（让 Hermes 能编排 opendraft） |
| 2-6 | 各 phase 内部细化 | 15-20h | 高 |
| **1+7 并行** | **合计** | **28-39h** | **高** |

**建议实施顺序**：先 7.1+7.2+7.3（agent 友好的核心骨架），再 1（ResearchBrief），再做 7.4+7.5（headless + MCP 暴露）。这样每一步都有可验证的中间产出。

---

**附录 A**：完整 `generate_draft()` 当前签名见 `engine/draft_generator.py:486-530`  
**附录 B**：友念框架原文见 `D:/tmp/younian/04_友念智能/03_论文与研究/`  
**附录 C**：opendraft 当前 phase 实现见 `engine/phases/{research,structure,citations,compose,validate,compile}.py`  
**附录 D**：当前 tracker/streamer/checkpoint 半成品代码见 `engine/draft_generator.py:576-606`、`engine/phases/citations.py:78-79`、`engine/utils/checkpoint.py:20,99`
