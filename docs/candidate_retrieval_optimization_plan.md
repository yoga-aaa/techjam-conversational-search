# 候选召回与语义检索优化技术方案

> 状态：Proposed，待按阶段实施与评测后决定是否进入 `configs/final.json`
>
> 适用基线：`dev`，`c3f1b77`，并包含 Lean v2 语义状态实现的当前工作区
>
> 目标 Catalog：固定 50,000 条 `Clothing_Shoes_and_Jewelry` 商品
>
> 主方案：SQLite FTS5 多查询召回 + 字段感知约束 + RRF 融合
>
> 当前阶段约束：不使用 LLM，不新增第三方运行时依赖，不修改官方 Agent 接口和 Pipeline 顺序

本文件是 `docs/state_tracking_optimization_plan_lean.md` 完成后的候选层后续方案，不替代或放宽 Lean v2。若两者发生冲突，以 Lean v2 的 canonical state、negative constraint 和 Ranker final guard 不变量为准。

## 1. 决策摘要

当前最适合本系统的优化，不是直接引入 X Phoenix、向量数据库或大型语义模型，而是在已经正确的 canonical state 和现有 SQLite FTS5 索引之间增加一个轻量的“多查询候选生成”层：

1. 从同一个 `SearchPlan` 确定性地产生最多 5 条互补的 FTS5 查询；
2. 同时覆盖全量严格匹配、slot 间 AND、字段感知和宽松 OR fallback；
3. 每条查询独立召回更深的候选，但最终仍只向现有 Ranker 输出 `candidate_k` 个候选；
4. 使用无监督 Reciprocal Rank Fusion（RRF）融合各查询排名；
5. 所有候选继续经过共享 negative matcher 和 Ranker final guard；
6. 保持 Information Gain、Coverage、dynamic constraint weighting、rarity weighting 和响应逻辑不变。

目标数据流为：

```text
canonical SessionState
        |
        v
existing RuleQueryPlanner
        |
        v
      SearchPlan
        |
        v
LexicalQueryVariantBuilder                  仅产生瞬时查询，不写回 state
  | strict_all_tokens
  | slot_phrase_and
  | fielded_slot_and
  | category_anchor
  ` broad_or                              永远保留的 fallback
        |
        v
SQLite FTS5 parallel/sequential fan-out      每路只读取有限深度
        |
        v
shared hard-filter / negative matcher
        |
        v
RRF merge + deterministic de-duplication
        |
        v
existing HybridRetriever
        |
        v
existing HeuristicRanker + final guard
        |
        v
existing Policy -> Coverage -> Response
```

这是一项候选层优化。它不替换状态理解，也不改变用户意图和属性在 `SessionState` 中的表达。

## 2. 当前系统与问题基线

### 2.1 当前组件关系

当前官方执行顺序是：

```text
StateTracker
  -> QueryPlanner
  -> Retriever.probe
  -> Policy.before_search
  -> Retriever.retrieve
  -> Ranker
  -> CandidateCoverageManager
  -> Policy.after_ranking
  -> ResponseBuilder
