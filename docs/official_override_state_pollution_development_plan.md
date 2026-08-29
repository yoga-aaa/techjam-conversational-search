# 官方 Intent Override 状态污染修复开发方案

> 状态：Proposed，待确认后实施
>
> 基线分支：`dev`
>
> 文档编写时 HEAD：`bc792ca`
>
> 文档性质：基于当前实现的增量开发规格，不修改召回与排序方案
>
> 主验收集：公开 200 条官方场景
>
> 补充验收集：模拟测试集“标准模式”；不以复杂改写压力模式作为上线门槛

## 0. 文档定位与执行优先级

本方案只解决当前版本已经在公开场景中确认的 Intent Override 状态污染：

1. 显式覆盖句被上一轮 `pending_attribute` 劫持；
2. `ignore my earlier preference` 没有删除它明确指向的旧偏好；
3. 控制语句可能进入结构化状态；
4. 非价格文本可能被写入 `budget`；
5. 错误状态继续进入结构化约束、自由文本上下文和检索查询。

本文件是对现有 `docs/state_tracking_optimization_plan_lean.md` 已落地能力的增量修复规格，不得把两份方案从头叠加实施。现有否定、`DONTCARE`、Information Gain、Coverage、多轮翻页和启发式排序逻辑继续保留。

本轮不得顺手调整 BM25、Structured Retriever、RRF、Ranker 权重、Coverage 阈值或提问策略。状态修复与召回优化必须分开评测，避免无法判断指标变化来源。

## 1. 最终决策摘要

本轮采用以下语义原则：

> 默认保留已有偏好；只有用户给出明确的放弃、替换或忽略语义时，才删除被明确指向的旧偏好；任何无关约束都必须保留。

具体决策如下：

1. 普通补充或细化不是全局 override，不得删除其他属性；
2. 同属性替换只更新该属性，保留 category、budget、size、use_case 等无关状态；
3. 官方显式句式 `ignore my earlier preference + what I need is` 触发“引用式覆盖”；
4. 引用式覆盖将一条可识别的旧软偏好移出硬约束，但保留为低优先级软检索信号，不清空全部会话状态；
5. 显式覆盖中的新值不得使用上一轮 `pending_attribute` 推断属性；
6. 控制子句只表达状态操作，不得成为 slot value 或正向检索上下文；被覆盖的旧偏好可以保留为软检索上下文；
7. `budget` 只接受能够确定转换为价格数值的输入，包括可确定解析的英文文本数字；
8. 不引入 LLM、Embedding、NER、Catalog Grounding、额外召回或第三方运行时依赖；
9. 公开 200 条是主要证据，模拟标准模式只做规模和稳定性补充；
10. 不为模拟集未公开的复杂改写堆叠规则。

## 2. 证据范围与基线诊断

### 2.1 必须区分的两类证据

此前报告中的 `synthetic_v2_*` 会话来自模拟测试集，不是公开 200 条。它们只能用于补充说明潜在鲁棒性问题，不能作为本轮增加复杂语言规则的主要依据。

本方案的主要依据来自：

- `data/public_set.jsonl` 中的 200 条公开样本；
- `evaluator/local_evaluator.py` 生成的官方标准对话；
- 当前 `dev@bc792ca`；
- 当前 `configs/final.json`；
- 当前本地冻结的 50,000 商品 Catalog。

公开 JSONL 不直接保存逐轮对话文本。`intent_override` 的旧偏好、新要求、覆盖轮次和消息由官方本地评测器确定性生成。当前公开评测器使用的覆盖模板是：

```text
Actually, ignore my earlier preference. What I need is: {new_value}.
```

因此，本方案只需要稳定覆盖已发布的标准语义结构，不需要实现通用自然语言理解系统。

### 2.2 公开 200 条状态诊断结果

公开集包含 30 条 `intent_override`。对当前版本逐条回放并检查覆盖前后的状态，得到：

