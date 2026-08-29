# Category Anchor + Evidence-Gated Slate V2 优化开发计划

## 1. 文档状态

- 文档类型：算法优化开发计划，当前不包含业务代码修改。
- 基线仓库：`https://github.com/vepharix/techjam-conversational-search`
- 基线分支：`main`
- 基线提交：`87a64a6cc9232cade87514903cf96dd13f6466ac`
- 基线提交说明：`feat: merge clean dialog improvements (#11)`
- 基线同步时间：2026-08-29，Asia/Shanghai。
- 推荐研究分支：`research/category-anchor-gated-slate-v2`
- 推荐实验配置：`configs/experiments/category_anchor_gated_slate_v2.json`
- 目标：在保持模块化、零标签泄漏和可回滚的前提下，独立实现类别锚点、开放式提问、约束精排和证据门控，并通过逐阶段消融验证是否可以稳定超过当前 `main`。

## 2. 执行摘要

当前 `main` 和 `dev` 的默认配置在官方公开集上结果完全一致：

| 指标 | `main@87a64a6` 基线 |
| --- | ---: |
| Hit Rate@10 | 0.995000 |
| MRR | 0.778343 |
| MTTC | 3.335000 |
| Efficiency | 0.766500 |
| TechnicalScore | 0.884303 |

外部公开实现 `leongyiquan/submission.Agent` 在同一份官方评估器、公开集和 50,000 商品目录上可复现：

| 指标 | 外部参考结果 | 相对当前 `main` |
| --- | ---: | ---: |
| Hit Rate@10 | 1.000000 | +0.005000 |
| MRR | 0.946929 | +0.168586 |
| MTTC | 2.095000 | -1.240000 |
| TechnicalScore | 0.962179 | +0.077876 |

本项目已有 `exp/smart-slate` 尝试同类思路，但累计结果只有 `0.887809`，且 Hit 降至 `0.990000`。其现有消融结果为：

| 累计版本 | Hit | MRR | MTTC | TechnicalScore |
| --- | ---: | ---: | ---: | ---: |
| `main` 基线 | 0.995000 | 0.778343 | 3.335000 | 0.884303 |
| + 类别锚点 | 0.995000 | 0.568058 | 2.590000 | 0.836117 |
| + `other` 优先 | 1.000000 | 0.583121 | 2.340000 | 0.848136 |
| + 具体性/完整匹配奖励 | 1.000000 | 0.584123 | 2.305000 | 0.849137 |
| + Top1/Top10 门控 | 0.990000 | 0.777363 | 3.020000 | 0.887809 |

因此 V2 不应复制或直接合并 `exp/smart-slate`。正确路线是：

1. 从同步后的 `main@87a64a6` 建立干净研究分支；
2. 先完成不改变线上决策的 Shadow Evaluator V2；
3. 重做不会在精排前丢失目标的 Category Anchor V2；
4. 单独验证 repeated-`other`；
5. 单独验证具体性和完整匹配奖励；
6. 只有 Shadow 数据证明目标排名会稳定改善时，才启用 Top1/Top10 门控；
7. 所有实验都必须有独立配置、flag-off 等价性和明确的停止条件。

## 3. 基线同步与分支纪律

### 3.1 已完成的同步状态

当前仓库已确认：

```text
local main    = 87a64a6cc9232cade87514903cf96dd13f6466ac
upstream/main = 87a64a6cc9232cade87514903cf96dd13f6466ac
origin/main   = 87a64a6cc9232cade87514903cf96dd13f6466ac
```

`main...upstream/main` 和 `origin/main...upstream/main` 的左右提交差异均为 `0 0`。

### 3.2 创建研究分支前的标准操作

当前工作区存在未提交实验，因此不得直接 checkout、merge 或 reset。负责人应先提交或安全保存当前工作，再执行：

```bash
git fetch upstream main
git fetch origin main
git switch main
git pull --ff-only upstream main
git switch -c research/category-anchor-gated-slate-v2
```

创建后必须记录：

```bash
git rev-parse HEAD
git status --short --branch
git diff --check
```

分支起点必须是 `87a64a6`，除非 upstream `main` 在正式开始开发前发生更新。若发生更新，应重新跑基线并更新本文档中的基线提交和指标，禁止在未重新评估的提交上沿用旧基线数字。

### 3.3 禁止事项

- 不从当前 `dev` 开分支。`dev` 包含大量关闭的 Strict、Global IDF、semantic slate 和 protected rescue 实验路径。
- 不从 `exp/smart-slate` 继续堆叠参数。
- 不把多个算法步骤压入同一提交或同一实验配置。
- 不修改官方 evaluator、公开集或基线标签。
- 不在 Agent、Retriever、Ranker、Policy 或 runtime trace 中读取 `ground_truth`、`sample_id` 对应标签或场景标签。
- 不复制无明确许可证外部仓库的源代码；只独立实现公开算法思想。

## 4. 当前系统诊断

### 4.1 当前默认流水线

`main@87a64a6` 的稳定顺序为：

```text
Official Agent adapter
  -> Rule state tracker
  -> Query planner
  -> Candidate probe
  -> Pre-search policy
  -> BM25 / optional routes
  -> Heuristic reranker
  -> Post-ranking policy
  -> Coverage ordering
  -> Response builder
```

必须保持这条编排顺序。新能力应通过新组件或配置注入，不应把外部 Agent 的单文件逻辑搬入 `core/pipeline.py`。

### 4.2 当前优势