```

与本方案直接相关的实现如下：

| 位置 | 当前行为 | 必须保留的性质 |
| --- | --- | --- |
| `state/rule_state.py` | 维护 canonical positive/negative state | Lean v2 五类操作和属性级 override 不变 |
| `planning/query_planner.py` | 将有效 state 压成一组 `lexical_terms` | 只从当前正向状态构造查询，负向值不进入查询 |
| `retrieval/bm25.py` | 将所有 terms 拼为一个 OR 表达式 | 继续使用同一 FTS5 索引和列权重 |
| `retrieval/structured.py` | 可选 postings-based 属性召回 | 功能代码保留，默认仍关闭 |
| `retrieval/hybrid.py` | BM25、Structured、Dense 的 RRF 融合点 | Retriever 接口和融合扩展点保留 |
| `ranking/heuristic.py` | 候选集内启发式重排 | 权重、rarity、dynamic weighting 和 final guard 不变 |
| `policy/information_gain.py` | 根据候选分布选问题 | priors 和 threshold 不变 |
| `policy/coverage.py` | 停滞时轮换未展示结果 | 状态签名与轮换算法不变 |

### 2.2 直接根因

当前 `BM25Retriever._expression()` 的实际语义是：

```text
term_1 OR term_2 OR ... OR term_n
```

这会产生三个问题：

1. 常见 category、性别、材质词命中数过大，单个罕见且重要的属性被稀释；
2. 不同 slot 之间没有“应该共同满足”的召回语义；
3. 排序前只保留统一的 Top-K，目标商品即使可被 BM25 找到，也可能在 Ranker 运行前被截断。

本地当前评测快照为：

| 指标 | 当前值 |
| --- | ---: |
| Overall HitRate@10 | 0.995 |
| Overall MRR | 0.743752 |
| Buying HitRate@10 | 0.9875 |
| Browsing HitRate@10 | 1.0 |
| Intent Override HitRate@10 | 1.0 |
| Boundary HitRate@10 | 1.0 |

`public_0020` 是唯一完全 miss。诊断显示它的 canonical state 和 negative state 正确，但目标在不同轮次的单路 BM25 排名约为 117–281，而当时传入 Ranker 的候选预算为 60 或 100。对第一轮使用所有有效 term 的 AND 查询时，目标可上升到约第 44 位。这证明首先应优化查询生成和候选召回，而不是继续修改 state 或 Ranker 系数。

该样本只用于定位失败层，不作为参数或词典的训练样本。本方案的任何规则都不得引用其 ASIN、完整标题或特有属性值。

## 3. 目标、非目标与硬不变量

### 3.1 目标

优先级从高到低：

1. 提升 Ranker 之前的 Candidate Recall@K；
2. 让共同满足多个有效 slot 的商品不再被宽泛 OR 查询挤出；
3. 保持或提升官方 HitRate@10、MRR 和 MTTC；
4. 对 OOV 属性值、同义模板和不同商品类别具有可验证的泛化性；
5. 维持离线、确定性、低依赖和可回滚。

### 3.2 非目标

本阶段不做：

- 不重新设计状态解析或五类状态操作；
- 不修改 Information Gain priors、Coverage threshold、ranking coefficient；
- 不把 positive constraints 变成硬过滤条件；
- 不放宽任何明确的 negative constraint；
- 不训练或调用 LLM、双塔、Cross-Encoder、ColBERT；
- 不引入 Faiss、Elasticsearch、向量数据库或第三方 tokenizer；
- 不直接移植 X Phoenix 的模型、训练栈或 engagement scorer；
- 不构造完整 Catalog Grounder；
- 不修改 `evaluator/`、Catalog、`data/public_set.jsonl` 或公开标签。

### 3.3 硬不变量

- `Agent.reset/respond` 和响应结构不变；
- `core/pipeline.py` 的组件执行顺序不变；
- factory 中仍是 lexical/dense/structured 注入 `HybridRetriever` 的拓扑；
- `SearchPlan` 仍是 Retriever 的唯一输入，Query Variant 仅为检索内部瞬时对象；
- 查询只能读取 canonical positive state 的投影；
- `negative_slots` 仍是负向事实来源；
- 所有路线继续使用 `catalog/constraints.py` 的共享 matcher；
- Ranker final guard 必须保留，不能假设前置过滤永远正确；
- `candidate_k` 仍表示返回给后续 Ranker 的候选上限；
- tie-break 必须确定性，不能依赖 set/dict 的偶然遍历顺序；
- Agent 运行时不读取 public target、scenario label 或 ground truth。

## 4. 技术选型

### 4.1 方案对比

| 方案 | Candidate Recall | 泛化 | 依赖/复杂度 | 当前决策 |
| --- | --- | --- | --- | --- |
| 只增大 `candidate_k` | 中等，不能修复查询表达 | 一般 | 低，但 Ranker/IG 成本扩大 | 不作为主方案 |
| 只提高 BM25 列权重 | 不稳定，仍是单条 OR | 较差，容易针对公开集调参 | 低 | 拒绝 |
| 直接启用现有 StructuredRetriever | 当前大规模 tie，曾有公开回归 | 一般 | 低 | 保留代码，暂不启用 |
| FTS5 多查询 + RRF | 高，直接覆盖候选截断 | 高，无监督、无词典 | 低 | **当前主方案** |
| 双塔 Dense + ANN | 可补词面不匹配 | 取决于训练数据 | 中高，需要模型和新依赖 | 条件式后续项 |
| ColBERT/Cross-Encoder | 精排能力强 | 取决于训练域 | 高，不解决首次召回时不能单独使用 | 条件式后续项 |
| 直接移植 X Phoenix | 与商品 query 任务标签不一致 | 低 | 很高 | 拒绝 |

### 4.2 为什么选择 SQLite FTS5 多查询

当前 Catalog 已经完整建立 FTS5 索引，FTS5 原生支持：

- phrase；
- `AND`、`OR`；
- column filter；
- 每列 BM25 权重；
- Unicode61 tokenization。

因此可以在不引入新索引、不复制 Catalog、不改变产品模型的前提下，得到接近工业“多候选源 fan-out”的效果。SQLite 官方文档明确支持 phrase、Boolean operator、column filter 和加权 BM25：[SQLite FTS5](https://www.sqlite.org/fts5.html)。

### 4.3 为什么选择 RRF

不同 FTS5 查询的原始 BM25 分值不可直接比较：严格 AND、字段过滤和宽松 OR 的匹配集合及 IDF 都不同。RRF 只依赖每路排名，能避免手工归一化不同查询的 BM25 数值。

本系统的 `HybridRetriever` 已使用：

```text
1 / (60 + rank)
```

新层继续复用相同公式和 `k=60`，不引入第二套融合语义。RRF 的原始工作也采用这种按排名聚合的无监督方法：[Cormack et al., SIGIR 2009](https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf)。

### 4.4 为什么当前不选择 Dense

当前只有 200 条 public session label，且不能用公开 target 反复训练。直接使用通用 embedding 会带来三个不可控变量：

- query-product domain mismatch；
- 模型版本和第三方运行时依赖；
- lexical 精确属性、否定约束和商品相关性的权衡。

Amazon 的商品搜索工作证明双塔语义召回有效，但也强调需要 query-product 交互数据、预训练/蒸馏和独立相关性验证：[Semantic Product Search](https://www.amazon.science/publications/semantic-product-search)、[Web-scale Semantic Product Search](https://www.amazon.science/publications/web-scale-semantic-product-search-with-large-language-models)。这些条件目前尚不具备，所以 Dense 是升级路径，不是首个实现阶段。

## 5. 目标架构

### 5.1 组件边界

```text
RuleQueryPlanner
  input : SessionState
  output: SearchPlan
  responsibility:
    - canonical positive terms
    - structured_constraints
    - hard_filters
    - excluded_terms projection

