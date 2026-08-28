# 否定约束与 Intent Override：私有评测稳健版实施规格（Lean v2）

> 面向执行者：Luna Max
>
> 基线分支：`dev`（文档编写时 HEAD：`c3f1b77`）
>
> 文档性质：可直接执行的代码规格
>
> 技术路线：纯规则、开放值复制、确定性状态更新；不使用 LLM，不新增第三方运行时依赖
>
> 优先级：本文件高于 `docs/state_tracking_optimization_plan.md`。不要把两份方案叠加实施。

## 0. 给执行代理的强制指令

本轮只解决两个基础语义问题：

1. 否定约束：`I don't want black` 必须把 `black` 记录为排除条件，并保证推荐结果不违反该条件。
2. Intent Override：`I don't want black, I want white` 或跨轮修改时，只更新相关属性，保留 category、budget、size 等无关状态。

必须保留当前 `dev` 已有的以下能力和构造关系：

- `InformationGainQuestionScorer`
- `CandidateCoverageManager`
- `HeuristicRanker(store, config)`
- `HeuristicPolicy(search_config, policy_config, store)`
- `Components.coverage_manager`
- `core/pipeline.py` 当前执行顺序

本轮禁止实施：

- 完整 Catalog Grounder 或从 50,000 商品构建属性值词典；
- LLM、Embedding、NER、CRF、BERT 或其他模型调用；
- provenance、confidence、reasoning trace、低置信度分级状态；
- 7 类以上的状态操作代数；
- 在 BM25、Structured、Hybrid、Ranker 中分别实现不同版本的排除规则；
- 调整 Information Gain priors、Coverage threshold、dynamic ranking 参数或 rarity 参数；
- 修改 `evaluator/`、`data/public_set.jsonl`、Catalog 或公开标签；
- 根据公开样本的 target ASIN、完整句子或特有值做硬编码；
- 顺手重构与本任务无关的 Policy、Ranking、Response 或 Agent 接口。

若实现需要超出上述范围，停止扩展，在交付报告中列为后续项，不要自行加入本轮代码。

## 1. 为什么采用 Lean v2

公开集只有 200 条，私有集有 800 条。最容易过拟合的是：

- 针对公开句式堆正则；
- 针对公开属性值扩充封闭词典；
- 用公开分数反复调整排序权重、稀有度和 Coverage 阈值；
- 多个模块各自维护一套属性值识别逻辑。

Lean v2 只保留能够跨模板、跨属性值泛化的结构性能力：

- 从原文复制 value span，不要求 value 必须在词典中；
- 将无限语言表达归约为 5 种确定性状态操作；
- 用前一轮 `pending_attribute`、显式属性词和已有状态完成 slot typing；
- 无法判断 slot 但否定意图明确时，落到 `other`，仍执行全商品文本排除；
- Planner 从当前有效状态构造查询，不直接拼接原始对话历史；
- 所有路线复用一个排除匹配实现；
- 用不变量测试和 OOV/模板变形测试作为主验收，而不是只看公开总分。

## 2. 成功标准

实现完成后必须同时满足以下不变量。

### 2.1 状态不变量

- 明确否定值必须进入 `SessionState.negative_slots`。
- 同一个 slot 下，同一个规范化 value 不得同时出现在 `active_slots` 和 `negative_slots`。
- `SET` 只替换对应 slot 的正向值，不清除其他 slot。
- `EXCLUDE` 添加负向值，并移除同 slot 下相同的正向值。
- `ALLOW` 只撤销对应负向值，不自动把它变成正向偏好。
- `REMOVE` 只撤销正向偏好，不把它变成负向偏好。
- `DONTCARE` 清空对应 slot 的正负值，并写入 `no_preference_attributes`。
- 普通 `actually`、`instead`、`ignore my earlier preference` 不得触发全局清空。
- `category`、`budget`、`size` 等无关约束必须在属性级 override 后保留。

### 2.2 查询不变量

- 负向 value 及其 cue 不得进入 `lexical_terms`。
- 负向 value 及其 cue 不得进入 `semantic_query`。
- 已被替换或撤销的旧正向 value 不得继续污染查询。
- `SearchPlan.excluded_terms` 必须是 `negative_slots` 的扁平、去重、稳定排序投影。