- 官方入口已经是薄适配器，核心模块职责清晰。
- 语义状态能够处理 pending attribute、no-preference、negative constraint 和 intent override。
- BM25、Ranker、Policy、Coverage 和 Response Builder 已有稳定接口。
- 默认配置已有动态约束权重、候选内稀有度加权和信息增益提问。
- 当前 Hit 已达 `0.995`，说明优化重点是保护 Hit 的同时提升 MRR 和 MTTC，而不是无约束扩张候选。

### 4.3 当前主要缺口

#### 缺口 A：类别没有成为召回保底集合

当前类别进入 active slots、query terms 和约束分数，但默认没有独立、强保证的类别候选路线。目标可能在 BM25 `candidate_k=100` 之外，之后再好的 Ranker 也无法救回。

#### 缺口 B：提问策略没有最大化官方模拟器的披露带宽

默认策略按 Buying/Browsing 路由询问 feature、material、use_case 等具体槽位；只有连续多个 no-preference 后才回退到 `other`。官方模拟器在 `ask_attribute="other"` 时可能一次披露多个尚未披露条件，因此具体槽位问题可能浪费轮次。

#### 缺口 C：排序只表达覆盖比例，没有强表达完整满足

当前 Ranker 采用候选池内 rarity 和动态 constraint weight，但：

- 长而具体的 feature 与单词级 material 的区分仍不充分；
- 没有默认启用明确的 all-constraints-matched 奖励；
- budget 与普通文本匹配的语义边界还需要单独固定；
- Gate 所需的“匹配了多少个有效非类别约束”没有成为稳定 contract。

#### 缺口 D：展示门控依赖错误的稳定性假设

现有 `smart-slate` 中，`public_0161` 和 `public_0172` 在无门控版本第 2 轮均为 rank 7；门控将其隐藏，之后目标退出 Top10，最终形成两个 miss。

这说明 Gate 失败的根因不是简单的 turn 或 match-count 阈值，而是候选和排序尚未稳定。必须先测量“门控前排名”，再决定是否截断展示。

## 5. 总体目标与非目标

### 5.1 总体目标

最终候选版本必须同时满足：

1. Hit Rate@10 不低于 `0.995000`，目标值为 `1.000000`；
2. MRR 不低于 `0.778343`，目标值至少 `0.900000`；
3. MTTC 不高于 `3.335000`，目标值不高于 `2.500000`；
4. TechnicalScore 必须严格高于 `0.884303`；
5. 四个官方场景均不得产生新的 miss；
6. Runtime model token 保持为 0；
7. turn p95 延迟不得超过 clean baseline 的 1.25 倍；
8. flag-off 模式必须与 `main` 基线逐 session、逐 turn 等价；
9. 不添加 public target ASIN、sample ID 或固定标签特例；
10. 所有新行为必须能通过配置关闭并独立消融。

### 5.2 非目标

本轮不优先实施：

- Dense embeddings 或外部模型调用；
- React 演示站；
- 全面替换 Query Planner、State Tracker 或 Response Builder；
- 不受保护地开启 Structured Retriever；
- 全局 IDF 优先排序；
- 无条件 RRF 扩池；
- 针对公开集逐 sample 调参；
- 直接移植外部单文件 Agent。

## 6. 目标架构

### 6.1 模块结构

建议新增或扩展：

```text
shopping_copilot/
  evaluation/
    __init__.py
    shadow.py                  # 非提前终止评估和离线标签连接
  retrieval/
    category_anchor.py         # 独立类别索引和类别候选路线
  ranking/
    heuristic.py               # 具体性、完整匹配、匹配数量诊断
  policy/
    heuristic.py               # repeated-other 和 evidence gate
  observability/
    trace.py                   # ground-truth-free 门控前/后诊断
scripts/
  run_shadow_evaluation.py
  compare_shadow_runs.py
configs/experiments/
  category_anchor_v2.json
  other_first_v2.json
  complete_match_ranking_v2.json
  evidence_gated_slate_v2.json
  category_anchor_gated_slate_v2.json
tests/
  test_shadow_evaluator.py
  test_category_anchor.py
  test_evidence_gated_slate.py
```

### 6.2 数据流

```text
SessionState
  -> SearchPlan
  -> BM25 candidates
  -> exact category anchor set
  -> anchor-aware candidate construction
  -> full pre-gate ranking
       -> match count
       -> complete-match flag
       -> category membership
       -> budget evidence
  -> question decision
  -> evidence + stability gate
  -> visible Top1 or Top10
```

### 6.3 三层排名必须分离

Shadow 和 trace 必须明确区分：

1. `retrieval_rank`：目标在候选召回集合中的位置；
2. `pre_gate_rank`：目标在完整 Ranker 输出中的位置；
3. `visible_rank`：目标在最终返回 slate 中的位置。

如果只记录 `visible_rank`，无法判断 miss 是召回失败、排序失败还是门控隐藏造成的。

## 7. 实验治理与公共规则

### 7.1 一次只改变一个变量

推荐提交顺序：

```text
R0  baseline fingerprint
R1  shadow evaluator only
R2  category anchor diagnostics only
R3  category anchor live retrieval
R4  repeated-other policy
R5  specificity + complete-match ranking
R6  evidence-gated slate
R7  paraphrase and failure recovery hardening
R8  final integrated candidate
```

每个阶段必须生成独立 config、结果文件和实验记录。不得把 R3～R6 合为一次评估，因为组合得分无法说明单项机制是否有效。

### 7.2 Tune/validation/locked-holdout 使用规则

建议固定、可复现地将 200 个公开 session 分为三个互斥集合：

| 分区 | 数量 | 用途 | 使用限制 |
| --- | ---: | --- | --- |
| Tune | 120 | 淘汰明显失败方案、选择有限参数网格 | 可以查看 changed-session 和 Shadow 细节 |
| Validation | 40 | 比较预注册候选组合、选择最终方案 | 不允许在查看结果后继续扩张参数网格 |
| Locked holdout | 40 | 最终晋升判定 | 参数与代码冻结后只运行一次 |