| 诊断项 | 当前结果 | 解释 |
| --- | ---: | --- |
| Intent Override 样本数 | 30 | 公开集正式场景 |
| 旧偏好与新要求属于不同属性类型 | 25/30 | 跨属性覆盖是主要情况，不是模拟集特例 |
| override 到来时，pending 与新值预期属性不一致 | 18/30 | 上一轮提问会错误影响覆盖句解析 |
| 覆盖前旧偏好已进入 `active_slots` | 16/30 | 其余旧偏好此前未形成结构化状态 |
| 上述旧偏好在覆盖后仍然保留 | 16/16 | 当前没有实现“earlier preference”的引用删除 |
| 新值只进入错误属性、未进入预期属性 | 至少 11/30 | 典型情况是 material 被写成 feature/budget/color |
| 控制词进入最终 `lexical_terms` | 0/30 | 现有 stopwords 有保护作用 |

最后一项不代表没有状态污染。公开回放中仍观察到控制片段进入结构化 slot；只是这些词在 Query Planner 阶段又被 stopwords 过滤。结构化约束、状态签名和后续解析仍可能受到影响。

以上是状态级诊断，不等同于“16 条一定最终召回失败”。端到端指标必须在修复后使用未经修改的官方 evaluator 重新计算。

## 3. 问题根因

### 3.1 `pending_attribute` 优先级过高

当前 slot 推断顺序大致为：

```text
explicit slot
    ↓
pending_attribute
    ↓
existing value match
    ↓
known vocabulary / clause semantics
```

这个顺序适合普通问答。例如系统刚问 material，用户只回答 `cotton`，使用 pending 是正确的。

但官方 override 是一个完整、自包含的控制句。即使上一轮在问 budget，新句中的 `cotton` 也不能被解释成预算。当前逻辑没有区分“短回答”和“自包含的显式覆盖”，导致 pending 劫持新值。

### 3.2 当前状态操作不能表达“引用式覆盖”

现有 `SET` 只替换目标 slot：

```text
SET material=cotton
```

它不会表达：

```text
删除之前那条软偏好
然后设置 material=cotton
```

当旧偏好在 `feature`、新要求在 `material` 时，只执行 `SET material` 必然保留旧 feature。这不是普通 SET 的错误，而是显式 override 缺少“旧偏好引用”信息。

### 3.3 控制子句被当作开放值

短回答机制允许把词典外文本复制到 pending slot。这对 OOV 属性值很重要，但它也可能把：

```text
Actually, ignore my earlier preference
```

当成一个开放值。控制子句在语义上只描述状态操作，必须在进入隐式短回答路径前被拦截。

### 3.4 `budget` 缺少写入前类型校验

当前 Query Planner 在使用 budget 时尝试 `float(value)`，转换失败后只是不生成价格过滤；错误文本仍然可能已经存在于 `active_slots["budget"]`，继续影响：

- lexical terms；
- structured constraints 之外的状态签名；
- recommendation history reset；
- 后续 override 和问题选择。

正确边界应前移到状态写入阶段：不能证明是价格，就不得提交为 budget 状态。

## 4. 语义契约

实现前必须统一以下语义，测试也按这些不变量编写。

### 4.1 普通补充

示例：

```text
I would also prefer cotton.
For material, cotton would be good.
```

要求：

- 不进入“引用式覆盖”路径；
- 不删除其他 slot；
- 不删除此前没有被明确否定或替换的偏好；
- 同一 slot 内是追加还是替换，继续遵守现有已发布模板和现有状态操作，不在本轮扩张语义代数。

### 4.2 属性级替换

示例：

```text
Make the material cotton instead.
Switch the color to white.
```

要求：

- 只更新被明确指定的 slot；
- 删除该 slot 中被替换的旧值；
- 保留所有无关 slot；
- 不触发全局状态清空。

### 4.3 明确移除

示例：

```text
I no longer need waterproofing.
```

要求：

- 只移除明确命名的值；
- 不自动把它变成负向排除；
- 不影响其他值。

### 4.4 官方引用式覆盖

标准结构：

```text
ignore my earlier preference
what I need is: X
```

要求：