### 2.3 推荐不变量

- 任意最终推荐商品只要匹配明确负向 value，就必须被排除。
- 单词匹配必须有 token 边界：排除 `black` 不得误杀只包含 `blackberry` 的商品。
- 多词值必须可工作，例如 `merino wool`、`rose gold`、`machine washable`。
- Dense 路线未来即使启用，也必须被最终 Ranker guard 覆盖。

### 2.4 集成不变量

- 官方 `Agent.reset/respond` 接口和响应结构不变。
- 当前测试全部继续通过。
- Information Gain、Coverage、动态排序和 rarity 逻辑除必要兼容点外不改算法。
- 约束发生实质变化后，旧候选覆盖历史被重置。
- Coverage 的状态签名必须考虑正向、负向和 no-preference 状态。

## 3. 目标架构

```text
user_message + pending_attribute + previous SessionState
                         |
                         v
                 parse_turn()               纯解析，不修改 state
                         |
                         v
        ordered SlotOperation[SET | EXCLUDE | REMOVE | ALLOW | DONTCARE]
                         |
                         v
                 apply_operations()         唯一的状态归并规则
                         |
                         v
      active_slots + negative_slots + no_preference_attributes
                         |
              +----------+-----------+
              |                      |
              v                      v
       RuleQueryPlanner       coverage state signature
              |
              v
       existing retrieval routes
              |
              v
       shared exclusion matcher
              |
              v
       existing HeuristicRanker / Policy / Coverage / Response
```

关键原则：

- Parser 输出“这一轮发生了什么”，Reducer 决定“状态如何变化”。
- Parser 和 Reducer 都是确定性的，不访问 public ground truth。
- 只新增一个共享匹配实现；各调用点不得复制自己的 substring/regex 规则。
- Pipeline 不新增阶段，不改变现有组件构造参数。

## 4. 技术选型

| 子问题 | 本轮方案 | 不采用方案 | 原因 |
| --- | --- | --- | --- |
| 属性值识别 | 原文开放 span 复制 | 完整封闭值词典 | 私有值可能未出现在公开集或 seed lexicon |
| Slot typing | pending → 显式属性 → 旧状态 → 小型 seed → `other` | 全 Catalog Grounder | 降低复杂度并避免双重词汇体系 |
| 否定作用域 | clause 切分 + cue family + span 截断 | 全句检测 `not` | 能区分同句正负条件 |
| Override | 5 类 ordered operations | `actually` 后全量清空 | 保留无关约束 |
| 状态更新 | 纯函数式规则 + 单一 reducer | 直接在多个正则分支 mutation | 易验证状态不变量 |
| Query | 从 canonical state 重建 | 拼接全部原始消息 | 避免旧值和否定值污染 |
| 商品排除 | 一个共享 token matcher | 各模块独立 substring | 防止语义漂移和边界误判 |
| 模型 | 不使用 | LLM/小模型 | 当前问题可由结构化状态转换解决 |

## 5. 最小数据契约

### 5.1 `SessionState`

在 `shopping_copilot/core/contracts.py` 的 `SessionState` 只新增一个持久字段：

```python
negative_slots: dict[str, list[str]] = field(default_factory=dict)
```

现有字段继续保持：

- `active_slots`：当前正向约束；
- `excluded_terms`：兼容现有 Planner/Retriever 的扁平投影，不再作为 canonical source of truth；
- `no_preference_attributes`：用户明确表示无偏好的属性；
- `messages`：完整原始对话，只用于审计和现有 Policy 行为；
- `active_context`：只允许保存仍有效的、经过清理的正向自由文本。

禁止增加以下持久字段：

- `positive_value_turns`
- `negative_value_turns`
- `last_operations`
- `last_update_ambiguous`
- `clarification_attribute`
- `reset_recommendation_history`
- 任何 confidence/provenance map

### 5.2 瞬时操作

在 `shopping_copilot/state/semantic_delta.py` 定义：

```python
from dataclasses import dataclass
from enum import Enum


class OperationKind(str, Enum):
    SET = "set"
    EXCLUDE = "exclude"
    REMOVE = "remove"
    ALLOW = "allow"
    DONTCARE = "dontcare"


@dataclass(frozen=True)
class SlotOperation:
    kind: OperationKind
    slot: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnDelta:
    operations: tuple[SlotOperation, ...] = ()
    positive_context: tuple[str, ...] = ()
```

