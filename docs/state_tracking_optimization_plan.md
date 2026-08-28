# 否定约束与 Intent Override 优化实施计划

> **状态：已归档，不用于本轮代码执行。** 本方案包含完整 Grounder、置信度和多层过滤等扩展设计。请以 [`state_tracking_optimization_plan_lean.md`](state_tracking_optimization_plan_lean.md) 为唯一执行规格，不要合并两份方案。

> - 面向执行者：Luna Max（代码实现代理）
> - 项目：TechJam Conversational Search
> - 文档状态：已归档的扩展方案（非本轮执行规格）
> - 默认技术路线：**不使用生成式 LLM，不新增第三方运行时依赖**
> - 适用范围：英文购物对话、官方固定 Catalog、最多 10 轮会话

## 0. 执行摘要

当前系统已经具备 `excluded_terms`、多轮状态和 Intent Override 的部分接口，但存在三个核心缺陷：

1. 属性值只要出现在句中就会被当作正向约束，无法识别 `I don't want black`。
2. Intent Override 依赖少数固定触发词，并会清空几乎所有非 category 状态，无法进行属性级、值级修改。
3. 查询仍由原始对话文本构造；即使识别出否定，`black` 仍可能进入 BM25 或未来的 Dense Query。

本计划实现一个可配置的 `rule_delta_v2` 状态追踪器，采用以下架构：

```text
Current user message + Previous structured state + Pending attribute
                              |
                              v
                    Open-value span extraction
                              |
                              v
                  Catalog-grounded slot typing
                              |
                              v
                    Ordered SlotOperations
          SET / ADD / REMOVE / EXCLUDE / ALLOW / DONTCARE / CLEAR
                              |
                              v
                    Deterministic StateReducer
                              |
                              v
        Positive slots + Negative slots + No-preference + Provenance
                              |
                              v
              Canonical query planning (no raw negated text)
                              |
                              v
     BM25 / Structured retrieval filters + final ranker exclusion guard
```

这套设计的目标不是枚举所有可能的自然语言，而是：

- 属性值从用户原文复制，避免依赖封闭值词典；
- 属性类型优先由完整 Catalog 和对话上下文判断，避免只拟合 200 个公开目标；
- 将无限表达归约为有限、可测试的状态操作；
- 低置信度时保守处理或请求澄清，不执行不可逆的错误硬过滤；
- 使用配置保留 v1，任何回归都可以一行配置回滚。

## 1. 目标与非目标

### 1.1 必须完成

- 正确解析显式否定值，例如：
  - `I don't want black.`
  - `Anything but leather.`
  - `Please avoid polyester.`
- 正确解析同轮替换，例如：
  - `I don't want black, I want white.`
  - `Make it white instead.`
- 正确解析跨轮撤销、恢复和无偏好：
  - `I no longer need waterproof.`
  - `Black is fine after all.`
  - `Color doesn't matter.`
- Intent Override 只修改相关 slot/value，不因 `actually` 清空预算、尺寸等无关条件。
- 支持 marker 或 pending attribute 下的开放属性值，例如 `ochre`、`merino wool`、`machine washable`。
- 使用完整 Catalog 做属性 grounding，而不是从公开 200 个 target 构造词典。
- Planner 仅从当前有效状态生成查询；负向值不得进入正向 lexical/semantic query。
- 所有检索路线和最终排序都不得返回违反高置信度硬排除的商品。
- 保持官方 `Agent.reset/respond` 接口和返回结构不变。
- 保持旧 `rule` 实现可通过配置选择，便于回滚和 A/B 对比。

### 1.2 明确不做

- 本轮不接入远程或本地生成式 LLM。
- 本轮不训练 BERT、MiniLM、CRF 或其他统计模型。
- 本轮不承诺中文、多语言和任意隐式情感表达。
- 本轮不修改 `evaluator/`、`data/public_set.jsonl` 或官方评分逻辑。
- 本轮不修改 Catalog 内容，不从 public ground truth 硬编码商品或属性值。
- 本轮不启用 Dense Retriever；只保证未来 Dense Query 不包含已否定值。
- 本轮不把 `configs/final.json` 直接切换到 v2；只有通过全部验收门槛后才允许提升为 final。

## 2. 现状约束

执行前必须阅读：

- `docs/team/architecture.md`
- `docs/team/ownership.md`
- `docs/competition_specification.md`
- `shopping_copilot/core/contracts.py`
- `shopping_copilot/core/interfaces.py`
- `shopping_copilot/core/factory.py`
- `shopping_copilot/state/rule_state.py`
- `shopping_copilot/planning/query_planner.py`
- `shopping_copilot/retrieval/bm25.py`
- `shopping_copilot/retrieval/structured.py`
- `shopping_copilot/retrieval/hybrid.py`
- `shopping_copilot/ranking/heuristic.py`
- `tests/test_team_pipeline.py`

必须遵守：

- Python 3.10+ 标准库是默认运行环境。
- `core/contracts.py`、`core/interfaces.py`、`core/pipeline.py` 属于共享边界，改动必须保持向后兼容。
- `evaluator/` 和公开标签不可修改。
- 现有 v1 测试必须继续通过。
- 不提交 Catalog、评测结果、trace、模型缓存或凭据。

## 3. 技术选型