1. 将可识别的“earlier preference”移出硬/结构化约束；
2. 使用 X 自身语义推断 slot；
3. 不使用当前 pending 解释 X；
4. 保留 category、用户画像和其他已确认约束；
5. 控制文本不得写入 slot、context 或 query；旧偏好值本身可以进入低优先级软 context；
6. 如果旧偏好引用无法可靠解析，保守地不删除其他状态，但仍正确设置 X；
7. 禁止为了强行删除旧值而清空全部非 category slot。

## 5. 推荐的最小实现设计

### 5.1 只增加一个窄范围的显式覆盖信号

不要新增通用意图分类器。建议在 `parse_turn` 前后增加一个轻量检测结果：

```text
explicit_reference_override =
    has_ignore_earlier_preference_cue
    AND has_new_requirement_payload
```

第一版只覆盖官方发布的语义组合，例如：

- 放弃提示：`ignore my earlier preference`；
- 新值提示：`what I need is`。

不要在本轮主动加入模拟集中的：

- `discard previous choice`；
- `on second thought`；
- `prioritize ... from now on`；
- 其他未出现在官方发布模板中的复杂改写。

检测应基于规范化后的 cue 组合，而不是按 sample ID、ASIN、新值或完整句子硬编码。

### 5.2 显式覆盖必须隔离 pending

推荐处理顺序：

```text
读取并清空 pending_attribute
    ↓
检测整条消息是否为官方显式覆盖
    ↓ yes
提取 new requirement payload
    ↓
使用 pending=None 解析 payload
    ↓
删除被引用的旧软偏好
    ↓
应用新值 SET 操作
    ↓
清理 context，并按有效状态变化重置推荐历史
```

注意：pending 仍然是一轮提示，收到用户消息后仍需消费并清空；这里只是禁止它参与显式覆盖 payload 的 slot 推断。

普通短回答路径保持不变：

```text
系统问：What material do you prefer?
用户答：cotton
```

此时仍应使用 `pending_attribute=material`。

### 5.3 控制子句必须成为 no-op，而不是开放值

当一个 clause 已被识别为 override 控制部分时：

- 不进入 `_parse_implicit_clause`；
- 不产生 `SET`；
- 不产生 `positive_context`；
- 不进入 budget、feature、other 等任何 slot；
- 不依赖 Query Planner 的 stopwords 做事后补救。

Query Planner 的 stopwords 继续作为防御层，但不能承担状态正确性的主要责任。

### 5.4 使用单条轻量“可覆盖偏好记录”

不引入完整 provenance graph。每个 session 只保存一条当前可被 `earlier preference` 指向的软偏好记录，例如：

```python
@dataclass
class SupersedablePreference:
    source_turn: int
    raw_text: str
    parsed_values: tuple[tuple[str, str], ...]
```

字段含义：

- `source_turn`：偏好来自哪一轮；
- `raw_text`：规范化前的原始偏好片段，用于清理 context；
- `parsed_values`：用现有 value splitter 和 slot typer 得到的精确原子值，用于从 active state 中删除匹配项。

只保存一条记录，不保存完整状态历史，不改变公开 Agent 接口。

#### 捕获规则

第一版只捕获能够语义上作为“先前软偏好”的内容：

- 用户首轮 category 之外的独立偏好片段；
- 没有 `key requirement` 等硬约束提示；
- 不是 `still exploring`、边界回复或控制句；
- 使用现有解析器在 `pending=None` 下提取 canonical values；
- 无法可靠提取时，可以保存 raw text 供 context 清理，但不得据此模糊删除其他 slots。

这条规则对应官方公开 simulator 的会话协议，但不依赖公开样本 ID 和具体商品值。

#### 删除规则

收到显式引用式覆盖后：

1. 只遍历 `SupersedablePreference.parsed_values`；raw text 只用于 context 清理和软信号，不直接据此删除结构化值；
2. 只从结构化状态删除规范化后精确相等的 active value；
3. 从硬约束 context 删除同一记录的旧值，再把旧值规范化后重新放入软 context；
4. 不做全 Catalog 模糊匹配；
5. 不按宽泛 token overlap 删除值；
6. 不删除 category、profile terms 或其他来源的不同值；
7. 降级完成后清空该记录，避免后续重复引用。