要求：

- `TurnDelta` 只在一次 `update()` 调用内存在，不写入 `SessionState`。
- 操作顺序必须与用户句中顺序一致，Reducer 按顺序执行。
- 不添加 `confidence`、`source_text`、`diagnostics` 等本轮不使用的字段。

## 6. 五种操作的精确定义

| 操作 | 示例 | Reducer 行为 |
| --- | --- | --- |
| `SET` | `I want white instead` | 用新值替换该 slot 的正向值；从同 slot 负向值中移除相同新值；移除 no-preference 标记 |
| `EXCLUDE` | `I don't want black` | 将值加入该 slot 负向值；从同 slot 正向值中移除相同值 |
| `REMOVE` | `I no longer need waterproof` | 仅从同 slot 正向值移除；不加入负向值 |
| `ALLOW` | `Black is fine after all` | 仅从同 slot 负向值移除；不自动设为正向值 |
| `DONTCARE` | `Color doesn't matter` | 清空该 slot 的正向和负向值，并加入 no-preference |

补充规则：

- `SET` 的多个值表示该 slot 的当前可接受集合，例如 `red or blue`。
- 本轮不实现单独 `ADD`；自然语言中的 `also` 对新 slot 仍是 `SET`，同 slot 多值在一次 `SET` 中表达。
- 本轮不实现单独 `CLEAR`；撤销具体正向值使用 `REMOVE`，明确无偏好使用 `DONTCARE`。
- 空 list 必须从 `active_slots` 或 `negative_slots` 删除，不保留空 key。
- value 比较使用统一规范化结果，但状态中保存清理后的原始短语，不能只保存拆分 token。

### 6.1 同轮顺序示例

初始状态为空：

```text
I don't want black, I want white.
```

操作：

```text
EXCLUDE(color, black)
SET(color, white)
```

结果：

```python
active_slots["color"] == ["white"]
negative_slots["color"] == ["black"]
```

```text
I don't want black; actually black is fine after all.
```

操作：

```text
EXCLUDE(color, black)
ALLOW(color, black)
```

最终不得保留 `black` 负向约束。

## 7. Parser 实现规格

### 7.1 API

`shopping_copilot/state/semantic_delta.py` 提供纯函数：

```python
def parse_turn(
    user_message: str,
    pending_attribute: str | None,
    active_slots: dict[str, list[str]],
    negative_slots: dict[str, list[str]],
) -> TurnDelta:
    ...


def apply_operations(state: SessionState, delta: TurnDelta) -> bool:
    """Mutate canonical constraint fields and return whether they changed."""
```

`apply_operations()` 是唯一允许同时维护以下三者一致性的函数：

- `active_slots`
- `negative_slots`
- `no_preference_attributes`

`RuleStateTracker.update()` 负责 session 生命周期、pending attribute、turn、messages、intent 和 coverage-history reset，不在正则分支里重复 reducer 语义。

### 7.2 Clause 切分

按以下边界切分并保留原顺序：

- `.`, `;`, `,`
- 对立连接：`but`, `however`, `instead`, `rather than`
- 并列且明显引入新主语/谓词的 `and I ...`

注意：先保护固定 cue `anything but` 和例外结构 `not only ... but also ...`，不得把其中的 `but` 当作普通 clause 边界。把 `instead`、`rather than` 用作边界时，必须把该标记附着到相邻 value span，不能在切分时直接丢弃，否则 `cotton instead` 无法被识别为 `SET`。

每个 clause 独立判断 polarity。禁止因为整句出现一次 `not`，就把所有 value 都标为负向。

### 7.3 Cue family

正则应围绕 cue family 和开放 complement span 编写，不得硬编码完整评测句子。

同一 clause 存在重叠 cue 时，匹配优先级固定为：

```text
DONTCARE > ALLOW > REMOVE > EXCLUDE > SET
```

高优先级操作消费过的 span 不得再次被低优先级规则解释。例如 `I don't mind black` 只能生成 `ALLOW`，`I no longer need waterproof` 只能生成 `REMOVE`。

#### `EXCLUDE`

至少覆盖：