BM25Retriever
  input : SearchPlan
  internal:
    - QueryVariantBuilder
    - FTS5 executor
    - shared candidate filter
    - lexical RRF accumulator
  output: RetrievalResult, at most plan.candidate_k candidates

HybridRetriever
  input : lexical + optional structured + optional dense results
  responsibility:
    - route-level union and RRF
    - no state parsing and no negative semantics

HeuristicRanker
  input : state + plan + candidate result
  responsibility:
    - existing lexical/constraint/profile score
    - existing rarity and dynamic weighting
    - final negative guard
```

### 5.2 新增瞬时类型

新增 `shopping_copilot/retrieval/query_variants.py`，定义仅在一次检索调用中存在的对象：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LexicalQueryVariant:
    name: str
    expression: str
    weight: float = 1.0
```

要求：

- 不把 variants 写入 `SearchPlan` 或 `SessionState`；
- 不添加 confidence、ground-truth hint 或 target-specific boost；
- `name` 只允许固定枚举值；
- v1 所有 route weight 都是 `1.0`；
- 相同 expression 去重并保持稳定顺序；
- 最多生成 5 条查询，防止随 slot 数量组合爆炸。

## 6. Query Variant 设计

### 6.1 输入语义

只使用：

- `plan.lexical_terms`；
- `plan.structured_constraints`；
- `plan.route`；
- `plan.hard_filters` 和 `plan.excluded_terms` 只用于候选过滤，不进入正向 FTS 表达式。

不读取：

- `state.messages`；
- public labels；
- target ASIN；
- 原始被否定或被 override 的旧值。

### 6.2 slot 组合语义

Lean v2 中一次 `SET` 的多个 value 表示该 slot 当前可接受集合，因此查询组合规则固定为：

```text
同一 value 的多个 token：
  phrase route   -> phrase
  relaxed route  -> token AND token

同一 slot 的多个 value：OR
不同 slot：AND
```

例如：

```text
category = ["walking shoes"]
color    = ["black", "navy"]
material = ["merino wool"]
```

产生的 slot-level 逻辑为：

```text
"walking shoes"
AND ("black" OR "navy")
AND "merino wool"
```

relaxed value route 为：

```text
("walking" AND "shoes")
AND ("black" OR "navy")
AND ("merino" AND "wool")
```

### 6.3 字段映射

复用 Catalog 现有 FTS 列，不创建第二份属性索引：

| State slot | 首选 FTS 列 |
| --- | --- |
| `category` | `categories` |
| `brand` | `store`, `title` |
| `material` | `title`, `features`, `details`, `description` |
| `color` | `title`, `features`, `details`, `description` |
| `size` | `title`, `features`, `details`, `description` |
| `style` | `title`, `features`, `details`, `description` |
| `feature` | `title`, `features`, `details`, `description` |
| `use_case` | `title`, `features`, `description`, `categories` |
| `other` | 所有可搜索列 |
| `budget` | 不进入 FTS，继续使用 `price_max` |

字段映射只约束“到哪里查”，不声明 Catalog 值已被完整 ground。最终正向相关性仍由现有 Ranker 判断。

### 6.4 固定查询族

按以下顺序生成，缺少适用输入时跳过，最后去重：

#### A. `strict_all_tokens`

把所有唯一正向 token 用 AND 连接：

```text
"novelty" AND "women" AND "cotton"
```

用途：保护同时覆盖全部已知词面的商品。只有至少两个有效 token 时生成。

#### B. `slot_phrase_and`

slot 间 AND；同 slot 多 value OR；多词 value 使用 phrase。

用途：保留 `merino wool`、`rose gold`、`machine washable` 等词组语义。至少两个非 budget slot 时生成。

#### C. `fielded_slot_and`

与 B 相同，但应用字段过滤。例如：

```text
categories : "shoes"
AND {title features details description} : "waterproof"
```

用途：避免商品长 description 中偶然出现 `women`、`shoe` 等词就冒充 category/attribute 匹配。

#### D. `category_anchor`

如果存在 category，则使用 category group AND 其他全部正向 term 的 OR group：

```text
categories : "novelty"
AND ("cotton" OR "grey" OR "imported")
```

用途：严格 slot AND 因某个 Catalog 字段缺失而为空时，仍保持品类锚点并恢复候选。

#### E. `broad_or`

保留当前行为：

```text
"novelty" OR "women" OR "cotton" OR "grey" OR "imported"
```

用途：永远存在的 recall fallback，也继续作为 `probe()` 的 broadness 基准。

### 6.5 FTS5 安全构造

不得把原始 value 直接拼入 MATCH 表达式。构造步骤固定为：

1. 调用 `catalog.constraints.normalized_tokens()`；
2. 丢弃空 token；
3. 每个 token/phrase 使用一个共享 `_fts_quote()`；
4. 只由代码插入列名、括号和 Boolean operator；
5. 列名来自固定映射，不能来自用户字符串；
6. 空 group 不生成 variant；
7. expression 完全相同的 variant 只执行一次。

这同时避免 FTS 语法错误、操作符注入和 Planner/检索 tokenization 漂移。

### 6.6 Buying 与 Browsing