按官方场景分层分配：

| 场景 | 总数 | Tune | Validation | Locked holdout |
| --- | ---: | ---: | ---: | ---: |
| Buying | 80 | 48 | 16 | 16 |
| Browsing | 80 | 48 | 16 | 16 |
| Intent Override | 30 | 18 | 6 | 6 |
| Boundary | 10 | 6 | 2 | 2 |
| 合计 | 200 | 120 | 40 | 40 |

划分还必须按基线难度分层，避免困难样本集中到一个集合。难度只能使用冻结基线的离线结果定义，例如：

- 基线 turn 1～2 命中；
- 基线 turn 3～5 命中；
- 基线 turn 6～10 命中；
- baseline miss；
- 首次命中 rank 1；
- 首次命中 rank 2～10；
- 初始候选 Top10 之外。

分割 manifest 必须基于固定 seed 和稳定 hash 生成并提交，禁止人工移动 session。由于 Boundary 总共只有 10 条，参数选择不得根据 Boundary 均值调权；全部 10 条还必须作为跨分区强制回归集，任何 Boundary 新 miss、循环询问或非法响应都阻止晋升。

最终报告仍需给出 full 200 结果和 scenario 分组结果，但 full 200 不能被包装成真正未见测试集。若看完 full 200 后继续调参，后续 full 200 结果只能标记为 development result，不能再称为最终 holdout 结果。必须使用 paraphrase、metamorphic 和 synthetic catalog tests 补充公开集较小带来的泛化风险。

### 7.3 结果记录

每个实验在 `experiments/registry.csv` 增加一行，并单独保存：

- Git commit；
- base commit；
- config path 与 config SHA256；
- catalog SHA256；
- public-set SHA256；
- Hit、MRR、MTTC、Efficiency、TechnicalScore；
- 各 scenario 指标；
- changed-session 列表；
- turn p50、p95；
- candidate-stage recall；
- flag-off 等价性；
- 结论：promote、keep-disabled 或 reject。

### 7.4 单点实验矩阵

Shadow Evaluator 属于观测工具，不计入算法开关。四个行为优化定义为：

- `C`：Category Anchor V2；
- `O`：Repeated-`other`；
- `R`：Specificity + Complete Match Ranking；
- `G`：Evidence + Stability Gate；
- `B`：冻结的 `main@87a64a6` 基线。

首先运行单点实验：

| 实验 | 主要问题 | 主指标 | 不能只看什么 |
| --- | --- | --- | --- |
| `B` | 基线是否稳定可复现 | 全部官方指标、延迟、确定性 | 单次总分 |
| `B+C` | 类别是否成为可靠召回保底 | anchor target presence、candidate recall、MTTC | 最终 MRR |
| `B+O` | `other` 是否提高披露带宽 | 每轮新增约束数、问题数、MTTC | 问题模板命中次数 |
| `B+R` | 已召回目标是否被更好排序 | pre-gate MRR、all-match 排名、hard-negative 结果 | visible score |
| `B+G` | 当前排序条件下 Gate 是否安全 | Gate RR gains/losses、新 miss、defer EV | 综合分均值 |

`B+G` 即使预期失败也应保留为负对照。它用于证明 Gate 不是普遍有效，而是依赖 Category Anchor 和 Ranking 提供的候选稳定性。

不要求所有单点实验都立刻提升最终 TechnicalScore。Category Anchor 可能因为更早暴露低 rank 目标而暂时降低 MRR，但只要它显著提升候选召回，并且后续 `R` 和 `G` 能以预注册方式修复排序，就仍可能是必要组件。单点判断必须采用组件自己的阶段指标。

### 7.5 累计实验矩阵

按算法依赖顺序运行：

| 累计实验 | 目的 | 相对增量计算 |
| --- | --- | --- |
| `B+C` | 建立类别召回基础 | `Score(B+C) - Score(B)` |
| `B+C+O` | 加速有效约束披露 | `Score(B+C+O) - Score(B+C)` |
| `B+C+O+R` | 利用约束稳定 pre-gate 排名 | `Score(B+C+O+R) - Score(B+C+O)` |
| `B+C+O+R+G` | 在稳定排序上优化首次展示 | `Score(FULL) - Score(B+C+O+R)` |

每一步必须同时输出总体指标、scenario 指标、candidate-stage 指标、changed-session 和 latency。禁止只比较 `FULL` 与 `B`，否则无法知道哪一步贡献收益、哪一步只是被后续步骤补偿。

### 7.6 关键交互实验

组件之间存在明显交互，因此除单点和累计实验外，至少测试：

| 实验 | 要验证的交互 |
| --- | --- |
| `B+C+R` | Ranking 是否能修复 Category Anchor 的低排名问题 |
| `B+C+G` | 只有类别召回、没有完整匹配奖励时 Gate 是否安全 |
| `B+O+R` | 没有 Category Anchor 时，快速披露能否改善现有 BM25 排名 |
| `B+R+G` | 现有召回加更好排序后 Gate 是否已有价值 |
| `B+C+R+G` | `other` 在最终闭环中是否必要 |
| `B+C+O+G` | 没有新 Ranking 时，更多约束能否直接支持 Gate |
| `B+C+O+R+G` | 完整方案和最终交互效果 |

四个二值开关共有 `2^4 = 16` 种组合，计算上可行。推荐在 Tune 上运行完整 16 组合矩阵，用于识别主效应和交互方向；但不得在 Validation 和 Locked holdout 上反复遍历全部组合。进入 Validation 前必须根据 Tune 结果和预先定义的安全门槛，将候选缩小为少数组合；进入 Locked holdout 前只能保留一个最终方案和一个冻结基线。