```text
don't want X
do not want X
avoid X
exclude X
without X
anything but X
not X
```

#### `SET`

至少覆盖：

```text
want X
prefer X
need X
make it X
switch to X
X instead
what matters is: X
a key requirement is: X
```

#### `REMOVE`

至少覆盖：

```text
no longer need X
no longer want X
drop X
remove X as a requirement
forget the X preference
```

#### `ALLOW`

至少覆盖：

```text
X is fine after all
X is okay after all
you can include X
I don't mind X
```

#### `DONTCARE`

保留并扩展现有 boundary 语义：

```text
color doesn't matter
any color is fine
no preference for color
either is fine
use your judgment
```

若没有显式 slot，`DONTCARE` 只能使用 `pending_attribute`。没有 pending attribute 时必须保守 no-op，不能猜测并清空任意 slot。

### 7.4 假阳性保护

以下表达不得产生 `EXCLUDE`：

```text
not only black but also white
I'm not sure about color
not necessarily waterproof
not too expensive
```

其中 `not too expensive` 可以继续由现有 budget 逻辑处理或保持未解析，但不得把 `expensive` 写入硬排除。

### 7.5 Value span 提取

从 cue 后复制 complement span，并在以下位置截断：

- clause 边界；
- 引入相反 polarity 的 cue；
- 明显的新属性标签；
- 句末。

清理首尾的：

- 冠词：`a`, `an`, `the`
- 礼貌词：`please`
- 控制词：`instead`, `after all`, `as a requirement`
- 标点和多余空格

允许用 `or`、`/`、分号和明确枚举 `and` 拆成多个值，但不得把普通多词短语拆散。例如：

- `black or navy` → `black`, `navy`
- `merino wool` → 一个 value
- `machine washable` → 一个 value

### 7.6 Slot typing 优先级

严格按下列顺序选择 slot，命中后停止：

1. clause 中显式属性标签：`color`, `material`, `size`, `style`, `brand`, `budget`, `feature`, `use case`, `category`；
2. `pending_attribute`；
3. value 与当前 `active_slots` 或 `negative_slots` 中已有值规范化后相同；
4. 当前 `rule_state.py` 已有的小型稳定 seed lexicon；
5. 明确形式 `X color`、`X material`、`for running` 等局部上下文；
6. 无法分类：使用 `other`。

重要约束：

- 明确 `EXCLUDE` 即使 value 是 OOV，也允许落入 `negative_slots["other"]`，以便进行全商品文本硬排除。
- pending attribute 必须能处理 OOV，例如系统刚问 color，用户回答 `Anything but ochre.`，应得到 `negative_slots["color"] == ["ochre"]`。
- 不扫描 Catalog 构建 value → attribute 映射。
- 不复制 `policy/information_gain.py` 的完整词表；若确需共享少量 seed，移动到一个小型常量位置并保证两个模块只保留各自用途，不重写 Information Gain。

## 8. `RuleStateTracker` 集成

修改 `shopping_copilot/state/rule_state.py`，保留公开方法签名：

```python
reset(session_id, user_profile)
get(session_id)
mark_asked(session_id, attribute)
update(session_id, user_message, turn)
```

`update()` 固定执行顺序：

1. 读取并清空 `pending_attribute`；
2. 对 canonical preference state 做 before snapshot；
3. 调用 `parse_turn()`；
4. 调用 `apply_operations()`；
5. 同步 `excluded_terms`；
6. 更新经过清理的 `active_context`；
7. 如果 canonical preference state 改变，重置推荐历史；
8. 更新 `turn`、`messages`、`intent_mode`。

### 8.1 canonical preference snapshot

比较以下内容，忽略 list 顺序和大小写差异：

```text
active_slots
negative_slots
no_preference_attributes
```

任何一项实质变化都调用一个本地 helper：

```python
def _reset_recommendation_history(state: SessionState) -> None:
    state.question_scores = {}
    state.shown_asins.clear()
    state.previous_candidate_ids = ()
    state.previous_slot_signature = ()
    state.candidate_overlap = 0.0
    state.stagnant_candidate_turns = 0
    state.coverage_mode = False
```

不要为此在 `StateDelta` 或 `SessionState` 中新增 flag。

### 8.2 `excluded_terms` 兼容投影