不为两种 intent 维护两套查询代码。只使用轻量的生成条件：

- Buying 或已存在两个以上有效 slot：生成完整查询族；
- Browsing 且只有 category：只生成 fielded category（若与 broad 不同）和 `broad_or`；
- Browsing 随后积累 material/feature/color 等 slot 后，自动进入完整查询族；
- 所有 intent 都保留 `broad_or`。

这样既不会让首轮宽泛 Browsing 执行无意义的 5 路相同查询，也不改变现有 intent 权重。

## 7. 召回执行与预算

### 7.1 两个不同的 K

必须区分：

```text
route_fetch_k: 每条内部 query variant 从 SQLite 读取的深度
candidate_k:   融合后传给现有 Ranker 的最大候选数
```

v1 默认：

```python
route_fetch_k = min(
    len(store.products),
    max(plan.candidate_k, 4 * plan.candidate_k),
)
```

也就是继续复用当前 BM25 已有的 `4 * candidate_k` 深取逻辑，但把它真正用于每个互补查询，而不是只服务于单路过滤补位。

约束：

- 不把 `candidate_k` 默认值从 100 直接扩大；
- early clarification path 把 `candidate_k` 改为 60 时，Retriever 继续遵守 60 的最终输出上限；
- 最多 5 路，因此正常 turn 最多读取约 `5 * 400 = 2,000` 行；
- 先用基准测试确认 p50/p95，再决定是否需要更低的 max variant 或 fetch multiplier；
- 不根据某个 public target 的已知排名设置门槛。

### 7.2 路线执行

每条 variant 执行相同 SQL 模板：

```sql
SELECT parent_asin,
       bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0)
FROM products
WHERE products MATCH ?
ORDER BY 2
LIMIT ?
```

列权重保持原值，不在同一改动中调参。Python 的单个 SQLite connection 不适合无条件跨线程并发，因此 50,000 Catalog 的 v1 采用同 connection 顺序执行。只有实测 SQLite 查询成为 p95 瓶颈时，才评估独立只读 connection 的并发方案。

### 7.3 前置过滤

每路结果在进入融合前执行：

1. Catalog ID hydration；
2. `price_max`；
3. `matches_any_excluded_term(product, plan.excluded_terms)`；
4. 去除无效记录。

明确负向过滤导致候选不足时，不允许关闭或放宽 negative constraint。可以由宽松正向 route 补候选，但补出的商品仍必须通过同一 matcher。

### 7.4 路线失败与 fallback

- 某个严格 route 返回 0：继续其他 routes；
- 某个 variant 与 broad expression 重复：去重，不重复执行；
- 所有严格 routes 为空：`broad_or` 保持当前行为；
- 无任何正向 term：维持当前空 probe/clarification 行为；
- FTS expression 构造应通过 normalization 保证合法；若运行时仍发生 `sqlite3.OperationalError`，记录 variant 名和错误并继续 broad fallback，但不能吞掉非查询语法类的初始化错误。

## 8. 融合与候选分值

### 8.1 Lexical 内部 RRF

对每个候选：

```text
lexical_rrf(candidate) = sum(
    route_weight(route) / (60 + rank_in_route)
)
```

v1 固定：

```text
route_weight = 1.0
rrf_k = 60
```

不根据 public 指标为 strict/category/broad 分别调权。候选在多个独立查询中都排得靠前，本身就是更强的相关性证据。

### 8.2 Candidate 映射

融合后生成：

```python
Candidate(
    parent_asin=asin,
    lexical_score=lexical_rrf_score,
    source_routes=(
        "bm25:broad_or",
        "bm25:strict_all_tokens",
        # ...
    ),
)
```

最终确定性排序键：

```text
1. lexical_rrf_score 降序
2. 命中的 lexical route 数量降序
3. parent_asin 升序
```

只保留前 `plan.candidate_k` 个。

### 8.3 为什么不在候选层再加一套 constraint coefficient

现有 Ranker 已经计算：

- 正向 constraint coverage；
- dynamic constraint weight；
- rarity weight；
- profile score。

候选层再引入 `0.3 * slot_coverage + 0.7 * RRF` 会制造第二套调参系统，并可能过度过滤只存在语义等价表达的商品。因此 v1 的 slot 信息只用于构造互补查询，不再增加新的连续权重。

如果后续诊断发现“目标已进入某一路 fetch，但被 RRF 融合截掉”是主要通用失败，才升级为 coverage-preserving reservoir，并先增加对应 metamorphic test。该层不在 v1 预先实现。

### 8.4 与现有 HybridRetriever 的关系

两级 RRF 分工不同：

```text
BM25Retriever 内部：融合多个 lexical query variant
HybridRetriever：   融合 lexical、structured、dense 三种候选源
```

不得把 lexical variant 伪装为多个顶层 Retriever，也不得在 Hybrid 中复制 FTS 查询生成逻辑。`HybridRetriever` 只需保留更细的 `source_routes` 标签；其现有 lexical/structured/dense 拓扑不变。

## 9. 与现有模块的兼容设计

### 9.1 State 与 Planner

不改变五类状态操作。Planner 仍输出一份 canonical plan。Query Variant Builder 是 canonical plan 的纯投影，因此：

- override 后旧值不会出现在任何 variant；
- negative value 不会进入任何正向 variant；
- `ALLOW`/`REMOVE`/`DONTCARE` 的当前语义不变；
- OOV value 只要已正确进入 active slot，就能通过统一 tokenizer 参与召回。