| 子问题 | 采用方案 | 本轮不采用 | 决策理由 |
| --- | --- | --- | --- |
| 未知属性值 | 原文 span 复制 | 封闭值枚举 | 私有目标来自完整 Catalog，值空间远大于公开会话 |
| Slot 类型识别 | pending context + 显式 label + Catalog grounding + 小型 seed lexicon | 只靠手工词典 | 避免对 200 个公开 target 的词汇过拟合 |
| 否定作用域 | clause 切分 + 高精度 cue/exception + 有序操作 | 全句只查 `not` | 必须绑定到具体 slot/value，避免误伤 |
| Intent Override | Operation-based delta | 检测 `actually` 后全量清空 | 只修改相关状态，保留无关约束 |
| 状态更新 | 确定性 Reducer + invariant | 让模型直接生成最终状态 | 可复现、可审计、可单测 |
| 查询构造 | 规范化状态重建 | 拼接原始对话历史 | 防止否定词和值污染 BM25/Dense |
| 排除执行 | 属性级 matcher + Retriever filter + Ranker guard | 全文本 substring 单点过滤 | 避免漏过滤和 `black`/`blackberry` 误判 |
| 泛化测试 | value/template family holdout + metamorphic cases | 只跑 public score | public score 无法发现模板记忆 |
| 模型策略 | v2 无模型；满足升级门槛后再评估小 encoder | 默认生成式 LLM | 当前数据、延迟、可复现性不支持先上 LLM |

研究依据仅用于设计，不允许用论文 benchmark 数字替代本项目验收：