每次 reducer 执行后重新生成，不做增量维护：

```python
state.excluded_terms = {
    normalized_value
    for values in state.negative_slots.values()
    for normalized_value in values
}
```

这样 `negative_slots` 是唯一事实来源，不会出现两个排除状态不同步。

### 8.3 active context

`messages` 继续保存原始消息；`active_context` 不再直接保存包含控制语句和负向 span 的完整消息。

规则：

- 只追加 `TurnDelta.positive_context`；
- `EXCLUDE`、`REMOVE`、`ALLOW`、`DONTCARE` 对应的 clause 不追加；
- `SET` 替换某 slot 时，从已有 `active_context` 中移除旧 value；
- 所有 negative value 必须从已有和新增 context 中按 token 边界移除；
- 清理后为空的 context 项删除；
- category 和未绑定 slot 的正向产品描述可以保留。

`positive_context` 必须是去掉 `I want`、`please`、`actually`、`instead` 等对话控制词后的 product-bearing residual，不得把完整正向 clause 原样复制进去。已经进入 `active_slots` 的 value 可以不再重复写入 `positive_context`。

## 9. Canonical Query Planner

修改 `shopping_copilot/planning/query_planner.py`，不新增第二个 Planner 类。

### 9.1 lexical terms

只从以下来源生成：

1. `active_slots` 的当前值；
2. 清理后的 `active_context`；
3. 现有必要的 profile 路径保持不变。

然后：

- 去 stopword；
- 去重；
- 删除所有 negative value 中出现的 token；
- 保持现有最多 40 tokens 限制。

### 9.2 semantic query

不得继续使用原始消息历史直接拼接。使用：

```text
active slot values + sanitized active_context
```

再次执行 negative-token 清理，作为不变量保护。Dense 当前关闭，但该字段必须正确。

### 9.3 excluded terms

```python
excluded_terms = tuple(
    sorted({
        value
        for values in state.negative_slots.values()
        for value in values
    })
)
```

不修改 `SearchPlan` 数据结构，不新增 `negative_constraints` 字段。最终 attribute-aware guard 直接读取 Ranker 已收到的 `SessionState.negative_slots`。

## 10. 共享商品排除匹配

新增 `shopping_copilot/catalog/constraints.py`，只提供纯函数，不创建需要 factory 注入的新 class。

建议 API：

```python
def normalized_tokens(text: str) -> tuple[str, ...]:
    ...


def product_matches_value(product: Product, slot: str, value: str) -> bool:
    ...


def violates_negative_slots(
    product: Product,
    negative_slots: dict[str, list[str]],
) -> bool:
    ...


def matches_any_excluded_term(product: Product, values: tuple[str, ...]) -> bool:
    ...
```

匹配规则：

- 使用与 Planner 一致的 Unicode/小写/token normalization；
- 单 token value 必须按完整 token 匹配；
- 多 token value 优先连续短语匹配；若 Catalog 标点造成切分，可退化为所有 value token 均出现；
- `brand` 优先匹配 `store + title`；
- `category` 匹配 categories；
- 其他 slot 匹配 `searchable_text`；
- 空值永不匹配；
- 禁止裸 `value in searchable_text` 作为唯一逻辑。

`RuleQueryPlanner` 应直接复用 `normalized_tokens()`，不要另写一套边界规则；现有 Planner stopword 集仍可在 tokenization 后继续应用。

### 10.1 调用点

只维护一套匹配语义，在以下必要位置复用：

1. `BM25Retriever` 和 `StructuredRetriever`：替换已有 substring 排除判断，保留其候选补充作用；
2. `HeuristicRanker.rank()`：在计算分数前跳过 `violates_negative_slots(...)` 的商品，作为覆盖所有检索路线的最终硬约束。

这不是三套过滤规则：两个 retrieval 调用点和一个最终 guard 必须全部调用同一个 `catalog/constraints.py` 实现。不要在 `HybridRetriever` 再增加第四套过滤。

BM25 现有 `fetch_limit = candidate_k * 4` 保持不变；本轮不新增自适应 expansion。

## 11. 与当前新增模块的兼容要求

### 11.1 Information Gain