如果没有记录或无法把旧文本解析成 canonical value，执行保守降级：不删除其他结构化状态，只正确解析并设置新要求；旧 raw/value 可以作为软 context 保留。

### 5.5 新要求的 slot 推断顺序

显式覆盖 payload 使用独立顺序：

```text
explicit slot label
    ↓
validated price expression
    ↓
stable released vocabulary / deterministic semantic cue
    ↓
existing exact value type
    ↓
feature / other fallback
```

禁止使用 pending。

必须保证至少以下标准值：

- cotton / polyester / nylon / leather / wool → `material`；
- black / white / blue / red 等 → `color`；
- 明确价格表达 → `budget`；
- 已发布模板中的 use case cue → `use_case`；
- 其余商品要求 → `feature` 或 `other`，不得伪造 budget。

不允许为了覆盖公开商品值而扩充一个大型封闭属性词典。

### 5.6 Budget 写入门禁

新增一个确定性的价格解析函数，逻辑上类似：

```python
parse_price_value(text, *, pending_is_budget: bool) -> float | None
```

第一版支持：

- 阿拉伯数字：`80`、`80.5`；
- 货币形式：`$80`、`80 dollars`；
- 已发布价格 cue：`under 80`、`below 80`、`at most 80`、`budget around 80`；
- 确定性的英文文本数字：`eighty`、`one hundred and twenty`；
- 当且仅当 pending 确实为 budget 时，允许纯数字或纯文本数字短回答。

以下输入必须返回 `None`：

- `cotton`；
- `Imported`；
- `prioritize cotton`；
- 混有不可解释控制文本且没有价格语义的字符串；
- 含糊、无法确定转换的口语数量表达。

当返回 `None` 时：

- 不得写入 `active_slots["budget"]`；
- 如果消息属于显式 override，继续用 payload 自身语义判断其他 slot；
- 如果只是普通 budget 回答且无法解析，保守 no-op 或继续澄清，不猜测数值。

英文数字解析采用有限状态/词表累加即可，时间复杂度 O(tokens)，不得引入 NLP 库。预算的“around”与“maximum”是否要形成不同硬过滤关系不在本轮范围；本轮只解决非法 budget 状态写入。

### 5.7 Context 与推荐历史

完成覆盖操作后必须同步维护：

- `active_slots`：删除被引用旧值，设置新值；
- `negative_slots`：不做无关变化；
- `active_context`：移除旧偏好作为硬约束的贡献和所有控制文本，再保留旧偏好的规范化文本作为软检索信号；
- `pending_attribute`：本轮消费后清空；
- recommendation history：仅当有效检索约束确实发生变化时重置；
- `shown_asins`、coverage 状态：沿用现有统一重置方法，不增加第二套逻辑。

不要直接拼接原始消息历史构造查询。原始 `messages` 可以继续用于审计，但 Query Planner 只能消费当前有效状态。

## 6. 预计代码影响范围

### 6.1 `shopping_copilot/core/contracts.py`

- 为 `SessionState` 增加一条轻量可覆盖偏好记录，或等价的少量字段；
- 不改变 Agent 对外接口；
- 不增加 Catalog、模型或检索器引用。

### 6.2 `shopping_copilot/state/semantic_delta.py`

- 增加官方显式引用式覆盖检测；
- 提取新要求 payload；
- 控制 clause 直接 no-op；
- 显式覆盖 payload 使用 `pending=None`；
- 增加确定性价格/英文数字解析；
- 保留现有 SET、EXCLUDE、REMOVE、ALLOW、DONTCARE 行为。

除非实现证明现有 `TurnDelta` 无法承载最小元数据，否则不要扩张为新的大型操作代数。推荐在 `TurnDelta` 中增加窄范围 metadata，而不是让所有模块理解一个新全局 override 类型。

### 6.3 `shopping_copilot/state/rule_state.py`