### 9.2 Probe 与 Information Gain

`probe()` 继续只用 `broad_or` 计算：

- `candidate_count`；
- `category_count`；
- `top_score_gap`。

原因是 Policy 当前把 probe 当作“原始请求有多宽”的信号。若改成 strict route 的 count，可能让同一个请求突然绕过 clarification，属于 Policy 行为变更。

Information Gain 路径在 early decision 后仍把 `candidate_k` 设为 `question_candidate_limit`，多查询 Retriever 只改善这 60 个候选的质量，不修改问题 priors、coverage 或 score threshold。

### 9.3 Ranker

Ranker 不修改公式和配置。需要验证的唯一交互是：

- `lexical_score` 从单路原始 BM25 变成 lexical RRF；
- Ranker 现有 max normalization 仍然适用；
- rarity 的 document frequency 仍在实际候选集内计算；
- `violates_negative_slots()` final guard 保留。

如果 MRR 回归，应先判断是融合后的 lexical score 改变候选次序，还是候选集变大/变质；不能先调 ranking coefficient。

### 9.4 Coverage

Coverage 仍观察 Ranker 输出，不参与首次候选生成。它继续：

- 以正向、负向和 no-preference signature 判断状态是否变化；
- 在真正停滞时将 unseen candidate 提前；
- 不为了修复候选召回而修改 overlap threshold。

### 9.5 StructuredRetriever

现有代码完整保留，`configs/final.json` 中仍默认关闭。完成 lexical fan-out 后单独消融：

1. multi-query lexical only；
2. multi-query lexical + existing structured；
3. 对比 Candidate Recall、MRR、latency 和 tie 分布。

只有 Structured 在多个场景上产生稳定增益才启用。不得因单个 session 打开或删除该 owner 功能。

## 10. 配置设计

为支持实验、回滚和消融，建议给 `SearchConfig` 增加：

```json
{
  "search": {
    "lexical_fanout_enabled": true,
    "lexical_max_variants": 5,
    "lexical_fetch_multiplier": 4,
    "lexical_rrf_k": 60
  }
}
```

解析约束：

```text
lexical_max_variants:       1..5
lexical_fetch_multiplier:   1..8
lexical_rrf_k:              >= 1
```

实施时先新增：

```text
configs/experiments/multiquery_lexical.json
```

不要在首个实现 diff 中修改 `configs/final.json`。只有完整验收通过后，再由明确的 promotion 变更开启 final 配置。

若必须严格保持 factory 当前调用文本，可让 `BM25Retriever` 的第二个 settings 参数带默认值；更推荐由 factory 显式传入 `config.search`，但组件拓扑仍保持 `BM25 -> HybridRetriever`。因为这涉及 `core/config.py` 和 `core/factory.py`，需要按 ownership 文档完成 Member 3 与 retrieval owner 的集成 review。

## 11. 可观测性与失败分层

### 11.1 Ground-truth-free trace

仅在 trace 开启时增加融合后候选的 ground-truth-free 信息：

```json
{
  "lexical_variant_count": 4,
  "candidate_source_counts": {
    "bm25:strict_all_tokens": 55,
    "bm25:slot_phrase_and": 42,
    "bm25:fielded_slot_and": 37,
    "bm25:broad_or": 100
  },
  "multi_route_candidate_count": 31,
  "retrieved_candidate_ids": ["..."]
}
```

trace 不能包含 ground truth、target rank 或 scenario label。`retrieved_candidate_ids` 只用于本地离线 join，final config 默认关闭 trace。

### 11.2 离线诊断脚本

新增 `scripts/analyze_candidate_recall.py`。它在 analysis 边界重放公开 session，并调用 `BM25Retriever.diagnose(plan)` 读取与正式 `retrieve()` 共用执行函数产生的每路候选 ID；随后才与 public labels join。`diagnose()` 不加入 `Retriever` Protocol，不被 Agent Pipeline 调用，也不能维护另一套查询或过滤实现。

脚本输出：

| 层级 | 指标 | 失败含义 |
| --- | --- | --- |
| Query route | Route Recall@fetch_k | 查询表达仍找不到目标 |
| Lexical fusion | Candidate Recall@candidate_k | 目标被融合截断 |
| Ranker | HitRate@10 given candidate | 目标已召回但排序失败 |
| Final response | Session Hit@10 | Policy/Coverage/turn 级最终结果 |

该脚本是 analysis 边界工具，Agent 不得导入它，`evaluator/` 不得为它修改。普通 runtime trace 只足以分析融合后 Candidate Recall；Route Recall@fetch_k 必须来自上述显式 replay/diagnose 路径，不能根据融合后候选反推。

## 12. 文件级实施清单

### 12.1 新增

- `shopping_copilot/retrieval/query_variants.py`
  - `LexicalQueryVariant`
  - FTS quote/group helpers
  - deterministic variant builder
- `tests/test_multiquery_retrieval.py`
  - 查询族、融合、fallback、过滤和确定性测试
- `tests/test_candidate_recall.py`
  - 大量干扰商品下的 metamorphic recall 测试
- `configs/experiments/multiquery_lexical.json`
  - 只开启新 lexical fan-out 的实验配置
- `scripts/analyze_candidate_recall.py`
  - ground-truth 只在评测后使用的候选分层分析