- 不修改 scoring 公式、priors、词典或阈值。
- 不因为用户排除了某个 color，就把整个 `color` 标记为 unavailable；`not black` 后继续询问正向颜色偏好是合理的。
- Ranker 输出已经执行负向 guard，因此 Information Gain 接收的候选应当不违反明确排除。

### 11.2 Candidate Coverage

修改 `shopping_copilot/policy/coverage.py` 中的 slot signature，使其同时编码：

```text
+slot = positive values
-slot = negative values
~slot = explicit no-preference
```

可继续复用 `previous_slot_signature`，不新增字段。例如：

```python
(
    ("+category", ("shoes",)),
    ("+color", ("white",)),
    ("-color", ("black",)),
)
```

签名值必须大小写归一、去重并稳定排序。

约束变化时由 `RuleStateTracker` 重置 Coverage 历史；`CandidateCoverageManager.observe()` 仍负责正常的停滞检测。不要让两个模块各自实现不同的“是否 override”判断。

### 11.3 Ranking

- 只增加最终 negative guard。
- 不调整 dynamic constraint weighting。
- 不调整 rarity weighting。
- 不借本任务修改 substring 正向 constraint score；该问题单独做后续消融。

### 11.4 Factory 和 Pipeline

- `shopping_copilot/core/factory.py` 的构造关系保持不变。
- 不删除 `coverage_manager`。
- 不改变 `core/pipeline.py` 的调用顺序。
- 只在 trace 中增加：

```python
"negative_slots": {key: list(values) for key, values in state.negative_slots.items()}
```

- 保留现有 `excluded_terms` trace，便于确认投影一致。

### 11.5 Config

- 本轮不增加新的 state implementation 名称。
- 不修改 `configs/final.json` 中现有 Ranking/Policy 参数。
- 不通过配置引入“半开启”的新语义；否定和局部 override 属于状态正确性修复。

## 12. 文件级实施清单

按顺序执行，每一阶段完成后运行相关测试。

### 阶段 A：状态契约和纯语义层

修改：

- `shopping_copilot/core/contracts.py`
- `shopping_copilot/state/semantic_delta.py`（新增）
- `shopping_copilot/state/rule_state.py`

完成标准：

- 5 种 operation 可独立解析和归并；
- `negative_slots`、`active_slots`、no-preference 满足不变量；
- 现有 pending attribute、marker、budget、size、category 行为不回归；
- 删除当前 `is_override` 分支中的全局清空语义，改为 slot-level operations；
- 推荐历史在 canonical state 变化后统一重置。

### 阶段 B：Canonical Query

修改：

- `shopping_copilot/planning/query_planner.py`

完成标准：

- negative 和 stale value 不出现在 lexical/semantic query；
- current positive value、category、budget filter 仍存在；
- 不修改 SearchPlan contract。

### 阶段 C：统一硬排除

修改：

- `shopping_copilot/catalog/constraints.py`（新增）
- `shopping_copilot/retrieval/bm25.py`
- `shopping_copilot/retrieval/structured.py`
- `shopping_copilot/ranking/heuristic.py`

完成标准：

- 所有调用点使用同一 matcher；
- token 边界、多词值、OOV 值测试通过；
- Ranker 对任意来源 candidate 都执行最终 guard；
- Hybrid 不增加重复规则。

### 阶段 D：现有模块兼容

修改：

- `shopping_copilot/policy/coverage.py`
- `shopping_copilot/core/pipeline.py`

完成标准：

- Coverage signature 包含正向、负向和 no-preference；
- trace 包含 `negative_slots`；
- Factory/Policy/Information Gain 构造和行为保持不变。

### 阶段 E：测试与评测

新增：

- `tests/test_semantic_state.py`
- `tests/test_negative_constraints.py`

如需修改已有测试，只能更新对错误全局 override 行为的断言；不得删除或放宽其他断言。

## 13. 必须实现的测试矩阵

### 13.1 状态解析与归并