对于任何“单独降分、组合增益”的组件，报告必须明确：

- 单点损失来自候选、排序还是展示层；
- 与哪个组件发生正交互；
- 该交互是否在 Validation 重现；
- 去掉该组件后完整系统是否显著退化；
- 该组件是否增加隐藏集或性能风险。

### 7.7 Leave-one-out 最终必要性验证

完整方案冻结后，每次移除一个组件：

| 实验 | 目的 |
| --- | --- |
| `FULL-C` | 测量 Category Anchor 在完整闭环中的最终贡献 |
| `FULL-O` | 测量 repeated-`other` 是否仍有独立价值 |
| `FULL-R` | 测量完整匹配排序是否不可替代 |
| `FULL-G` | 测量 Gate 的最终净贡献和风险 |

Leave-one-out 回答的是“组件在最终系统中是否仍然必要”，不能由早期单点实验替代。某个组件可能单独有效，但在完整系统中已被其他组件取代；也可能单独无效，却是最终正交互不可缺少的一部分。

建议决策规则：

- 移除后 Hit、MRR、TechnicalScore 或关键 scenario 明显变差：组件保留；
- 移除后指标不变但 latency/复杂度下降：组件删除；
- 移除后总体不变但某 scenario 获得明确保护：作为保险组件保留时必须单独说明；
- 移除后只在 Tune 变差、Validation 不变：按过拟合风险删除或保持关闭。

### 7.8 配对统计和不确定性报告

所有版本必须在同一 session 集上做配对比较，而不是比较两个独立均值。至少报告：

- Hit 的新增命中数、新增 miss 数和配对四格表；
- 必要时对 Hit 使用 McNemar 检验，但不能用 p 值替代新 miss 硬门槛；
- MRR、MTTC、TechnicalScore 的 paired bootstrap 置信区间；
- 变好、变差、不变的 session 数量；
- 每个 scenario 的 changed-session；
- RR gain 和 RR loss 的总量及最大单 session 损失；
- Gate 导致的 earlier hit、later hit、new hit、new miss；
- 候选召回、pre-gate 排名和 visible 排名分别变化多少。

公开集只有 200 条，Boundary 只有 10 条，因此置信区间应作为不确定性说明，而不是唯一晋升标准。Hit、scenario 新 miss、标签边界、flag-off 等价性和延迟仍使用硬门槛。

### 7.9 推荐执行流程

```text
R0 冻结基线与分割 manifest
  -> R1 Shadow Evaluator V2
  -> Tune 上运行 B+C / B+O / B+R / B+G 单点实验
  -> Tune 上运行完整 16 组合或预注册关键交互
  -> 按依赖顺序运行累计实验 B+C+O+R+G
  -> 选择少量候选进入 Validation
  -> 冻结 FULL 的代码、配置和参数
  -> 对 FULL 做 leave-one-out
  -> 只保留一个最终方案
  -> Locked holdout 只运行一次
  -> Full 200 development report
  -> Shadow + Paraphrase + Metamorphic + Latency 最终审计
  -> promote / keep-disabled / reject
```

如果 Locked holdout 失败，不得查看具体 session 后继续修补并再次称其为 holdout。可以回到新一轮开发，但必须更新实验轮次、记录 holdout 已被使用，并将后续结果标记为新的 development cycle。

## 8. R0：基线冻结与指纹

### 8.1 目的

确保所有后续增量都与同一个 `main@87a64a6` 比较，排除 evaluator、catalog、config 和工作区污染。

### 8.2 操作

1. 从同步后的 `main` 创建研究分支；
2. 验证官方文件和 catalog hash；
3. 运行完整单元测试；
4. 运行两次 clean evaluation，确认结果字节级稳定；
5. 保存 baseline 结果为只读 artifact；
6. 记录每轮 latency 和 session 总时长；
7. 确认唯一 baseline miss 为 `public_0020`，但不得为其添加 runtime 特例。

### 8.3 预期命令

```bash
python -m unittest discover -s tests -p "test*.py" -v
python -m scripts.verify_data
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output output/category_anchor_gated_slate_v2/r0_baseline_a.json
python -m scripts.run_evaluation \
  --config configs/final.json \
  --output output/category_anchor_gated_slate_v2/r0_baseline_b.json
python -m scripts.compare_runs \
  output/category_anchor_gated_slate_v2/r0_baseline_a.json \
  output/category_anchor_gated_slate_v2/r0_baseline_b.json
```

### 8.4 R0 验收

- 指标必须精确复现 `0.995 / 0.778343 / 3.335 / 0.884303`；
- 两次 clean session 结果必须一致；
- 测试全部通过；
- config 仍为默认 `configs/final.json`；
- 工作区不包含未经记录的代码差异。

若不满足，停止所有算法开发，先解决环境或基线漂移。

## 9. R1：Shadow Evaluator V2

### 9.1 目的

官方 evaluator 在第一次命中后停止，无法评估“隐藏早期 rank 2～10 后，目标是否会升到 rank 1”。Shadow Evaluator V2 必须跑满 10 轮，并同时记录候选、精排和展示三个层级。

### 9.2 关键设计

Shadow Runner 可以在离线评估层读取 `ground_truth`，但 Agent 和 runtime 组件不得读取。运行时 trace 只记录：

- `ranked_candidate_ids`；
- `visible_candidate_ids`；
- `source_routes`；
- `active_slot_signature`；
- `matched_constraint_count`；
- `matched_product_constraint_count`；
- `all_constraints_match`；
- `leader_score` 与 component scores；
- `top10_jaccard_with_previous_turn`；
- `question_attribute`；
- `recommendation_count`；
- `gate_reason`；
- latency。