### 12.2 修改

- `shopping_copilot/retrieval/bm25.py`
  - 单表达式执行改为 query fan-out
  - 共享过滤和 lexical RRF
  - `probe()` 保留 broad-only 语义
  - 提供 analysis-only `diagnose(plan)`，与正式检索共用每路执行函数
- `shopping_copilot/retrieval/hybrid.py`
  - 仅在需要时保留 granular `source_routes` 和稳定 tie-break
  - 不新增过滤语义
- `shopping_copilot/core/config.py`
  - 解析并校验 lexical fan-out 实验参数
- `shopping_copilot/core/factory.py`
  - 如采用显式配置注入，只扩展 BM25 构造参数；组件关系不变
- `shopping_copilot/core/pipeline.py`
  - 只增加 ground-truth-free candidate route trace
- `docs/team/architecture.md`
  - 实施稳定后补充 lexical fan-out 内部结构

### 12.3 明确不修改

- `shopping_copilot/state/`
- `shopping_copilot/catalog/constraints.py` 的语义规则
- `shopping_copilot/ranking/heuristic.py` 的公式和系数
- `shopping_copilot/policy/information_gain.py`
- `shopping_copilot/policy/coverage.py` 的阈值和算法
- `shopping_copilot/core/interfaces.py`
- `shopping_copilot/core/contracts.py`
- `starter/agent.py`
- `evaluator/`
- `data/catalog.jsonl`
- `data/public_set.jsonl`
- `configs/final.json`，直到独立 promotion 决策

## 13. 分阶段实施

### 阶段 R0：冻结基线与观测

1. 记录 branch、HEAD、status；
2. 保存全量单测结果；
3. 保存官方 evaluation JSON；
4. 保存每场景指标；
5. 记录当前 respond p50/p95 latency；
6. 用离线工具确认现有失败属于 route、fusion 还是 ranker。

完成标准：能够把每个 miss 明确归入“未召回、融合丢失、排序丢失、最终策略”之一。

### 阶段 R1：纯 Query Variant Builder

只新增纯函数和单元测试，不接入 BM25。

完成标准：

- variant 顺序和数量确定；
- slot 内 OR、slot 间 AND；
- phrase/relaxed/fielded 表达正确；
- negative/stale value 永不出现；
- Unicode、标点、空值不会产生非法 FTS；
- 不超过 5 路。

### 阶段 R2：BM25 fan-out 与 RRF

接入现有 BM25Retriever：

- broad-only probe；
- 每路独立 fetch；
- 共享 hard/negative filter；
- RRF merge；
- 输出不超过 candidate_k；
- 保留 deterministic tie-break。

完成标准：synthetic candidate recall 测试通过，现有 negative、semantic state 和 pipeline 测试全部通过。

### 阶段 R3：公开集与 metamorphic 验证

执行 official evaluation 和候选分层分析，重点看：

- Overall；
- Buying；
- Browsing；
- Intent Override；
- Boundary；
- Route Recall@fetch_k；
- Candidate Recall@60/@100；
- Hit@10 given candidate；
- p50/p95 latency。

如果回归，只修复通用查询或融合原因，并添加 metamorphic test。禁止增加 public ASIN、完整标题或专属属性值例外。

### 阶段 R4：现有 Structured 消融

只报告：

- lexical fan-out only；
- lexical fan-out + structured；
- route overlap；
- MRR/Hit/latency 差异。

无稳定增益则 structured 继续关闭，但功能代码保留。

### 阶段 R5：promotion

只有所有门槛满足，才单独提出 `configs/final.json` promotion。promotion diff 不再夹带算法修改，便于回滚和审查。

## 14. 测试设计

### 14.1 Query 构造测试

至少覆盖：

- 单 category Browsing 不产生重复 route；
- category + material 产生 slot AND 和 fielded route；
- `black or navy` 在同 slot 中为 OR；
- `merino wool` phrase route 不拆义；
- relaxed route 要求 `merino` 和 `wool` 同时存在；
- budget 只进入 `price_max`；
- negative `black` 不出现在任何 variant；
- override 后 stale `leather` 不出现在任何 variant；
- OOV `lyocell`、`ecru`、`goodyear welted` 无需词典即可生成合法查询；
- 最多 5 路且 expression 去重。

### 14.2 Retrieval 与融合测试

- RRF 与输入 route 执行顺序无关；
- 同一商品命中两路时高于只命中一路且排名相近的商品；
- tie 时按稳定键排序；
- `source_routes` 完整且无重复；
- 每路 raw BM25 数值尺度不同也不影响 RRF；
- strict route 为空时 broad fallback 正常；
- 某路出现合法空结果时不影响其他路；
- 返回候选不超过 `candidate_k`；
- `probe()` 与 fan-out 开关前 broadness 语义一致。

### 14.3 Candidate Recall metamorphic 测试

构造 synthetic catalog，不使用 public 商品值：

1. 一个 target 同时满足 category、material、feature；
2. 150 个 distractor 只匹配高频 category；
3. 80 个 distractor 只匹配 material；
4. 若干长 description 偶然包含 category token；
5. 若干完全满足但字段分布不同的合理替代商品。

必须满足：