| 用例 | 初始状态 | 输入 | 必须结果 |
| --- | --- | --- | --- |
| 基础否定 | 空 | `I don't want black.` | `negative.color=[black]`，无正向 black |
| 同轮替换 | 空 | `I don't want black, I want white.` | `negative.color=[black]`，`active.color=[white]` |
| 属性级 override | material=leather, budget=80 | `Actually, cotton instead.` | material=cotton，budget=80 |
| 保留 category | category=shoes, color=black | `Make it white instead.` | category=shoes，color=white |
| 撤销正向 | feature=waterproof | `I no longer need waterproof.` | 无 positive waterproof，也无 negative waterproof |
| 恢复允许 | negative color=black | `Black is fine after all.` | 无 negative black，不自动 positive black |
| 无偏好 | positive color=white, negative color=black | `Color doesn't matter.` | color 正负都清空，no-preference=color |
| pending OOV | pending=color | `Anything but ochre.` | `negative.color=[ochre]` |
| 无上下文 OOV | 空 | `Please avoid ecru.` | `negative.other=[ecru]` |
| 多词值 | pending=material | `I don't want merino wool.` | 一个 value `merino wool`，不拆成两个 |
| 顺序覆盖 | 空 | `Avoid black; actually black is fine after all.` | 最终无 negative black |
| 非否定 | 空 | `Not only black but also white.` | 不产生 negative slot |
| 不确定表达 | 空 | `I'm not sure about color.` | 不产生 negative slot |
| Boundary pending | pending=color | `Either is fine.` | no-preference=color |
| Boundary 无 pending | 空 | `Either is fine.` | 不随机清空任何 slot |

### 13.2 Query

至少断言：

- `I don't want black, I want white shoes` 后，`white`、`shoes` 在查询中，`black`、`don't`、`want` 不在查询中；
- `leather → cotton instead` 后，`leather` 不在 lexical/semantic query，`cotton` 在；
- 多词 negative 的每个 token 都不作为正向 lexical token；
- `SearchPlan.excluded_terms` 与 `negative_slots` 扁平结果完全一致。

### 13.3 商品过滤

使用临时 synthetic catalog，至少包含：

```text
A: Black leather walking shoe
B: White cotton walking shoe
C: Blackberry graphic white shirt
D: Merino wool winter sock
E: Wool-blend winter sock
```

至少断言：

- 排除 `black` 时 A 被过滤，C 不因 `blackberry` 被过滤；
- 排除 `merino wool` 时 D 被过滤；
- BM25 candidate、Structured candidate 和手工构造的 Dense-like candidate 都会被 Ranker guard；
- 排除后仍能从 BM25 的扩展候选中补足未违反约束的结果；
- 所有返回推荐均不匹配 negative slots。

### 13.4 Coverage 与现有行为

至少断言：

- 仅新增 `negative.color=black` 时 Coverage signature 变化；
- 约束变化后 `shown_asins`、previous candidates、stagnation、coverage mode 被重置；
- 无约束变化的普通补充语句不重置 Coverage；
- 当前 `test_override_clears_candidate_coverage_history` 继续通过；
- Information Gain 测试、Coverage rotation 测试和 dynamic ranking 测试继续通过。

### 13.5 OOV 与模板变形

用表驱动测试把至少 6 个未出现在 seed lexicon 的值代入多个 cue family，例如：

```text
ochre
ecru
lyocell
rose gold
goodyear welted
wrinkle resistant
```

不要为这些测试值扩充 seed lexicon。测试目标是验证开放 span copy，而不是扩大词典。

同一个语义至少测试 3 个不同 cue family。避免测试只覆盖与 public simulator 完全相同的完整模板。

## 14. 执行命令和验收门槛

### 14.1 修改前记录基线

如果本地存在官方 Catalog，先运行：

```bash
python -m unittest discover -s tests -p "test*.py" -v
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output /tmp/semantic_state_before.json
```

若 Catalog 不存在，记录为环境限制，但 synthetic unit tests 仍必须完成。

### 14.2 每阶段验证

```bash
python -m unittest tests.test_semantic_state -v
python -m unittest tests.test_negative_constraints -v
python -m unittest discover -s tests -p "test*.py" -v
```

### 14.3 修改后评测

```bash
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output /tmp/semantic_state_after.json

python -m scripts.compare_runs \
  /tmp/semantic_state_before.json \
  /tmp/semantic_state_after.json
```

### 14.4 硬验收门槛

必须全部满足：