评估完成后，由 `shadow.py` 在离线阶段把 target ID 与上述 ground-truth-free trace 连接，计算目标排名。

### 9.3 每 session 输出建议

```json
{
  "sample_id": "public_xxxx",
  "scenario_type": "buying",
  "baseline_first_hit_turn": 2,
  "baseline_first_hit_rank": 7,
  "turns": [
    {
      "turn": 1,
      "retrieval_rank": 43,
      "pre_gate_rank": 8,
      "visible_rank": null,
      "visible_depth": 1,
      "leader_product_match_count": 0,
      "top10_jaccard": null
    }
  ]
}
```

### 9.4 聚合输出

- 每轮 target candidate recall@100；
- 每轮 pre-gate Hit@10、Top3、Top1；
- 每轮 visible Hit@10、Top3、Top1；
- turn-1 rank 2～10 cohort 在 turn 2～5 的保留率；
- turn-2 rank 2～10 cohort 在 turn 3～5 的保留率；
- 每轮平均 reciprocal rank；
- 按 Buying、Browsing、Intent Override、Boundary 分组；
- 模拟 defer-to-turn-N 的精确 score delta；
- 因 Gate 变成 miss 的 session 列表；
- 因 Gate 从低 rank 升为 rank 1 的 session 列表。

### 9.5 测试

- evaluator 命中后仍继续到第 10 轮；
- override 前的排名不计为 eligible hit；
- Boundary 一次性回复逻辑与官方 evaluator 一致；
- runtime trace 不包含 target、label 或 ground truth；
- pre-gate 和 visible rank 可以不同；
- Gate 关闭时 visible list 与官方输出一致；
- 固定输入两次运行结果一致。

### 9.6 R1 验收

- 默认配置的 Agent 输出必须与基线逐 session 一致；
- Shadow 只增加观测，不改变询问和推荐；
- `public_0161`、`public_0172` 必须能显示“无门控 turn 2 rank 7、门控后 miss”的因果链；
- 生成一份 baseline rank-vs-turn 报告。

## 10. R2：Category Anchor V2 诊断模式

### 10.1 目的

建立与官方 coarse-category 规则一致的类别索引，但暂时不改变 live candidates。先证明目标是否被正确锚定，以及类别集合大小和可排序性。

### 10.2 独立实现原则

不得从外部仓库复制 helper。应根据官方 evaluator/spec 独立实现，并在 tests 中用固定 fixture 比较：

- 忽略顶层通用类别；
- 规范化逗号分隔和空白；
- 使用有效 category path 的末端部分；
- 对 opening lead-in 使用 longest normalized suffix match；
- 找不到精确 anchor 时不硬过滤，回退 BM25。

生产代码不应 import evaluator。官方一致性只在 tests 或 evaluation 工具中校验。

### 10.3 索引结构

建议构建：

```text
anchor_key -> ordered tuple[parent_asin]
parent_asin -> anchor_key
parent_asin -> normalized searchable text
constraint phrase -> optional cached postings
```

索引必须在 Agent 初始化时一次构建，后续 turn 不得重复遍历 50,000 商品。

### 10.4 诊断字段

- `category_anchor_detected`；
- `category_anchor_key`；
- `category_anchor_size`；
- `target_in_anchor`，仅在离线 post-join 后产生；
- `anchor_parse_method`；
- `anchor_fallback_reason`；
- `anchor_build_ms`；
- `anchor_lookup_ms`。

### 10.5 R2 硬门槛

- 公开集 opening 的 target-in-anchor 必须为 100%；
- paraphrase L1 的 category-anchor 命中率必须为 100%；
- synthetic greeting、缺类别和错误类别不得错误硬过滤；
- diagnostic-only 模式下官方结果必须与基线完全相同；
- 每 turn lookup 不应遍历全目录。

未达到 100% target-in-anchor 时，不得进入 live retrieval。

## 11. R3：Category Anchor V2 Live Retrieval

### 11.1 设计目标

类别集合应成为召回保底路线，但不能像现有 `smart-slate` 一样在完整精排前通过 RRF `candidate_k=100` 截断目标。

### 11.2 候选构建原则

1. BM25 仍是主路线和 fallback；
2. 精确 anchor set 不作为不可逆硬过滤；
3. 一旦存在非类别约束，应在完整 anchor set 上计算低成本约束证据；
4. 先完成 anchor 内约束排序，再选 anchor lane 候选；
5. BM25 和 anchor lane 在最终 Ranker 前合并；
6. 同一商品只保留一次，并记录 source routes；
7. 不得只按 rating/popularity 截断 anchor set；
8. 目标是否进入 final candidate pool 必须由 Shadow 验证。

### 11.3 性能设计

现有 `smart-slate` 会在 `probe()` 和 `retrieve()` 中重复扫描 category candidates，并逐商品调用约束匹配。V2 必须：

- 缓存 anchor lookup；
- 按 `anchor_key + normalized constraint signature + negative signature + budget` 缓存排名；
- 预计算 normalized fields；
- 避免 `probe()` 重复执行完整 `_ranked()`；
- 使用集合/postings 交集缩小 exact-match 候选；
- 对 fallback broad route 保持原 BM25 顺序；
- 在配置关闭时不增加默认热路径成本。

### 11.4 候选阶段验收

至少输出：

| 阶段 | 指标 |
| --- | --- |
| Anchor set | target presence |
| BM25 Top100 | target presence |
| Anchor lane TopN | target presence |
| Union candidates | target presence |
| Pre-gate Top10 | target presence/rank |
| Final visible slate | target presence/rank |

### 11.5 R3 晋升门槛