- target 进入 fused Candidate@K；
- 增加只命中一个常见词的 distractor 不应挤出全 slot target；
- 给 target 和 query 同时增加一个正确 slot，不应降低 candidate recall；
- 打乱 Catalog 行顺序不改变候选集合和排序；
- strict query 空时 broad fallback 能恢复候选；
- phrase 标点变形仍可由 relaxed route 找到；
- 不依赖某个固定 ASIN 或公开完整句子。

### 14.4 Negative 与集成回归

必须继续通过：

- token 边界：`black` 不误杀 `blackberry`；
- 多词负向值；
- BM25/Structured/Dense-like candidate 的 Ranker final guard；
- negative 过滤后补位仍不返回违约商品；
- Information Gain；
- Coverage rotation；
- dynamic weighting；
- rarity weighting；
- Agent contract 和 evaluator smoke test。

## 15. 验证命令与验收门槛

### 15.1 命令

```bash
python -m unittest tests.test_multiquery_retrieval -v
python -m unittest tests.test_candidate_recall -v
python -m unittest tests.test_semantic_state -v
python -m unittest tests.test_negative_constraints -v
python -m unittest discover -s tests -p "test*.py" -v
git diff --check
```

有 Catalog 时：

```bash
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output /tmp/retrieval_before.json

python -m scripts.run_evaluation \
  --config configs/experiments/multiquery_lexical.json \
  --output /tmp/retrieval_after.json

python -m scripts.compare_runs \
  /tmp/retrieval_before.json \
  /tmp/retrieval_after.json
```

候选分析：

```bash
python -m scripts.analyze_candidate_recall \
  --results /tmp/retrieval_after.json \
  --trace /tmp/retrieval_after_trace.jsonl \
  --dataset data/public_set.jsonl
```

### 15.2 硬门槛

- 全量测试 100% 通过；
- 所有 metamorphic candidate recall 不变量通过；
- Candidate Recall@60 和 @100 不低于当前基线；
- Overall HitRate@10 不低于 0.995；
- Buying/Browsing/Intent Override/Boundary 不出现超过 1 个 session 的新增 miss；
- Overall MRR 不低于基线超过 0.005；若下降必须定位到通用原因，不允许直接调 Ranker 系数；
- 明确 negative constraint 违规数必须为 0；
- p95 respond latency 不超过基线的 1.5 倍；若超出，优先减少重复 expression 和无效 route，而不是牺牲硬约束；
- 无新运行时第三方依赖；
- `git diff --check` 通过；
- `evaluator/`、Catalog、public labels 无 diff。

这些是 promotion 门槛，不表示可以用 public set 训练。public 只用于回归检测，参数选择必须依靠原则、synthetic/metamorphic tests 和 route-level diagnostics。

## 16. 消融矩阵

按固定顺序执行，每次只改变一个维度：

| 实验 | Lexical fan-out | Structured | Dense | 用途 |
| --- | --- | --- | --- | --- |
| E0 | off | off | off | 当前 final 基线 |
| E1 | on | off | off | 主方案净增益 |
| E2 | on | on | off | Structured 增量 |
| E3 | on，去掉 fielded route | off | off | fielded route 贡献 |
| E4 | on，去掉 strict route | off | off | strict route 贡献 |
| E5 | on，只有 broad route | off | off | 验证实现等价于旧 BM25 |

每个实验同时报告：

- Candidate Recall@K；
- HitRate@10；
- MRR；
- MTTC；
- 四类 scenario；
- route overlap；
- p50/p95 latency。

rarity、dynamic weighting 和 Coverage 只沿用已有消融结论，不在本轮修改 final 参数或删除代码。

## 17. Dense/神经检索升级路径

只有满足以下任一证据才启动：

1. 多查询 lexical 后仍有一组 held-out miss，在所有 FTS route 的 fetch 深度内都找不到目标；
2. 失败主要来自同义、上位词、功能描述和商品文案之间的稳定词面鸿沟；
3. 已获得独立于 public 200 sessions 的 query-product relevance 数据；
4. 可以提供模型版本、离线 fallback、延迟、内存和许可说明。

### 17.1 推荐的二阶段结构

```text
lexical multi-query FTS5 --------+
                                 +--> top 300 union/RRF --> existing or learned reranker
small product-search bi-encoder -+
```

技术选择顺序：

1. 先用小型 bi-encoder 生成 query/product 单向量；
2. 50,000 商品先评估 exact dot-product/Flat index，不预设必须 ANN；
3. 只有 exact search latency 不达标才引入 HNSW/IVF；
4. Dense 只增加候选，不绕过 negative final guard；
5. 精排训练数据足够后，再比较 Cross-Encoder 与 ColBERT-style late interaction。