- 捕获并保存单条 supersedable preference；
- 在显式引用式覆盖时将旧记录匹配值从硬约束降级为软 context；
- 清理对应 active context；
- 保留无关状态；
- 复用现有 recommendation history reset。

### 6.4 `shopping_copilot/planning/query_planner.py`

- 保留现有 `float()` 防御；
- 确认无非法 budget 文本进入 lexical terms；
- 不在 Planner 中复制一套价格解析和 override 语义；
- 不修改路线权重和 candidate_k。

### 6.5 测试文件

优先扩展：

- `tests/test_semantic_state.py`；
- `tests/test_team_pipeline.py`；
- 必要时新增一个专门的官方 override 回归测试文件。

禁止修改：

- `evaluator/local_evaluator.py`；
- `data/public_set.jsonl`；
- Catalog；
- 公开 ground truth；
- 用于最终对比的评测配置。

## 7. 开发步骤

### Step 0：冻结基线

在任何代码修改前保存：

- 当前 commit；
- 当前 `configs/final.json`；
- 公开 200 条完整结果；
- 四个 scenario 的分组指标；
- 30 条 override 的状态诊断；
- P50/P95 单轮和 session 延迟；
- 当前测试结果。

基线报告必须标明数据来源，禁止把公开集和模拟集汇总成一个数字。

### Step 1：实现价格解析门禁

先独立实现和测试数值/英文数字解析，不接触 override 删除逻辑。确保：

- 合法数字进入 budget；
- 文本数字能够标准化；
- 非价格文本不能进入 budget；
- 当前阿拉伯数字预算行为不回归。

### Step 2：实现控制子句识别与 pending 隔离

实现官方显式覆盖检测：

- 控制 clause no-op；
- payload 使用 `pending=None`；
- 新值写入正确 slot；
- 普通短回答继续使用 pending。

完成后先跑 unit tests，不立即调整任何 ranking 参数。

### Step 3：实现单条可覆盖偏好记录

- 首轮捕获软偏好记录；
- 明确 override 时删除精确匹配值；
- 清理旧 context；
- 保留无关约束；
- 删除失败时保守降级。

### Step 4：状态与管线回归

检查：

- active/negative slot 不变量；
- no-preference 行为；
- category、budget、size 保留；
- intent mode；
- recommendation history reset；
- Coverage 多轮翻页；
- Response contract。

### Step 5：公开 200 条一次性主验收

只有 unit/integration tests 全部通过后，才运行公开 200 条。不得根据单个 public ID 增加特例再反复调整。

### Step 6：模拟标准模式补充验收

只运行与官方相近的标准措辞模式，确认：

- 更大商品重复度下没有新增污染；
- 整体指标没有明显回归；
- 性能仍满足门槛。

复杂自然语言压力模式不作为本轮合入条件。

## 8. 测试规格

### 8.1 显式跨属性覆盖

初始状态：

```text
category=shirts
feature=Imported
color=blue
size=M
pending=budget
supersedable preference=Imported
```

输入：

```text
Actually, ignore my earlier preference. What I need is: cotton.
```

期望：

```text
category=shirts
material=cotton
color=blue
size=M
feature 不再包含 Imported
budget 不包含 cotton
旧偏好可保留在 active_context 作为软信号，但不得进入 active_slots 或 structured_constraints
active_context 不包含控制句
```

### 8.2 同属性引用式覆盖

```text
material=leather
pending=color
supersedable preference=leather
```

输入：

```text
Actually, ignore my earlier preference. What I need is: cotton.
```

期望：

```text
material=[cotton]
color 不包含 cotton
material 不包含 leather
```

### 8.3 普通补充不得误删

```text
material=leather
feature=waterproof
```

输入：

```text
For color, blue would be good.
```

期望：

```text
material=leather
feature=waterproof
color=blue
```

没有显式 discard cue 时，不得使用 supersedable preference 删除旧值。

### 8.4 普通 pending 短回答保持有效

```text
pending=material
用户输入=cotton
```

期望：`material=cotton`。