- Hit Rate@10 不低于 0.995；
- candidate recall 不得出现 baseline 新回归；
- `public_0020` 应成为诊断观察对象，但不得作为特例；
- MRR 可在本阶段暂时下降，但若下降超过 0.02，必须确认是“更早低 rank 命中”而不是错误排序；
- turn p95 不超过 baseline 1.25 倍；
- 若 target 在 anchor set 中却在 anchor lane 截断前丢失，立即 reject 当前候选策略。

## 12. R4：Repeated-`other` 提问策略

### 12.1 目标

利用官方模拟器中 `other` 可以披露多个未披露条件的行为，以更少轮次获得更强约束，同时保留对 Boundary 和自然用户表达的保护。

### 12.2 状态设计

在 `SessionState` 或 policy-owned state 中明确记录：

- `other_question_count`；
- `consecutive_no_additional_other`；
- `contentful_other_reply_count`；
- `requirements_drained`；
- `last_question_attribute`。

建议状态转移：

```text
contentful other reply
  -> add constraints
  -> reset consecutive_no_additional_other to 0
  -> continue asking other while under max count

no additional requirement
  -> increment consecutive_no_additional_other
  -> one reply: may ask other once more
  -> two consecutive replies: mark drained and stop

dissatisfaction
  -> clear drained
  -> resume clarification or strategy switch

intent override
  -> reset other-drain state for new intent
```

### 12.3 配置

建议配置字段：

```json
{
  "other_first_enabled": true,
  "other_first_max_questions": 3,
  "other_drain_confirmations": 2,
  "other_resume_on_dissatisfaction": true
}
```

具体值必须通过有限消融选择，不允许逐 session 调整。

### 12.4 测试矩阵

- `other` 一次返回两个约束；
- contentful 回复后继续询问；
- 一次 no-additional 后仍允许再次确认；
- 两次 no-additional 后停止；
- `I don't have a preference for other; use your judgment` 的 Boundary 处理；
- dissatisfaction 后恢复提问；
- override 后不继承旧 intent 的 drained 状态；
- turn 10 不再询问；
- `ask_attribute` 始终在官方枚举内。

### 12.5 R4 晋升门槛

- Hit 不低于 R3；
- MTTC 必须改善或持平；
- MRR 不得下降超过 0.005；
- Boundary 不得出现新 miss；
- 平均问题数、`other` 问题数和每轮新增约束数必须进入报告；
- paraphrase stress 下 disclosure count 不得明显下降。

## 13. R5：具体性加权与完整匹配奖励

### 13.1 目标

使长而具体的商品 feature 比高频单词级材料、颜色等约束拥有更高区分力，并奖励同时满足全部有效约束的候选。

### 13.2 匹配语义

- 对 product 和 constraint 使用同一 normalizer；
- 多词约束优先完整短语匹配；
- 属性级 matcher 应使用正确字段，避免 description 偶然出现 category token；
- category 参与类别锚点和总体一致性，但不计入 Gate 的 product-bearing match count；
- budget 使用数值比较，不做普通 substring match；
- negative constraint 仍由现有 shared guard 处理；
- missing metadata 表示 unknown，不自动等价于 conflict。

### 13.3 权重设计

具体性权重必须满足：

- 随 token count 或规范化短语长度单调增加；
- 有上限，避免单条异常长 description 垄断排序；
- category 不因文本长度获得额外 specificity；
- rarity 与 specificity 分开记录，便于消融；
- 完整匹配奖励必须是独立配置项；
- 所有 component score 写入 `RankedCandidate.component_scores`。

建议诊断字段：

- `matched_constraint_count`；
- `matched_product_constraint_count`；
- `active_constraint_count`；
- `all_constraints_match`；
- `specificity_score`；
- `rarity_score`；
- `budget_score`；
- `category_anchor_bonus`；
- `unmatched_active_count`。

### 13.4 消融顺序

1. shared normal form only；
2. phrase match only；
3. specificity weighting；
4. all-constraints bonus；
5. numeric budget scoring；
6. combined ranking。

不得先同时启用全部排序项再调系数。

### 13.5 R5 晋升门槛

- Hit 不低于 R4；
- MRR 必须严格高于 R4，目标增量至少 0.01；
- MTTC 不得退化超过 0.05；
- complete-match 商品必须在 synthetic hard-negative tests 中稳定领先部分匹配商品；
- 长 feature 不得因为任意单词重叠就获得完整匹配；
- Global IDF 不得优先于 final rerank，除非有新的独立证据推翻现有失败实验。

## 14. R6：Evidence-Gated Slate

### 14.1 前置条件

Gate 只有在 R1～R5 证明以下条件后才允许编码为 live behavior：

- 目标在 anchor/union candidates 中稳定存在；
- 无门控版本的目标排名在 turn 2～3 明显向 Top1 收敛；
- early rank 2～10 cohort 到 turn 3 的 Top10 保留率为 100%；
- validate split 上门控期望收益为正；
- 没有 `public_0161`、`public_0172` 类型的“隐藏后永久消失”案例。

若这些前置条件不满足，Gate 阶段必须保持关闭。

### 14.2 Gate 不使用 score gap 作为主判断

主要证据应为：

- leader 匹配的有效非类别约束数量；
- leader 是否完整匹配全部有效约束；
- candidate Top10 跨轮 Jaccard；
- leader 是否跨轮稳定；
- 当前 turn；
- requirements 是否 drained；
- 是否发生 dissatisfaction 或 override。

score gap 只能作为诊断，不作为唯一置信度来源。

### 14.3 建议状态机