- 全量单测通过；
- 本文状态、查询、过滤不变量测试 100% 通过；
- 无运行时 LLM 和新增第三方依赖；
- 官方 Agent contract 不变；
- public Intent Override 的 HitRate@10 不低于修改前；
- public Boundary 的 HitRate@10 不低于修改前；
- overall HitRate@10 最多允许下降 `0.005`（200 条中的 1 条）；
- overall MRR 最多允许下降 `0.01`；
- 不允许通过放宽明确负向硬约束来恢复公开分数。

如果 public 分数超出允许回归：

1. 比较 before/after 的 session IDs；
2. 检查是 parser 假阳性、stale query，还是 filter 误杀；
3. 修正通用语义规则并补 metamorphic test；
4. 禁止添加只匹配单个 public 完整句子或 target value 的例外。

## 15. 现有指标优化器的消融要求

语义修复通过后，再单独评估现有指标优化器；不得与语义修复混在同一轮调参。

按以下顺序做二值消融：

1. 当前 `configs/final.json`；
2. 只关闭 `rarity_weighting`；
3. 同时关闭 `rarity_weighting` 和 `dynamic_constraint_weight`；
4. 在当前 ranking 下只关闭 `coverage_enabled`。

选择规则：

- Information Gain 默认保留，因为它解决提问选择问题，并非否定状态的替代品；
- Coverage 默认保留，但如果只提升重复展示、多轮旋转而显著损害 MRR，则关闭；
- rarity 或 dynamic weighting 若没有稳定、可解释的正增益，优先关闭；
- 不根据单个 session 成败调 coefficient；
- 至少同时查看 overall、Buying、Browsing、Intent Override、Boundary 五组指标；
- 若两个配置差异小于 1 个 public session 的量级，优先选择逻辑更简单、参数更少的配置。

该消融阶段只产生评测结论。除非调用方明确授权，不修改 `configs/final.json`。

## 16. 明确延期项和升级触发条件

以下内容进入后续 backlog，不在本轮实现：

| 延期项 | 只有满足以下证据才启动 |
| --- | --- |
| Catalog Grounder | OOV span 已正确复制，但 slot typing 错误仍造成可量化的主要失败 |
| confidence/ambiguity 状态 | 保守 no-op 导致大量可复现失败，且现有 Policy 无法自然恢复 |
| 轻量分类模型 | 规则在模板留出集上持续失败，并已有足够标注数据做真正 held-out 验证 |
| LLM parser | 规则和小模型都不能覆盖，且延迟、成本、离线 fallback、JSON 验证均可接受 |
| Hybrid 中央过滤层 | 新增检索路线绕过 Ranker guard，或当前候选补充机制被证明不足 |
| 自适应 candidate expansion | 明确负向过滤导致 Top-K 经常不足，并能在 synthetic/public failure 中复现 |
| provenance/操作历史 | 调试或产品解释有正式需求，而非预想需求 |

## 17. Definition of Done

执行代理最终必须提交以下结果：

- [ ] `negative_slots` 是 canonical negative state；
- [ ] 5 类瞬时 operation 已实现；
- [ ] 不再使用 `actually` 触发全局非 category 清空；
- [ ] canonical query 不包含 negative/stale value；
- [ ] 共享 token-boundary matcher 已复用；
- [ ] Ranker final guard 覆盖所有 candidate route；
- [ ] Coverage signature 和 reset 正确；
- [ ] trace 可观察 positive/negative state；
- [ ] 现有模块构造和 Pipeline 顺序未变化；
- [ ] 全量测试通过；
- [ ] OOV、paraphrase、false-positive 测试通过；
- [ ] 有 Catalog 时完成 before/after public 对比；
- [ ] 未修改 evaluator、public labels、Catalog 和无关参数；
- [ ] 未实现本文件列出的延期项。

最终交付说明必须包含：

1. 修改文件列表；
2. 每个状态操作的实现摘要；
3. 测试命令和完整通过数量；
4. public before/after 总体与 scenario 指标；
5. 尚未覆盖的表达和风险；
6. 是否触发任何延期项的升级条件；
7. `git diff --check` 结果。

## 18. 最终决策原则

本任务的优化顺序固定为：

```text
语义正确性
  > 私有模板/属性值泛化
  > 不破坏现有模块
  > 公开集指标
  > 新颖但未经验证的复杂设计
```

如果一个新抽象、字段或规则无法直接支持本文某个硬不变量或失败用例，就不要加入本轮实现。