Faiss 支持 Flat、HNSW、IVF 等不同延迟/内存/召回权衡，但它属于后续新依赖：[Faiss](https://github.com/facebookresearch/faiss)。ColBERTv2 的 token-level late interaction 适合更强的语义相关性建模，但其索引和模型复杂度明显高于当前需求：[ColBERTv2](https://aclanthology.org/2022.naacl-main.272/)。

### 17.2 训练与评测数据要求

- public 200 sessions 只能做最终回归，不用于训练；
- 优先使用独立 query-product relevance 数据，标签区分 Exact/Substitute/Complement/Irrelevant；
- 训练、调参、测试按 query 和 product 双重隔离，防止同商品泄漏；
- 负样本应包含同 category、只匹配部分 slot 的 hard negatives；
- 模型输出仍只是候选分数，negative constraints 保持确定性硬过滤。

## 18. X 开源算法的使用边界

X 当前公开架构提供了有价值的设计模式：

- 多候选源 fan-out；
- retrieval 把百万候选缩到数百，再进入 ranking；
- source、filter、scorer、selector 的清晰分层；
- route 失败隔离和统一监控。

参考：[xai-org/x-algorithm](https://github.com/xai-org/x-algorithm) 和 [Phoenix README](https://github.com/xai-org/x-algorithm/blob/main/phoenix/README.md)。

但不直接使用其代码或模型，因为：

- 优化目标是 Feed engagement，不是 query-product relevance；
- 输入依赖用户行为序列、post/author embedding，而本系统输入是显式 canonical shopping state；
- 生产数据、持续训练、Kafka、MM embedding、Semantic-ID 等基础设施不能直接迁移；
- 公开 reference 数据是 synthetic stand-in，不证明商品搜索质量：[Phoenix reference](https://github.com/xai-org/x-algorithm/blob/main/phoenix/reference/README.md)；
- JAX/Rust/GPU 栈违反当前轻依赖和离线实现目标。

因此本方案只吸收它的“多候选源 + 两阶段”结构原则，不建立源码依赖。

## 19. 风险与控制

| 风险 | 表现 | 控制 |
| --- | --- | --- |
| variant 组合爆炸 | turn latency 上升 | 固定查询族、去重、最多 5 路 |
| 严格查询假阴性 | Catalog 字段缺词 | category anchor + broad fallback |
| RRF 改变 MRR | 目标已召回但 lexical 次序变化 | 分层 candidate/ranker 诊断，不先调 Ranker |
| Field mapping 过窄 | 值只出现在意外字段 | 同时保留非 fielded route |
| 常见词继续污染 | broad route 候选过多 | strict/slot/fielded route 提供独立排名证据 |
| FTS 语法问题 | OOV 标点导致异常 | 统一 normalized tokens + quote helper |
| negative 过滤后不足 | 推荐少于 top_k | 深取和其他正向 route 补位，永不放宽 negative |
| Public 过拟合 | 单样本提升、私有下降 | 不用 ASIN/标题；metamorphic distractor 和 OOV 测试 |
| Structured owner 功能被覆盖 | 无法独立消融 | 保留顶层 route 和配置开关 |
| 两级 RRF 难诊断 | lexical 与 hybrid 贡献混淆 | granular source route trace 和分层指标 |

## 20. 延期项及触发条件

| 延期项 | 启动证据 |
| --- | --- |
| coverage-preserving candidate reservoir | 目标常进入单路 fetch，但稳定被 RRF Top-K 截掉 |
| 自适应 candidate_k | 多路 query 后仍因候选预算发生跨场景 miss，且延迟有余量 |
| 重写 StructuredRetriever | Structured 消融显示有 recall 价值，但 tie/scoring 导致 MRR 回归 |
| Dense bi-encoder | lexical route 对稳定语义等价表达无法召回，且有独立训练数据 |
| Faiss/ANN | Dense exact search 的实测 p95 不达标 |
| Cross-Encoder/ColBERT | Candidate Recall 已高，主要瓶颈转为 top-10 精排，并有相关性标签 |
| Learn-to-rank | 有足够 held-out 数据支持训练和校准，不依赖 public 200 targets |
| Query synonym expansion | 有可审计的领域同义来源和变形测试，不靠公开词值手工扩表 |

如果扩展不能直接支持 Candidate Recall、确定性回退或硬否定不变量，应继续延期。

## 21. Definition of Done

- [ ] Query Variant Builder 只读取 canonical plan；
- [ ] 五类固定 query family 已实现并去重；
- [ ] slot 内 OR、slot 间 AND、phrase 和 field filter 语义有测试；
- [ ] broad OR fallback 永远保留；
- [ ] 每路深取、过滤、RRF、去重和 tie-break 确定性；
- [ ] 返回给 Ranker 的候选数不超过 `candidate_k`；
- [ ] probe/Information Gain 行为边界不变；
- [ ] negative matcher 和 Ranker final guard 未放宽；
- [ ] Information Gain、Coverage、dynamic weighting、rarity weighting 全部保留；
- [ ] Agent 接口、Pipeline 顺序和 factory 组件拓扑不变；
- [ ] synthetic/metamorphic tests 全部通过；
- [ ] 全量现有测试通过；
- [ ] Candidate Recall、官方总指标、四场景和 latency 完整报告；
- [ ] 任何回归均定位到 route/fusion/ranker/policy 具体层；
- [ ] 未硬编码 public ASIN、完整句子或特有属性值；
- [ ] 未修改 evaluator、Catalog、public labels；
- [ ] `git diff --check` 通过；
- [ ] 未经独立 promotion 不修改 `configs/final.json`；
- [ ] 未 commit 或 push，除非另行明确要求。

## 22. 最终推荐顺序

```text
R0 观测与候选分层
  -> R1 纯 Query Variant Builder
  -> R2 FTS5 fan-out + RRF
  -> R3 public + metamorphic + latency 验证
  -> R4 Structured 独立消融
  -> R5 final promotion
  -> 有充分证据后再考虑 Dense / learned reranking
```

该顺序先解决已经被证实的候选截断问题，同时把模型、训练数据和新依赖带来的风险留在有明确升级证据之后。