```text
if no question is being asked:
    return normal Top10
elif dissatisfaction or requirements_drained:
    escape to Top10
elif turn >= forced_expand_turn:
    escape to Top10
elif top10 is unstable across turns:
    escape to Top10
elif turn >= min_expand_turn and leader_product_match_count >= min_matches:
    return Top10
else:
    return Top1
```

初始候选建议只在有限网格中测试：

- `compact_count`: 1；
- `min_expand_turn`: 2 或 3；
- `min_matches`: 1、2；
- `forced_expand_turn`: 4、5；
- `stability_escape`: on/off；
- `top10_jaccard_threshold`: 少量预注册候选值。

### 14.4 稳定性逃生阀

这是 V2 相对现有 `smart-slate` 的关键新增保护：

- turn 1 没有历史，可以保持 compact；
- turn 2 起比较完整 pre-gate Top10 与上一轮；
- 若头部集合或 leader 明显不稳定，则全量展示，避免把 rank 2～10 的短暂命中永久隐藏；
- instability 只决定是否解除门控，不改变 Ranker 顺序。

### 14.5 Gate 报告必须包含

- 被 compact 的 session/turn 数；
- compact 时 target 原始 pre-gate rank 分布；
- Gate 带来的 RR gains/losses；
- new hits 和 new misses；
- earlier/later hits；
- forced expansion 次数；
- stability escape 次数；
- drained/dissatisfaction/override escape 次数；
- 每 scenario score delta；
- `public_0161`、`public_0172` 的逐轮完整 trace。

### 14.6 R6 硬门槛

- Hit >= 0.995，目标为 1.000；
- MRR 严格高于 R5；
- TechnicalScore 严格高于 R5 和 clean baseline；
- 任何 scenario 新 miss 数为 0；
- early rank 2～10 cohort 不得因 Gate 产生不可恢复 miss；
- forced expansion 最晚第 5 轮；
- flag-off 与 R5 完全等价；
- Gate 只截断展示，不修改候选和 Ranker 顺序。

## 15. R7：鲁棒性和失败恢复

### 15.1 Paraphrase Stress Harness

新增确定性改写层，至少覆盖：

- opening lead-in 改写；
- category 前后词序变化；
- constraint payload 大小写变化；
- 分号、逗号、and 等连接符替换；
- budget 表达改写；
- override 触发句改写；
- no-preference 与 no-additional 改写；
- dissatisfaction 改写。

L1 应保持 constraint payload 内容，改写句式；L2 可改变连接符、大小写、预算框架和顺序，但不改变商品语义。Harness 与 Agent 同作者带来的共同假设风险必须在报告中披露。

### 15.2 Metamorphic Tests

- 输入大小写变化不改变 anchor 和 ranking；
- 等价标点变化不改变约束集合；
- constraints 顺序变化不改变完整匹配判断；
- catalog 行顺序变化不改变 tie-break 结果；
- 同一 session 重放结果确定；
- 多 session 并行/交错不串状态；
- 加入无关 profile tag 不应覆盖强约束；
- unknown metadata 不应被当作 negative conflict。

### 15.3 失败恢复

可在 clean-set 零影响的前提下独立验证：

- override 后旧证据降权而非一律删除；
- dissatisfaction 后只放松最弱约束；
- stale recommendations 只在明确 dissatisfaction 后惩罚；
- 异常解析时回退 BM25，不返回非法结构；
- runtime exception 仍返回符合 API 的安全结果。

这些能力必须单独开关，clean set 为零影响只是保险价值，不应混入核心增益归因。

## 16. 评估矩阵

每一阶段至少运行：

| 评估 | 目的 |
| --- | --- |
| Unit tests | 模块和边界正确性 |
| Official public evaluation | 主指标 |
| Scenario breakdown | 防止局部回归 |
| Shadow full-turn evaluation | 反事实排名和 Gate 价值 |
| Changed-session diff | 定位收益与损失 |
| Candidate-stage recall | 区分召回、排序、展示问题 |
| Paraphrase L1/L2 | 措辞鲁棒性 |
| Metamorphic tests | 非标签化泛化保护 |
| Latency p50/p95 | 运行成本 |
| Flag-off comparison | 回滚安全性 |

### 16.1 核心指标优先级

1. 新 miss 数；
2. Hit Rate@10；
3. MRR；
4. TechnicalScore；
5. MTTC；
6. scenario 稳定性；
7. p95 latency；
8. 代码复杂度和可维护性。

不能只因 TechnicalScore 小幅上升就晋升。如果上升来自更早但更低 rank 的命中，同时破坏 MRR 或产生新 miss，应保持关闭。

## 17. 明确停止与回滚条件

任一阶段出现以下情况立即停止晋升：

- Hit 低于 0.995；
- 任何场景产生新 miss；
- MRR 相对上阶段下降超过 0.005 且无法由后续预注册阶段解释；
- target 在 anchor set 中但在 anchor lane 无理由消失；
- flag-off 输出不等价；
- runtime trace 出现 ground truth、target 或标签；
- turn p95 超过 baseline 1.25 倍；
- 需要加入 sample ID、ASIN 或公开标签特例才能通过；
- 需要修改 evaluator 才能得到收益；
- 同一配置重复运行结果不一致；
- 单模块无法独立关闭或无法归因。

回滚方式必须始终是关闭实验配置，恢复 `configs/final.json` 的 clean baseline；不得通过删除其他团队成员模块恢复行为。

## 18. 测试清单

### 18.1 Category Anchor

- [ ] 官方 coarse-category fixture 一致；
- [ ] 通用顶层类别被忽略；
- [ ] longest suffix 优先；
- [ ] opening 改写仍命中；
- [ ] category-only 不错误硬过滤；
- [ ] anchor miss 回退 BM25；
- [ ] anchor set target presence 100%；
- [ ] catalog 行顺序不影响结果。