### 8.5 Pending budget 不得劫持非数字

```text
pending=budget
用户输入=cotton
```

期望：

- `budget` 不新增值；
- 若是显式 override payload，`cotton` 进入 material；
- 若只是无法理解的普通回答，保守 no-op 或继续澄清。

### 8.6 文本数字预算

至少覆盖：

| 输入 | 上下文 | 期望 |
| --- | --- | ---: |
| `80` | pending=budget | 80 |
| `$80` | 无 pending，但有货币 cue | 80 |
| `eighty` | pending=budget | 80 |
| `under eighty dollars` | 无 pending | 80 |
| `one hundred and twenty` | pending=budget | 120 |
| `cotton` | pending=budget | 不写入 |
| `Imported` | pending=budget | 不写入 |

### 8.7 控制文本隔离

官方标准 override 后必须保证：

- `active_slots` 不包含 `actually`、`ignore my earlier preference`；
- `active_context` 不包含控制句；
- `lexical_terms` 不包含控制词；
- `structured_constraints` 不包含控制词；
- 新值仍然正常进入查询。

### 8.8 保守降级

当系统没有可覆盖偏好记录时，收到官方 override：

- 不清空所有旧 slots；
- 正确解析并设置新要求；
- 保留 category 和无关已确认约束；
- 记录可诊断信息，但不暴露异常给 evaluator。

## 9. 评测方案

### 9.1 公开 200 条主报告

必须输出：

| 指标组 | 指标 |
| --- | --- |
| 总体 | Hit Rate@10、MRR、MTTC、Technical Score |
| 分场景 | buying、browsing、intent_override、boundary 各自指标 |
| 状态正确性 | 旧偏好残留、新值错误 slot、非法 budget、控制文本污染 |
| 效率 | 单轮 P50/P95、session P50/P95、总运行时间 |
| 回归迁移 | hit→miss、miss→hit、提前命中、延迟命中 |

不得只报告整体分数。整体分数可能掩盖 override 改善和其他场景回归。

### 9.2 模拟标准模式补充报告

模拟报告单独列出：

- 样本数和生成版本；
- 标准措辞模式；
- 与公开集相同的核心指标；
- 状态污染统计；
- 运行时间。

不得把 synthetic 和 public 会话合并计算单一指标，也不得把复杂改写失败作为继续扩张规则的理由。

### 9.3 推荐的评测顺序

```text
unit tests
    ↓
integration tests
    ↓
公开 30 条 override 状态回放
    ↓
公开 200 条完整 evaluator
    ↓
模拟标准模式
    ↓
性能对比与最终决策
```

## 10. 性能与复杂度约束

本方案只能增加：

- 每条用户消息一次小规模 cue 检测；
- 一次有限英文数字解析；
- 对当前 session 少量 active values 的精确比较；
- 每个 session 一条轻量 preference record。

理论复杂度：

```text
O(message_tokens + active_state_values)
```

禁止增加：

- 每轮 Catalog 全量扫描；
- 新的 BM25/Structured/Dense 检索；
- LLM 或网络请求；
- Embedding 编码；
- 大型属性值词典构建；
- 模糊字符串搜索全状态历史；
- 随会话轮数无限增长的 provenance graph。

建议性能门槛：

- 公开 200 条总运行时间增幅不超过 5%；
- 单轮 P95 增幅不超过 5%；
- 同时记录绝对毫秒，避免将极小时间噪声误判为回归；
- 如环境波动明显，基线和新版本各运行至少 3 次，比较中位数。

## 11. 合入验收标准

### 11.1 必须满足的状态门槛

- 公开标准 override 不再被 pending 写入错误 slot；
- 当前已观察到的 11 条“新值只在错误 slot”目标降为 0；
- 覆盖前可精确识别的旧软偏好在覆盖后不再残留于硬约束；如保留在 `active_context`，只能作为软检索信号；
- 非数值文本进入 budget 的数量为 0；
- 控制文本进入 active slot/context 的数量为 0；
- category 和无关约束保留率为 100%；
- 普通补充、普通短回答、否定和 DONTCARE 测试不回归。