- [SOM-DST：Selective Overwrite / 状态操作](https://aclanthology.org/2020.acl-main.53/)
- [TripPy：开放词表 span copy](https://aclanthology.org/2020.sigdial-1.4/)
- [Unknown Slot Values：pointer-based value extraction](https://aclanthology.org/P18-1134/)
- [D3ST：schema description 驱动的泛化](https://research.google/pubs/description-driven-task-oriented-dialog-modeling/)
- [Robust DST with Weak Supervision and Sparse Data](https://aclanthology.org/2022.tacl-1.68/)

## 4. 目标架构与模块边界

### 4.1 新增模块

```text
shopping_copilot/
  catalog/
    grounding.py             # CatalogAttributeGrounder、值规范化、字段级匹配
    constraints.py           # ProductConstraintMatcher，正/负约束匹配
  state/
    operations.py            # SlotOperation、StateDelta、操作常量
    rule_delta_parser.py     # 规则式开放 span + cue/scope + slot typing
    reducer.py               # 唯一允许修改 canonical state 的归并器
    delta_state.py           # DeltaRuleStateTracker，维持现有 StateTracker 接口
  planning/
    canonical_query_planner.py # v2 专用，从 canonical state 构造无污染查询
tests/
  fixtures/
    semantic_state_cases.jsonl
  test_state_delta_parser.py
  test_state_reducer.py
  test_catalog_grounding.py
  test_negative_retrieval.py
scripts/
  run_state_robustness.py
```

如果实现过程中发现 `grounding.py` 和 `constraints.py` 共享大量规范化逻辑，应提取到 `catalog/text_normalization.py`，不得复制两套稍有不同的 normalization。

### 4.2 保留模块

- `shopping_copilot/state/rule_state.py` 保留为 v1，不在其中继续堆叠 v2 规则。
- `starter/agent.py` 的公共接口不变。
- `shopping_copilot/core/pipeline.py` 的执行顺序不变，只增加 trace 字段。
- `shopping_copilot/core/interfaces.py` 的 `StateTracker` 方法签名不变。

### 4.3 组件构造与调用关系

实现时采用以下显式依赖，不使用模块级全局单例：

```python
store = CatalogStore(catalog_path)
grounder = CatalogAttributeGrounder(store)
matcher = ProductConstraintMatcher(store)
parser = RuleDeltaParser(grounder)
reducer = StateReducer()
tracker = DeltaRuleStateTracker(parser=parser, reducer=reducer)
planner = CanonicalQueryPlanner(config.search)
```

关键方法签名：

```python
CatalogAttributeGrounder.classify(value: str) -> GroundingResult
RuleDeltaParser.parse(
    state: SessionState,
    user_message: str,
    context_attribute: str | None,
) -> StateDelta
StateReducer.apply(state: SessionState, delta: StateDelta, turn: int) -> SessionState
```

`RuleDeltaParser` 只解析，不直接 mutation state；`StateReducer` 是唯一 canonical state 写入点；`DeltaRuleStateTracker` 负责 session 生命周期、pending attribute、消息历史和调用编排。

## 5. 数据契约

### 5.1 SlotOperation

在 `shopping_copilot/state/operations.py` 新增不可变 dataclass：

```python
@dataclass(frozen=True)
class SlotOperation:
    kind: str
    slot: str | None
    values: tuple[str, ...] = ()
    source_text: str = ""
    confidence: float = 1.0
```

允许的 `kind`：

```text
SET        用本轮值替换该 slot 的旧正向值
ADD        在该 slot 增加正向值
REMOVE     撤销正向值，但不将其视为禁止值
EXCLUDE    加入负向值，并从正向值移除
ALLOW      从负向值移除，但不自动变成正向偏好
DONTCARE   清空该 slot 正负值，并记录 no-preference
CLEAR      清空该 slot 正负值及 no-preference，使 slot 回到未指定
```

说明：

- 不需要显式 `CARRYOVER`；没有操作即自然 carry over。
- 不新增 `REPLACE`；`EXCLUDE old + SET new` 或单独 `SET new` 已能表达两种不同语义。
- `confidence` 必须在 `[0.0, 1.0]`，v2 规则输出只使用离散档：`1.0`、`0.9`、`0.7`、`0.4`。
- 只有 `confidence >= 0.9` 的负向操作才能写入 `negative_slots` 并进入硬过滤；更低置信度只记录诊断并触发澄清。

### 5.2 StateDelta

```python
@dataclass(frozen=True)
class StateDelta:
    operations: tuple[SlotOperation, ...] = ()
    parser_version: str = "rule_delta_v2"
    ambiguous: bool = False
    diagnostics: tuple[str, ...] = ()
```

操作必须保持用户原句中的顺序。Reducer 逐条执行，后出现的明确表达优先。

### 5.3 SessionState 扩展

在 `shopping_copilot/core/contracts.py` 中向 `SessionState` 添加带默认值的字段，保留原字段以兼容 v1：

```python
negative_slots: dict[str, list[str]] = field(default_factory=dict)
positive_value_turns: dict[str, dict[str, int]] = field(default_factory=dict)
negative_value_turns: dict[str, dict[str, int]] = field(default_factory=dict)
last_operations: list[dict[str, object]] = field(default_factory=list)
```

兼容语义：

- `active_slots` 继续作为 canonical positive slots。
- `negative_slots` 是 canonical attribute-aware exclusions。
- `excluded_terms` 暂时保留，始终由高置信度 `negative_slots` 派生；不得再单独作为状态真相源写入。
- `messages` 保留原始审计历史。
- `active_context` 可保留兼容，但 Planner v2 不得再使用它构造查询。

### 5.4 SearchPlan 扩展

在 `SearchPlan` 字段列表末尾添加带默认工厂的字段，避免破坏现有构造代码：

```python
structured_exclusions: dict[str, tuple[str, ...]] = field(default_factory=dict)
```

构造规则：

- `structured_constraints` 来自 `active_slots`。
- `structured_exclusions` 来自 `negative_slots` 中高置信度生效的值。
- `excluded_terms` 是为旧检索器保留的扁平、规范化兼容视图。

所有新增 dataclass 字段必须提供默认值或同步更新所有构造点。执行 `rg "SessionState\\(|SearchPlan\\("` 确认没有遗漏。

## 6. 状态操作的精确定义

### 6.1 Reducer invariant

每次更新完成后必须满足：

1. 同一 slot 下，正向值集合与负向值集合交集为空。
2. `no_preference_attributes` 中的 slot 不得同时存在正向或负向值。
3. 所有值经过同一 normalization 后去重，但响应和 trace 可以保留第一次出现的可读形式。
4. `SET/ADD` 会从同 slot 的负向集合删除本次正向值。
5. `EXCLUDE` 会从同 slot 的正向集合删除本次负向值。
6. `DONTCARE` 清除该 slot 的正向、负向和 provenance。
7. `CLEAR` 与 `DONTCARE` 的区别：`CLEAR` 不加入 no-preference。
8. 空 list 对应的 slot key 必须从 dict 删除。
9. category 只有在用户明确更改 category 时才允许替换；普通 `actually` 不得删除 category。
10. `turn`、`messages`、`pending_attribute` 的现有生命周期保持不变。

### 6.2 操作示例

| 前置状态 | 用户输入 | Delta | 结果 |
| --- | --- | --- | --- |
| 无 | `I want black.` | `SET color=black` | `+color:black` |
| `+color:black` | `I don't want black.` | `EXCLUDE color=black` | `-color:black` |
| `+color:black` | `I don't want black, I want white.` | `EXCLUDE black; SET white` | `+white, -black` |
| `+feature:waterproof` | `I no longer need waterproof.` | `REMOVE waterproof` | feature 未指定，不把 waterproof 当禁用 |
| `-color:black` | `Black is fine after all.` | `ALLOW black` | color 未指定 |
| `+color:black` | `Color doesn't matter.` | `DONTCARE color` | color=no-preference |
| `+color:black, budget=100` | `Actually, make it white.` | `SET color=white` | budget 保留 |
| `+material:leather` | `Actually, ignore my earlier preference. What I need is: cotton.` | `REMOVE leather; SET cotton` | `+material:cotton` |

### 6.3 泛指旧偏好的消解

为兼容官方 override 模板，又避免全量清空，按以下优先级解析 `ignore/forget my earlier/previous preference`：

1. 句中明确指出 slot：清除或替换该 slot。
2. 句中明确指出 old value：只 `REMOVE` 该值。
3. 使用 `earlier/initial/original`：移除 provenance 中最早一轮加入的非 category 正向值。
4. 使用 `previous/last`：移除 provenance 中最近一轮加入的非 category 正向值。
5. 无修饰且只有一个非 category 活跃值：移除该值。
6. 无修饰且有多个候选：标记 `ambiguous=True`；不得全量清空。若当前句同时给出新值，只对新值所属 slot 执行 `SET`，并移除该 slot 的旧正向值。
7. 只有明确的 `start over / clear everything / ignore all requirements` 才允许清空所有非 category slot。

## 7. 文本解析算法

`RuleDeltaParser.parse(state, user_message, context_attribute) -> StateDelta` 必须按照以下顺序执行。

### 7.1 Normalization

- 使用 `unicodedata.normalize("NFKC", text)`。
- 匹配视图 lowercase，但 `source_text` 保留原文。
- 合并重复空白。
- 不使用任意 substring 判断；所有单词 cue 和单词型值必须使用 token boundary。
- 连字符词在匹配时同时支持 `non-slip` 和 `non slip`，但 canonical value 选择目录中形式或用户首次形式。

### 7.2 Clause segmentation

将以下连接结构作为候选边界，并保留原始顺序：

```text
but, however, instead, rather than, except, though, actually
comma, semicolon
```

不得无条件按 `and` 切分，因为 `black and white` 可能表示两个并列正向值。只有在 `and` 两侧分别出现完整 polarity/predicate cue 时才切分。

### 7.3 Exception 优先

先识别容易被误判为否定的表达：

```text
don't mind X       -> ALLOW 或 ADD（若用户明确说可接受）
not only X but Y   -> ADD X; ADD Y
no preference      -> DONTCARE，不是 EXCLUDE preference
not necessarily X  -> REMOVE/弱偏好，不是硬 EXCLUDE
No, I want X       -> discourse rejection + SET X，不把 X 视为否定
don't want X excluded -> ambiguous，不直接 EXCLUDE X
```

这些 exception 必须在通用 `not/no/don't` cue 之前执行。

### 7.4 Boundary/no-preference

继续覆盖现有：

```text
I don't have a preference for <slot>
Either is fine
It doesn't matter
Use your judgment
```

若句中有显式 slot，生成 `DONTCARE slot`；否则使用 `pending_attribute`。既没有显式 slot 又没有 pending context 时标记 ambiguous，不猜 slot。

### 7.5 Value span extraction

按高到低优先级抽取：

1. 显式 `key: value`，例如 `color: ochre`、`material: merino wool`。
2. 已知 marker 后的完整 span：
   - `a key requirement is:`
   - `what I need is:`
   - `what matters is:`
3. pending attribute 的短回答。
4. 否定/偏好 predicate 的 object span：
   - `don't want <span>`
   - `avoid <span>`
   - `anything but <span>`
   - `prefer <span>`
   - `want <span>`
   - `make it <span>`
   - `go with <span>`
5. seed lexicon 与 Catalog grounder 的 exact phrase candidate。

开放 span 不得因不在词典中而丢弃。无法确定 slot 时使用 `other` 或 `feature` 只作为软约束，不得产生属性级硬排除。

### 7.6 Slot typing 优先级

对每个 span 使用：

1. 显式 key/label。
2. pending attribute（仅限短回答或 marker answer）。
3. 当前句中的 slot 名称，例如 `for color`、`material should be`。
4. `CatalogAttributeGrounder.classify(span)`。
5. 通用格式规则：budget、size。
6. seed lexicon：material、color、use_case、style、feature。
7. fallback `feature`，置信度最高只能为 `0.4`。

### 7.7 Polarity 与 operation

高置信度 cue：

```text
EXCLUDE: don't want, do not want, avoid, anything but, must not, exclude, is out
REMOVE: no longer need, no longer care about, drop the requirement, not necessary
ALLOW: is fine after all, is okay after all, don't mind, can include
DONTCARE: no preference, doesn't matter, either is fine, any is fine
SET: want, prefer, make it, go with, what I need is, instead, rather
ADD: also, either X or Y, X and Y are both fine
CLEAR: clear/reset/remove this <slot> requirement
```

`actually` 本身只作为 discourse marker，绝不能独立触发清空。

裸 `not <span>` 只在以下情况作为高置信度 `EXCLUDE`：独立短句（`Not black.`），或位于明确对比结构中（`I want black, not white.`）。其他裸 `not` 必须标记 ambiguous，不能泛化成全句排除。

### 7.8 Confidence 与安全策略

| 情况 | confidence | 行为 |
| --- | ---: | --- |
| 显式 slot + 显式 cue + 明确 span | 1.0 | 正常应用，可硬过滤 |
| pending slot + marker/短回答 | 0.9 | 正常应用，可硬过滤 |
| Catalog 唯一 slot grounding + 明确 cue | 0.9 | 正常应用，可硬过滤 |
| 多个 slot grounding 但一个明显占优 | 0.7 | 可应用正向软约束；负向不写入 `negative_slots`，先澄清 |
| slot/value 或作用域不明确 | 0.4 | `ambiguous=True`，不执行硬排除 |

Policy v2 在 `ambiguous=True` 时优先询问对应 slot；若无法确定 slot，使用允许值 `other`。不得改变官方 `ask_attribute` 枚举。

## 8. Catalog Grounding

### 8.1 保留原始 details 结构

在 `CatalogStore` 内新增私有索引，不修改官方 Catalog：

```python
_detail_items_by_id: dict[str, tuple[tuple[str, str], ...]]
```

新增只读方法：

```python
def detail_items(self, parent_asin: str) -> tuple[tuple[str, str], ...]: ...
```

原有 `Product.details` 和 `searchable_text` 保持不变，避免影响 BM25 和响应。

### 8.2 Detail key 到 slot 的映射

初始 alias：

```text
color:    color, colour, shade
material: material, fabric
size:     size, width
style:    style, fit, pattern, department
brand:    brand, manufacturer
use_case: intended use, occasion, activity, sport
```

只匹配规范化 key token，不匹配任意 substring。

### 8.3 Grounder 索引

构建：

```python
normalized_value -> Counter[slot]
normalized_store -> brand
normalized_category -> category
```

`grounding.py` 同时定义：

```python
@dataclass(frozen=True)
class GroundingResult:
    slot: str | None
    confidence: float
    candidates: tuple[tuple[str, float], ...] = ()
    source: str = "none"
```

`candidates` 按得分降序排列；发生并列或最高分不足阈值时 `slot=None`，不得用 dict 遍历顺序打破语义歧义。`source` 只允许 `explicit`、`pending`、`catalog`、`seed`、`fallback`、`none`，便于 trace 和测试。

约束：

- 只索引非空、长度不超过 120 字符的 detail value。
- 不生成 title 的所有 n-gram，避免启动时间和内存爆炸。
- features/description 默认归为 `feature`，但不需要全部放入 phrase scanner；marker span 可直接 fallback 到 feature。
- 若同一 value 对应多个 slot，返回得分分布，不强行唯一归类。
- Grounder 只使用完整 Catalog，不读取 public samples 或 ground truth。

### 8.4 ProductConstraintMatcher

统一实现：

```python
matches_positive(product, slot, value) -> bool
violates_negative(product, slot, value) -> bool
```

匹配优先级：

1. slot 对应的结构化 detail values；
2. brand/store 或 category 专属字段；
3. 规范化 token/phrase 匹配相关文本字段；
4. 不得使用 `value in searchable_text` 这种裸 substring。

`black` 不得匹配 `blackberry`。多词值要求规范化 token 序列连续匹配。

## 9. Planner、Retriever 与 Ranker 改造

### 9.1 Canonical Query Planner

新增 `CanonicalQueryPlanner`，只为 `rule_delta_v2` 状态生成查询。保留现有 `RuleQueryPlanner` 给 v1，防止实验开关关闭后查询行为仍发生变化。

v2 构造规则：

```text
lexical_terms = tokens(category + positive slots)
semantic_query = readable(category + positive slots)
structured_constraints = positive slots
structured_exclusions = negative slots
excluded_terms = flattened negative slots compatibility view
```

禁止：

- 从 `active_context` 或全部 `messages` 直接生成 lexical terms；
- 把否定 cue（`don't`、`avoid`）放入查询；
- 把 negative value 放入 semantic query。

例：

```text
Message: I don't want black, I want white shoes under $100.

lexical_terms: [shoes, white]
semantic_query: white shoes
hard_filters: {price_max: 100}
structured_constraints: {category: [shoes], color: [white]}
structured_exclusions: {color: [black]}
```

### 9.2 BM25 Retriever

- 继续使用正向 FTS query 召回。
- 使用 `ProductConstraintMatcher` 进行属性级 post-filter。
- 过滤后不足 `candidate_k` 时，应分批获取更多 BM25 rows，直到：
  - 得到 `candidate_k`；或
  - 已耗尽匹配结果；或
  - 达到明确的安全上限 `min(candidate_count, candidate_k * 20)`。
- `probe()` 的 candidate count 可保持原始正向召回数量，但 diagnostics 新增被过滤数量需通过 trace 记录，不必修改公共 `RetrievalDiagnostics`。

不要直接把所有排除值拼到 FTS `NOT`：FTS5 没有单独的 unary NOT，而且全文 NOT 缺少属性范围，可能误排除描述中偶然出现该词的商品。

### 9.3 Structured Retriever

- cache key 必须包含 `structured_exclusions`。
- 正向值仍按当前属性分数召回。
- 最终 candidate 必须通过 `ProductConstraintMatcher`。
- 即使 `structured_enabled=false`，实现和测试仍要正确，供实验配置启用。

### 9.4 Hybrid Retriever

- 各子检索器应尽量先过滤。
- Hybrid fusion 完成后再执行一次统一 negative filter，防止未来 Dense 路线绕过过滤。
- 如果为实现共享过滤需要向 `HybridRetriever` 注入 `ProductConstraintMatcher`，在 factory 中完成，不改变 `Retriever` Protocol。

### 9.5 Ranker guard

- Ranker 开始计算分数前再次过滤违反高置信度负约束的 candidate。
- 这是安全兜底，不是主要过滤点。
- 正向 constraint score 不得包含 negative values。

## 10. Policy 与可观测性

### 10.1 Policy

- intent 判断时，存在明确 `negative_slots` 也应视为购买约束信号。
- 计算已知/已询问属性时，将 `negative_slots` 的 key 计入，避免重复询问同一排除属性。
- `ambiguous` 状态优先澄清，不执行低置信度硬过滤。
- 不改变最后一轮不提问的现有规则。

若不希望修改 `SessionState` 增加 `ambiguous`，可由 `last_operations` 中的 confidence 推导；优先避免新增重复状态字段。

### 10.2 Trace

在 pipeline trace 增加：

```json
{
  "negative_slots": {"color": ["black"]},
  "state_operations": [
    {"kind": "EXCLUDE", "slot": "color", "values": ["black"], "confidence": 1.0}
  ],
  "ambiguous_state_update": false,
  "negative_filtered_count": 0
}
```

不得记录 ground truth、隐藏 intent card 或用户标识。

如果传递 `negative_filtered_count` 会迫使大范围修改公共 contract，可先只记录 state 和 plan，不为了 trace 破坏稳定接口。

## 11. 配置与回滚

### 11.1 配置

扩展 `load_config()` 支持：

```json
{
  "state": {
    "implementation": "rule_delta_v2"
  }
}
```

允许值：

```text
rule            原实现
rule_delta_v2   新实现
```

新增：

```text
configs/experiments/state_delta_v2.json
```

该配置应复制 `configs/final.json` 的其他设置，只替换 state implementation。不要在第一阶段修改 `baseline.json` 或 `final.json`。

### 11.2 Factory

构建顺序：

```text
CatalogStore
  -> if rule_delta_v2:
       CatalogAttributeGrounder
       ProductConstraintMatcher
       DeltaRuleStateTracker
       CanonicalQueryPlanner
     else:
       RuleStateTracker
       RuleQueryPlanner
  -> Retrievers / Ranker / Policy
```

如果选择 `rule`，行为和启动成本必须尽量保持当前版本一致。Retriever/Ranker 的 matcher 参数应允许为 `None`；v1 不构建 Catalog grounding 索引，也不执行新增过滤。

### 11.3 回滚

任何回归均通过：

```json
"state": {"implementation": "rule"}
```

恢复旧实现。不得依赖 git revert 才能回滚运行行为。

## 12. 分阶段实施顺序

严格按顺序执行。每个阶段通过对应测试后再进入下一阶段。

### Phase 0：记录基线

- [ ] 确认 `git status --short`，不得覆盖用户已有改动。
- [ ] 运行现有测试：

```bash
python3 -m unittest discover -s tests -v
```

- [ ] 若 `data/catalog.jsonl` 存在，运行并保存到未提交的 `results/` 或 `/tmp`：

```bash
python3 scripts/run_evaluation.py \
  --config configs/final.json \
  --output /tmp/state-v2-before.json
```

- [ ] 记录 overall 和 `intent_override` 的 HitRate@10、MRR、MTTC、technical score。

### Phase 1：操作契约和 Reducer

- [ ] 新建 `state/operations.py`。
- [ ] 扩展 `SessionState` 和 `SearchPlan`，保持兼容默认值。
- [ ] 实现 `StateReducer`，所有状态 mutation 集中于此。
- [ ] 添加 reducer invariant 单元测试。
- [ ] 此阶段不接入 pipeline。

完成命令：

```bash
python3 -m unittest tests.test_state_reducer -v
python3 -m unittest discover -s tests -v
```

### Phase 2：Catalog Grounding 和 Matcher

- [ ] CatalogStore 保留只读 detail key-value。
- [ ] 实现统一 normalization。
- [ ] 实现 `CatalogAttributeGrounder`。
- [ ] 实现 `ProductConstraintMatcher`。
- [ ] 使用临时 mini catalog 测试未知值、冲突 slot 和 token boundary。
- [ ] 不读取 public labels。

完成命令：

```bash
python3 -m unittest tests.test_catalog_grounding -v
```

### Phase 3：Delta Parser 和 v2 Tracker

- [ ] 实现 clause segmentation、exception、boundary、span、slot、polarity。
- [ ] 实现 provenance-aware generic override。
- [ ] 新建 `DeltaRuleStateTracker`，实现现有 `StateTracker` Protocol。
- [ ] v1 `RuleStateTracker` 行为保持不变。
- [ ] 加载 robustness fixture 并逐例验证 delta 和最终状态。

完成命令：

```bash
python3 -m unittest tests.test_state_delta_parser -v
python3 scripts/run_state_robustness.py
```

### Phase 4：Planner 与负约束执行

- [ ] 新增 `CanonicalQueryPlanner` 从 canonical state 重建 query；保留 v1 `RuleQueryPlanner`。
- [ ] BM25、Structured、Hybrid、Ranker 统一使用 matcher。
- [ ] 过滤后补足 candidate。
- [ ] Policy 将 negative slot 视为已知约束。
- [ ] 加入 end-to-end mini catalog 测试。

完成命令：

```bash
python3 -m unittest tests.test_negative_retrieval -v
python3 -m unittest discover -s tests -v
```

### Phase 5：配置、trace 和公开集 A/B

- [ ] 支持 `rule_delta_v2` config。
- [ ] 新增 experiment config。
- [ ] 增加 trace 字段。
- [ ] 使用相同 Catalog 和 dataset 运行 v1/v2。
- [ ] 使用 `scripts/compare_runs.py` 比较。

```bash
python3 scripts/run_evaluation.py \
  --config configs/final.json \
  --output /tmp/state-v2-before.json

python3 scripts/run_evaluation.py \
  --config configs/experiments/state_delta_v2.json \
  --output /tmp/state-v2-after.json

python3 scripts/compare_runs.py \
  /tmp/state-v2-before.json \
  /tmp/state-v2-after.json
```

### Phase 6：提升为 final

只有第 14 节全部门槛通过后：

- [ ] 将 `configs/final.json` 的 state implementation 改为 `rule_delta_v2`。
- [ ] 重新运行完整测试和公开集评测。
- [ ] 在 PR/提交说明中记录假设、指标变化、回归和回滚配置。

## 13. 测试规格

### 13.1 semantic_state_cases.jsonl 格式

每行：

```json
{
  "case_id": "neg_color_001",
  "initial_state": {
    "active_slots": {"category": ["shoes"], "color": ["black"]},
    "negative_slots": {}
  },
  "pending_attribute": null,
  "message": "I don't want black, I want white.",
  "expected_operations": [
    {"kind": "EXCLUDE", "slot": "color", "values": ["black"]},
    {"kind": "SET", "slot": "color", "values": ["white"]}
  ],
  "expected_state": {
    "active_slots": {"category": ["shoes"], "color": ["white"]},
    "negative_slots": {"color": ["black"]}
  }
}
```

Fixture 只包含合成语义案例，不包含 public target ASIN 或 ground truth。

### 13.2 必测语义族

#### 正向与开放值

- `I want black.`
- `I'd prefer ochre.`
- `What matters is: machine washable.`
- pending `material` + `merino wool`
- `color: burgundy`

#### 否定

- `I don't want black.`
- `Do not include black.`
- `Anything but black.`
- `Avoid faux suede.`
- `Black is out.`
- `I want black, not white.`

#### 同轮替换

- `I don't want black, I want white.`
- `Not black; white instead.`
- `I'd rather have white than black.`
- `Make it white instead.`

#### 跨轮 override

- black -> `Actually, white.`
- leather + budget -> `Use cotton instead.`，budget 保留
- benchmark exact template -> old preference 被移除，新值加入
- `Ignore my earlier material preference.`
- `Start over.` -> 清空非 category

#### 撤销与恢复

- waterproof -> `I no longer need waterproof.`
- negative black -> `Black is fine after all.`
- negative black -> `I don't mind black.`
- color black -> `Remove the color requirement.`

#### 无偏好

- 显式 `I don't have a preference for color.`
- pending color + `Either is fine.`
- `Color doesn't matter.`
- 无 pending + `Either is fine.` -> ambiguous，不猜 slot

#### 否定例外

- `Not only black but white is fine.` 不得排除 black
- `I don't mind black.` 不得排除 black
- `Black isn't required.` 应 REMOVE，不是 EXCLUDE
- `I don't want black excluded.` 不得简单解释为 EXCLUDE black；应 ambiguous
- `No, I want black.` 不得把 black 排除

#### 状态 invariant

- 同值先 EXCLUDE 后 SET，最终正向；先 SET 后 EXCLUDE，最终负向。
- session A 操作不得影响 session B。
- reset 后无旧 negative/provenance。
- no-preference 后新的 SET 应移除 no-preference 标记。

### 13.3 Retrieval mini catalog

至少包含：

```text
A: black shoes
B: white shoes
C: black and white shoes
D: blackberry-colored novelty shoes（用于 token boundary）
E: white shirt（用于 category 约束）
```

验证：

- `+category:shoes, +color:white, -color:black` 不返回 A/C，B 优先。
- `-color:black` 不因单词边界错误排除 D。
- 过滤大量候选后仍尽力补足 `candidate_k`。
- Structured disabled/enabled 两种配置均不泄漏明确排除项。

### 13.4 Metamorphic 测试

`run_state_robustness.py` 至少自动生成以下变换：

- value substitution：black -> white -> ochre，operation/slot 不变。
- slot substitution：color -> material，operation 不变。
- harmless politeness：增加 please/could you，不改变 delta。
- punctuation：逗号/分号/句号变化，不改变语义。
- clause order：顺序改变时按最后明确操作更新结果。
- unrelated constraint：增加 budget/size 后，override 其他 slot 不得删除它们。

不要用随机 utterance split 证明泛化。报告必须按 value family 和 template family 分组。

## 14. 验收门槛

必须同时满足：

### 正确性

- [ ] 现有全部单元测试通过。
- [ ] 第 13 节指定的 deterministic semantic cases 100% 通过。
- [ ] Reducer invariant cases 100% 通过。
- [ ] 明确、高置信度 negative constraint 的 `ExclusionViolation@10 = 0`。
- [ ] 同轮 `black -> white` 最终状态必须是 `+white/-black`。
- [ ] slot-specific override 的 unrelated-slot retention 必须为 100%。
- [ ] v1 config 行为测试保持通过。

### 官方公开集

- [ ] overall recommended technical score 不低于同代码基线。
- [ ] Intent Override HitRate@10 不下降。
- [ ] Intent Override MRR 不下降。
- [ ] Boundary HitRate@10 不下降。
- [ ] 输出 contract、最大 10 条、合法 ASIN 和 usage 字段保持正确。

如果某一场景 MRR 小幅下降但 overall 上升，不得自动接受；必须输出失败 session 的 trace 差异并在评审中明确批准。

### 性能与资源

- [ ] 记录 v1/v2 Catalog 初始化时间、单轮 warm latency 和进程峰值 RSS。
- [ ] v2 warm 单轮中位延迟不得比 v1 增加超过 20%。
- [ ] Catalog grounder 不得在每个 turn 重建。
- [ ] 不得为 title/features 构建无界 n-gram 索引。
- [ ] 无网络时完整 pipeline 可运行。

### 工程质量

- [ ] 无新增运行时第三方依赖。
- [ ] `evaluator/`、public labels、Catalog 未修改。
- [ ] 新增公共函数有类型标注和简短 docstring。
- [ ] 规则常量按语义分组，禁止把所有表达堆进一个不可维护的巨型 regex。
- [ ] Parser、Reducer、Grounder、Matcher 职责分离。
- [ ] `git diff --check` 通过。

## 15. 抗过拟合约束

实现和调参期间必须遵守：

1. 不根据 public target ASIN 添加特例。
2. 不根据某个失败 sample 的完整标题硬编码 query。
3. 手工 seed lexicon 只能包含通用语言词，不得从 public ground truth 专门补值。
4. Catalog-derived value index 必须由完整 Catalog 自动构建。
5. 新规则必须至少附带：一个正例、一个反例、一个 value substitution case。
6. 公开集只用于端到端指标；语义正确性由独立 fixture 和 metamorphic suite 判断。
7. 若增加模板，必须说明它表示哪个通用 operation/cue，不允许以 sample ID 命名。
8. 不把“在公开集提高”当作规则正确性的唯一证据。

## 16. 失败处理与保守降级

解析不确定时按以下顺序处理：

1. 保留上一轮可信状态。
2. 可将原文 span 作为低置信度软 lexical hint，但不得把疑似负向值放回正向 query。
3. 选择最相关的合法 `ask_attribute` 澄清。
4. 最后一轮无法提问时，宁可不执行低置信度硬排除，也不要大面积误删候选。
5. Parser 发生异常时应用 no-op delta、记录诊断并继续响应，不能让 `respond()` 抛异常。不要临时切换到具有独立 session 存储的 v1 tracker。

注意：低置信度负约束“完全忽略”也可能返回用户不想要的商品；因此 trace 必须记录该降级，方便后续分析，但安全性优先于错误硬排除导致目标完全消失。

## 17. 可选 v3：小型判别模型升级门槛

本节不是本轮编码任务。只有满足以下任一条件才开启单独实验：

- template-family holdout operation accuracy 低于 95%；
- 真实或人工 paraphrase 集中，规则 parser 的高置信度覆盖率低于 90%；
- 超过 30% 的错误来自隐式表达、复杂指代或非局部否定作用域；
- 规则数量持续增长且新增规则反复破坏旧例。

届时优先选择：

```text
small encoder / cross-encoder
输入：previous canonical state + pending slot + current utterance
输出：operation class + slot class + extractive span
```

训练数据来源：

- Catalog value substitution；
- 人工语法生成的 state delta；
- template-family 隔离的 paraphrases；
- 可选离线 LLM teacher 生成候选，但必须规则/人工校验标签。

生成式 LLM inference 仍只作为最后一个可配置 fallback，不能直接 mutation SessionState；其输出必须先转成 `StateDelta`，再通过同一个 Reducer 和 invariant。

## 18. 建议提交拆分

为降低共享文件冲突，建议按以下提交拆分：

1. `feat(state): add slot operations and deterministic reducer`
2. `feat(catalog): add catalog-grounded attribute and constraint matching`
3. `feat(state): add open-value rule delta tracker`
4. `feat(search): enforce positive and negative canonical constraints`
5. `test(state): add semantic robustness and metamorphic coverage`
6. `exp(state): add rule_delta_v2 config and evaluation report`

每个提交必须能通过其作用域内测试；不要把 contract、parser、retrieval 和全部测试压成一个无法 review 的提交。

## 19. Definition of Done

只有以下全部成立，任务才算完成：

- v2 能正确处理否定、撤销、恢复、无偏好和属性级 override。
- 未知值可以通过 span copy 保留，并由 context/Catalog 尽可能归类。
- Query 不含已否定值，所有候选路线遵守硬排除。
- 规则不依赖 public target 特例。
- 独立语义测试、metamorphic 测试、现有测试、公开集回归门槛全部通过。
- v1 仍可通过 config 回滚。
- final config 只有在指标验收后才切换。
- 最终交付说明必须列出：修改文件、关键设计、测试命令、A/B 指标、已知限制和回滚方式。

## 20. 执行者最终汇报模板

完成后按以下格式汇报：

```markdown
## Outcome
- 实现状态：completed / partial / blocked
- final config：rule / rule_delta_v2

## Files changed
- path: purpose

## Semantic verification
- deterministic cases: passed/total
- metamorphic cases: passed/total
- ExclusionViolation@10: value
- unrelated-slot retention: value

## Official evaluation
| Metric | v1 | v2 | Delta |
| --- | ---: | ---: | ---: |
| HitRate@10 | | | |
| MRR | | | |
| MTTC | | | |
| TechnicalScore | | | |
| Intent Override HitRate@10 | | | |
| Boundary HitRate@10 | | | |

## Performance
- catalog init time v1/v2:
- warm turn median v1/v2:
- peak RSS v1/v2:

## Known limitations
- ...

## Rollback
- Set state.implementation to rule.
```