### 18.2 Other-first

- [ ] contentful reply 重置 drain counter；
- [ ] 一次 no-additional 不立即停止；
- [ ] 两次 no-additional 后停止；
- [ ] Boundary 不循环；
- [ ] override 重置新 intent 的提问状态；
- [ ] dissatisfaction 恢复询问；
- [ ] turn 10 不询问。

### 18.3 Ranking

- [ ] 完整多词 feature 匹配；
- [ ] 单词部分重叠不算完整 feature；
- [ ] all-match 领先 partial-match hard negative；
- [ ] budget 使用数值；
- [ ] category 不计入 product match count；
- [ ] negative constraints 仍被统一保护；
- [ ] missing metadata 为 unknown；
- [ ] tie-break 确定。

### 18.4 Gate

- [ ] 无证据时 compact；
- [ ] 足够约束时展开；
- [ ] drained 时展开；
- [ ] dissatisfaction 时展开；
- [ ] override 时安全展开或重置；
- [ ] instability 时展开；
- [ ] 最晚 turn 5 强制展开；
- [ ] Gate 不改变 pre-gate 排序；
- [ ] `public_0161` 和 `public_0172` 不再成为 miss；
- [ ] flag-off 逐 turn 等价。

## 19. 代码评审边界

### 19.1 低风险文件

- `shopping_copilot/evaluation/*`
- `scripts/run_shadow_evaluation.py`
- 新实验 config
- 新测试和实验报告

### 19.2 需要集成评审的文件

- `shopping_copilot/core/contracts.py`
- `shopping_copilot/core/interfaces.py`
- `shopping_copilot/core/pipeline.py`
- `shopping_copilot/core/factory.py`
- `shopping_copilot/state/rule_state.py`
- `shopping_copilot/ranking/heuristic.py`
- `shopping_copilot/policy/heuristic.py`
- `shopping_copilot/retrieval/hybrid.py`

若可通过 component diagnostics 或 trace sink 完成，不应为方便直接扩大 pipeline 的职责。

## 20. 推荐提交与 PR 拆分

建议每个阶段一个可独立评审和回滚的 PR：

1. `test: freeze main baseline fingerprints`
2. `feat: add non-stopping shadow evaluation v2`
3. `feat: add category anchor diagnostics`
4. `feat: add opt-in category anchor retrieval v2`
5. `feat: add opt-in repeated-other policy`
6. `feat: add specificity and complete-match ranking`
7. `feat: add evidence and stability gated slate`
8. `test: add paraphrase and metamorphic stress harness`
9. `docs: record integrated evaluation and promotion decision`

每个 PR 必须附带：

- baseline 与 candidate config；
- 测试结果；
- public metrics；
- scenario metrics；
- changed-session 摘要；
- latency；
- flag-off 对比；
- 已知风险；
- promotion decision。

## 21. 最终晋升标准

只有同时满足以下条件，才能考虑把实验配置晋升为 `configs/final.json`：

| Gate | 要求 |
| --- | --- |
| Baseline origin | 明确基于同步后的 `main@87a64a6` 或更新后重新冻结的 main |
| Tests | 全部通过 |
| Hit | >= 0.995，目标 1.000 |
| MRR | > 0.778343，目标 >= 0.900 |
| MTTC | <= 3.335，目标 <= 2.500 |
| TechnicalScore | > 0.884303，且高于各单阶段候选 |
| Scenario misses | 全部为 0 个新增 miss |
| Boundary | 无循环提问或排序退化 |
| Shadow | 无 Gate 导致的不可恢复 miss |
| Latency | turn p95 <= clean 1.25x |
| Tokens | 0 |
| Flag-off | 与 clean baseline 等价 |
| Data boundary | runtime 零标签访问 |
| Licensing | 无外部源码复制 |
| Documentation | 完整实验记录和复现命令 |

若最终版本只获得类似现有 `smart-slate` 的 `+0.0035` 综合分，但 Hit 从 `0.995` 降为 `0.990`，必须 reject。若像 protected rescue 一样提高 Hit/TechnicalScore 但降低 MRR并超过 p95 门槛，也必须 keep-disabled。

## 22. 预期交付物

完成本计划后应交付：

- 一条从干净 `main` 创建的研究分支；
- 基线 hash 和可复现评估 artifact；
- Shadow Evaluator V2；
- Category Anchor V2 诊断和 live route；
- repeated-`other` 策略；
- specificity/complete-match Ranker；
- evidence + stability Gate；
- paraphrase/metamorphic harness；
- 每阶段消融结果；
- changed-session 和 candidate-stage 报告；
- 最终 promotion/keep-disabled/reject 决策；
- 不含外部源码复制的模块化实现。

## 23. 参考资料

- 本项目 `main`：`https://github.com/vepharix/techjam-conversational-search/tree/87a64a6cc9232cade87514903cf96dd13f6466ac`
- 本项目已有 `smart-slate` 实验：`https://github.com/vepharix/techjam-conversational-search/tree/4e0e382`
- 外部高分方法报告：`https://github.com/leongyiquan/techjam-conversational-search/blob/3acf8fdad1fe2506e57788e24a4463e84a655bd6/REPORT.md`
- 外部高分 Agent 入口：`https://github.com/leongyiquan/techjam-conversational-search/blob/3acf8fdad1fe2506e57788e24a4463e84a655bd6/submission/agent.py`
- 模块化多路线参考：`https://github.com/narmi924/shopping-copilot/tree/88b10b661431af98992d5e71493103674a53d386`

外部仓库当前未见明确开源许可证。本计划仅记录公开可观察的算法思想、评估结论和独立实现要求，不授权复制其源代码。