### 11.2 必须满足的指标门槛

- 公开 200 条整体 Hit Rate@10 不下降；
- 公开 200 条整体 MRR 不下降；
- intent_override 分组 Hit Rate@10、MRR、MTTC 至少不劣于基线，目标是改善；
- buying、browsing、boundary 不出现由本改动导致的明显回归；
- hit→miss 不得由错误状态清理引起；
- 性能满足第 10 节门槛。

如果状态正确性显著提高但最终检索指标没有提高，不得立即修改 Ranker 掩盖问题。先检查：

1. 新值是否进入正确 query；
2. 旧值是否真的从 query 和 structured constraints 消失；
3. 候选池是否包含目标；
4. 之后再另立候选召回或排序实验。

## 12. 明确不做的事项

本轮不做：

- 通用自然语言 override 分类器；
- 模拟复杂 paraphrase 全覆盖；
- 完整值级 provenance 和置信度系统；
- Catalog 属性本体构建；
- Budget 软区间/硬上限的完整语义重构；
- Structured Retriever、RRF 或多路探索；
- Ranker、Policy、Information Gain、Coverage 参数调整；
- 根据公开 sample ID、target ASIN 或商品标题写特例；
- 修改官方 evaluator、测试集或 ground truth。

这些事项如后续确有独立证据，应分别建立实验文档，不得混入本修复。

## 13. 风险与回滚

### 风险 1：错误删除有效旧约束

控制方式：只删除单条 supersedable preference 中精确匹配的 canonical values；无法确认则不删，禁止全局清空。

### 风险 2：显式 override 检测误触发

控制方式：要求“放弃旧偏好 cue”和“新要求 payload cue”同时出现；普通 `actually` 或普通 `what I need is` 单独出现时不触发引用删除。

### 风险 3：词典分类再次过拟合公开商品

控制方式：只保留已存在的稳定小词表和确定性 cue；未知值复制到 feature/other，不根据公开 ASIN 增加特例。

### 风险 4：文本数字解析范围过大

控制方式：只支持确定性英文数字语法；解析失败返回 `None`，不猜测。

### 回滚策略

实现应集中在状态层并保持提交边界清晰：

1. 价格门禁独立提交；
2. pending 隔离与控制句 no-op 独立提交；
3. supersedable preference 删除独立提交；
4. 测试和报告独立提交或与对应功能同行。

如果公开指标或性能不满足门槛，可以按功能提交逐项回滚，不影响现有召回和排序模块。

## 14. 最终交付物

实施完成后必须交付：

1. 状态层代码改动；
2. 单元测试和集成测试；
3. 公开 30 条 override 状态诊断前后对比；
4. 公开 200 条完整指标报告；
5. 模拟标准模式补充报告；
6. 性能前后对比；
7. 变更文件清单；
8. 已知限制与未实施项；
9. 可独立回滚的提交记录。

## 15. 执行检查清单

### 开发前

- [ ] 确认分支为 `dev`
- [ ] 记录 HEAD 和 `configs/final.json`
- [ ] 保存公开 200 条基线
- [ ] 保存公开 30 条 override 状态诊断
- [ ] 确认没有修改 evaluator、公开集和 Catalog

### 实现中

- [ ] Budget 只接收可解析数值
- [ ] 文本数字能够确定性转换
- [ ] 控制 clause 不产生状态
- [ ] 显式 override payload 不使用 pending
- [ ] 只维护一条 supersedable preference
- [ ] 只删除精确匹配旧值
- [ ] 无关状态完整保留
- [ ] 不修改召回、排序和 Policy 参数

### 验收时

- [ ] 全部 unit/integration tests 通过
- [ ] 公开 30 条状态污染达到门槛
- [ ] 公开 200 条整体和分场景指标已对比
- [ ] 模拟集只运行标准模式
- [ ] 性能增幅在门槛内
- [ ] 无 sample ID、ASIN 或具体标题硬编码
- [ ] 结果来源标注明确，public 与 synthetic 分开报告
