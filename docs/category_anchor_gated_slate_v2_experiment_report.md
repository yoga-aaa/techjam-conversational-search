# Category Anchor Gated Slate V2 实验报告

## 1. 结论先行

本轮实验以远程 `main@87a64a6cc9232cade87514903cf96dd13f6466ac` 为基线，在独立分支
`research/category-anchor-gated-slate-v2` 中完成。原始 `main` 的行为没有被覆盖；所有新能力都由配置开关控制。

当前最值得保留的是 Evidence/稳定性 Gate：

| 版本 | Hit@10 | MRR | MTTC | Efficiency | 综合分 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| R0 clean baseline | 0.995000 | 0.778343 | 3.335000 | 0.766500 | 0.884303 | 冻结基线 |
| B+C Category Anchor 初版 | 0.995000 | 0.745655 | 3.310000 | 0.769000 | 0.874996 | reject：召回更快但排序被稀释 |
| B+C Category Anchor 硬过滤 | 1.000000 | 0.671490 | 2.990000 | 0.801000 | 0.861647 | reject：Hit/MTTC 好，MRR 太差 |
| B+O Repeated-other | 0.915000 | 0.723234 | 3.445000 | 0.755500 | 0.825570 | reject：过早追问损伤解析与命中 |
| B+R Specificity 初版 | 0.995000 | 0.735220 | 3.330000 | 0.767000 | 0.871466 | reject：精确匹配路径过强 |
| B+G Gate 初版 | 0.995000 | **0.808629** | 3.475000 | 0.752500 | **0.890589** | 保留为候选，不晋升默认 |
| B+G Gate 提前展开调参 | 0.995000 | 0.798129 | 3.390000 | 0.761000 | 0.889139 | 不如初版 |
| B+C+O+R+G 累计 | 0.950000 | 0.786835 | 3.430000 | 0.757000 | 0.862450 | reject：组合产生新 miss |

因此本轮没有把任何新开关写入 `configs/final.json`。推荐下一轮以 B+G 为候选，在更明确的 pre-gate/coverage 分层上继续做小范围调参；Category Anchor、Repeated-other 和当前 R5 排序实现先保持关闭。

## 2. 本轮到底改了什么

大白话说：我们把高分仓库里“类别先缩小范围、约束更精确、少展示一些结果、观察真实排名”的想法，拆成几个可独立开关的模块。每个模块都能单独打开和关闭，因此可以知道到底是哪一步带来了收益或损失。

新增内容如下：

- `shopping_copilot/retrieval/category_anchor.py`：从本地 50,000 商品目录建立类别倒排索引；类别路线使用独立候选预算，并支持硬类别边界、缓存和词级约束匹配。
- `shopping_copilot/retrieval/hybrid.py`：把类别路线接入 Hybrid Retriever；开关关闭时保留原始 BM25 行为，开启时不让非目标类别越过类别边界。
- `shopping_copilot/ranking/heuristic.py`：增加具体性权重、完整约束奖励、约束匹配数和类别路线观测字段；默认配置不启用新排序行为。
- `shopping_copilot/policy/heuristic.py`：增加 repeated-other 和 slate gate；gate 根据领导商品的实际匹配约束数、轮次、other 是否耗尽、列表稳定性决定展示 Top1 或 Top10。
- `shopping_copilot/core/contracts.py` 与 `state/rule_state.py`：增加 other 追问计数、连续无新增约束计数、上一轮可见列表。
- `shopping_copilot/observability` 与 `core/pipeline.py`：在显式 trace 开关下记录 retrieved、pre-gate、visible 三层，不向 Agent 注入标签。
- `scripts/run_shadow_evaluation.py`：全程跑满十轮，运行结束后才把 target 标签 join 到无标签 trace。
- `scripts/create_split_manifest.py`：固定 Tune 120、Validation 40、Holdout 40，并按场景和冻结基线难度分层。
- `scripts/run_split_evaluation.py`：只评估预先固定的 manifest 分区。
- `tests/test_category_anchor_slate.py`：类别尾部规则、other 计数和 gate 展开规则测试。

## 3. 环境与基线冻结

### 3.1 Git 基线

- 实验分支：`research/category-anchor-gated-slate-v2`
- 基线提交：`main@87a64a6cc9232cade87514903cf96dd13f6466ac`
- 远程 `dev` 对应：`df7e9e9`
- 本轮没有从脏的开发工作区直接切换；实验在独立 worktree 中进行。
- 原工作区已有的未提交修改和输出没有被覆盖。

### 3.2 数据指纹

- 商品目录：50,000 行
- catalog SHA256：`da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67`
- public set SHA256：`857259f7a438e6188ac63e18995b6ff4489bfcfc4a716a798b9a2aa0ee8f7579`
- 公开集：200 sessions，Buying 80、Browsing 80、Intent Override 30、Boundary 10。

### 3.3 R0 执行内容

解决的问题：先证明我们比较的是同一份代码、同一份目录、同一个官方评估器，而不是“换了数据以后看起来变好”。

执行内容：

1. 从远程 main 建立独立实验 worktree。
2. 校验目录行数和 SHA256。
3. 运行官方 48 项测试。
4. 用 `scripts/run_evaluation.py` 跑完整 public set。

结果：48 项测试通过；R0 指标为 Hit `0.995000`、MRR `0.778343`、MTTC `3.335000`、综合分 `0.884303`。接入新模块后，默认开关关闭的重跑结果完全一致，说明没有破坏基线。

复现文件：`output/category_anchor_gated_slate_v2/r0_baseline_after_wiring.json`。

## 4. Shadow Evaluator V2

### 4.1 它解决什么问题

官方评估器一旦看到目标商品进入推荐 Top10，就会提前结束这个 session。因此我们只能知道“最终什么时候第一次看见目标”，却不知道：

- 目标是不是早就被检索到了；
- 目标是不是已经排进了 pre-gate Top10；
- 目标是不是被展示门控藏起来；
- coverage 轮换是不是把原本排名较后的商品提前展示。

这四种情况对应四种完全不同的修法。Shadow Evaluator 的作用就是让 Agent 始终跑完十轮，把这三层证据留下；它自己不读取 ground truth，也不参与推荐排序。

### 4.2 执行方式

执行：

```text
python -m scripts.run_shadow_evaluation \
  --config configs/experiments/slate_gate_v2.json \
  --catalog data/catalog.jsonl \
  --dataset data/public_set.jsonl \
  --output output/category_anchor_gated_slate_v2/r6_shadow_gate.json \
  --trace output/category_anchor_gated_slate_v2/r6_shadow_gate.jsonl
```

运行结束后才用 target ID 离线计算排名。trace 文件约 26MB，包含约 2,000 个 turn 证据。

### 4.3 结果

Gate full 200 的正常可见结果：Hit `0.995000`、MRR `0.808629`、MTTC `3.360000`。

如果只看 trace 中的 pre-gate 前十，Hit 只有 `0.970000`、MRR `0.651938`。这说明 coverage 的 unseen rotation 也在影响可见结果：有些目标原始精排名次超过十，却被 coverage 轮换提前展示。因此不能把最终收益全部归给 gate。

这也是复用 Shadow Evaluator 的核心原因：它把“检索失败、排序失败、门控延迟、coverage 提前展示”拆开了。

## 5. 逐项实验记录

### 5.1 R2/R3：Category Anchor

问题：BM25 把类别当普通关键词。用户第一句话里的类别本来是很强的范围信号，但目标可能在 BM25 前 100 名之外，后面即使得到正确材质/颜色也没有机会回来。

思路：按照官方 evaluator 的类别尾部规则建立类别倒排索引。先用类别得到候选边界，再把候选交给原有精排。实现时额外做了三次微调：

1. 不把 category route 当成普通 fusion 投票。
2. 非目标类别不能越过类别边界。
3. 类别匹配缓存，并给类别 route 单独候选预算，避免每轮重复计算。

结果：

| 版本 | Hit | MRR | MTTC | 综合分 | 说明 |
| --- | ---: | ---: | ---: | ---: | --- |
| 初版整类并入 | 0.995 | 0.745655 | 3.310 | 0.874996 | 召回加入了弱候选，排序被稀释 |
| 硬类别过滤 | 1.000 | 0.671490 | 2.990 | 0.861647 | 更快命中，但目标常排在类别内后面 |
| top200/词化类别实验 | 1.000 | 0.633056 / 0.662579 | 2.860 / 2.935 | 0.852717 / 0.860074 | 候选截断或词化没有修复排序 |

结论：Category Anchor 在 candidate-stage recall 和 MTTC 上有价值，但当前实现没有解决类别内排序；它不能直接晋升。报告保留它，是因为后续可以用独立的类内 BM25/约束精排继续修复，而不是因为当前综合分通过。

### 5.2 R4：Repeated-other

问题：高分实现优先问 `other`，因为官方模拟器可以一次给出最多两个尚未披露的条件；理论上能够减少逐个问颜色、材质、尺寸的轮数。

思路：增加 `other_question_count` 和 `other_no_additional_count`。在达到最大次数或连续没有新约束后停止，回到固定问题/信息增益策略。

结果：Hit `0.915000`、MRR `0.723234`、MTTC `3.445000`、综合分 `0.825570`。

大白话结论：我们现在的解析器和官方模拟器并不是为“永远先问 other”设计的。过早问 other 会让用户回复中的约束落到不稳定的槽位，导致更多商品无法被正确约束。这个点当前 reject；后续如果重新做，应只在候选确实过宽且已有明确类别锚点时触发，而不是全局优先。

### 5.3 R5：具体性权重与完整匹配奖励

问题：长而具体的短语通常比单词级材质更能代表购买意图；同时，满足全部有效约束的商品应该获得额外奖励。

思路：

- 用 token 数给多词约束增加有限权重，设置上限避免长文本刷分；
- 统计 slot-aware 的有效约束命中数；
- 全部有效约束命中时增加独立 bonus；
- 排序输出 `matched_constraint_count` 和 `all_constraints_match`，供 gate 使用。

执行中发现初版把“具体性”和“精确匹配”耦合，导致 specificity-only 与 bonus-only 结果相同，不能据此下结论。随后已拆开代码路径并重跑。重测结果保存在：

- `output/category_anchor_gated_slate_v2/r5_specificity_only_v2_retest.json`
- `output/category_anchor_gated_slate_v2/r5_complete_bonus_only_v2_retest.json`

这两个重测文件应作为最终 R5 结论来源；如果结果仍低于 R0/R6，则按“当前排序路径过强”处理，不直接合并。

### 5.4 R6：Evidence/稳定性 Gate

问题：每轮都展示 Top10 会让用户在信息尚未稳定时看到太多噪声；但只展示 Top1 又可能把目标藏起来。单纯 score-gap 很容易被分数尺度骗过。

思路：

- 信息不足：只展示 Top1；
- 到第 3 轮且第一名至少匹配两个有效约束：展开 Top10；
- 连续 `other` 没有新约束：展开，避免死循环；
- 到第 5 轮强制展开；
- 可选检查上一轮可见列表的稳定性，不稳定时提前展开；
- turn 10 永远不再追问。

结果：初版 gate 的 MRR 从 `0.778343` 升到 `0.808629`，综合分从 `0.884303` 升到 `0.890589`；但 MTTC 从 `3.335` 变成 `3.475`。提前展开调参后综合分为 `0.889139`，不如初版。

大白话结论：Gate 确实能让展示更聚焦、提高首次可见商品的排名，但有一部分目标被延迟展示，所以它适合做候选策略，不适合未经产品权衡就替换默认行为。

## 6. 累计实验

按文档顺序尝试把各点合并：类别边界、other、R5 排序、Gate 一起打开。

结果：Hit `0.950000`、MRR `0.786835`、MTTC `3.430000`、综合分 `0.862450`。Buying Hit 只有 `0.937500`，产生了明确的新 miss。

结论：不能用“最后综合分还可以”掩盖累计方案的 Hit 回归。当前不能复制整个高分 Agent；模块化实验已经证明几个点之间存在负交互，尤其是类别候选池、other 解析和当前排序实现叠加后会把错误放大。

## 7. Validation / Holdout 对照

### 7.1 固定分割

使用固定 seed `category-anchor-gated-slate-v2`，按场景配额和 R0 冻结难度生成：

- Tune：120
- Validation：40
- Locked holdout：40

manifest：`output/category_anchor_gated_slate_v2/split_manifest.json`。

### 7.2 B 与 B+G

| Split | 版本 | Hit | MRR | MTTC | 综合分 |
| --- | --- | ---: | ---: | ---: | ---: |
| Validation 40 | B | 1.000000 | 0.811667 | 2.150000 | 0.920500 |
| Validation 40 | B+G | 1.000000 | 0.871667 | 2.475000 | 0.932000 |
| Holdout 40 | B | 0.975000 | 0.801280 | 2.525000 | 0.897384 |
| Holdout 40 | B+G | 0.975000 | 0.858780 | 2.750000 | 0.910134 |

B+G 在两个分区都提升 MRR 和综合分，但没有提升 Hit，且 MTTC 均变慢。由于 holdout 已用于一次预先冻结的候选对照，后续不再根据 holdout session 调参；这些结果标记为本开发周期的 holdout 审计，不宣称是全新未见事实。

## 8. 结果解释与最终决策

### 8.1 为什么不直接复制 leongyiquan

外部复现显示它的 `submission.Agent` 达到 `1.000 / 0.946929 / 2.095 / 0.962179`，但 starter 入口只有 `0.858944`。它的高分实现是约 641 行单文件；我们需要的是可协作、可逐项回滚的模块。并且没有看到明确开源许可证，所以本轮只借鉴：类别召回、other 带宽、完整匹配奖励、约束数门控、Shadow/压力测试思路，没有复制源代码。

### 8.2 当前保留/关闭清单

| 组件 | 当前决定 | 原因 |
| --- | --- | --- |
| Shadow Evaluator | 保留 | 观测工具，不改变算法，可解释 hidden rank |
| Category Anchor | 保持关闭 | Hit/MTTC 有收益，但当前类内排序严重损失 MRR |
| Repeated-other | 保持关闭 | full Hit 降至 0.915，官方回复解析承受不了全局优先 |
| Specificity/Complete Match 当前实现 | 保持关闭 | full 分数低于基线；需要重新设计融合尺度 |
| Evidence/稳定性 Gate | 保留实验候选 | full/validation/holdout MRR 和综合分均更高，但 MTTC 变慢、Hit 不升 |
| Cumulative Full | reject | Hit 降至 0.950，存在负交互 |

### 8.3 下一轮最值得做什么

1. 先保留 Shadow，不再增加没有证据链的“聪明规则”。
2. 如果目标优先是综合分，围绕 B+G 做小网格：只调展开轮次、最小匹配数和稳定性阈值，并只在 Tune 上选参数。
3. 如果目标优先是 MTTC/Hit，重新设计 Category Anchor 的类内排序：类别只负责过滤和召回，类内使用 BM25/结构化约束的独立排序，不让类别候选的默认 rating 或零 lexical 分数干扰现有排名。
4. `other` 不要全局优先；只在类别已锁定、候选仍过宽、且信息增益估计高于固定问题时触发。
5. 重新做 R5 时把“精确匹配”“稀有度”“具体性”拆成三个可单独回滚的分量，并记录 hard-negative 的具体名次变化。
6. 只有某个候选在 Validation 保住 Hit、MRR 和关键场景新 miss 门槛，才考虑下一轮 Locked holdout；在此之前不要改 `configs/final.json`。

## 9. 复现命令与产物索引

基础检查：

```text
python -m unittest discover -s tests -p "test*.py" -v
python -m scripts.verify_data
python -m scripts.run_evaluation --config configs/final.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --output output/category_anchor_gated_slate_v2/r0_baseline_after_wiring.json
```

单项/累计实验：

```text
python -m scripts.run_evaluation --config configs/experiments/category_anchor_v2.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --output output/category_anchor_gated_slate_v2/r2_category_anchor_*.json
python -m scripts.run_evaluation --config configs/experiments/other_first_v2.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --output output/category_anchor_gated_slate_v2/r4_other_first.json
python -m scripts.run_evaluation --config configs/experiments/slate_gate_v2.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --output output/category_anchor_gated_slate_v2/r6_slate_gate.json
python -m scripts.run_evaluation --config configs/experiments/cumulative_v2.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --output output/category_anchor_gated_slate_v2/r8_cumulative_full.json
```

分割与 Shadow：

```text
python -m scripts.create_split_manifest --dataset data/public_set.jsonl --baseline output/category_anchor_gated_slate_v2/r0_baseline_after_wiring.json --output output/category_anchor_gated_slate_v2/split_manifest.json
python -m scripts.run_split_evaluation --config configs/experiments/slate_gate_v2.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --manifest output/category_anchor_gated_slate_v2/split_manifest.json --split validation --output output/category_anchor_gated_slate_v2/r6_validation_gate.json
python -m scripts.run_shadow_evaluation --config configs/experiments/slate_gate_v2.json --catalog data/catalog.jsonl --dataset data/public_set.jsonl --output output/category_anchor_gated_slate_v2/r6_shadow_gate.json --trace output/category_anchor_gated_slate_v2/r6_shadow_gate.jsonl
```

主要产物：

- 开发文档：[category_anchor_gated_slate_v2_development_plan.md](category_anchor_gated_slate_v2_development_plan.md)
- R0：[r0_baseline_after_wiring.json](../output/category_anchor_gated_slate_v2/r0_baseline_after_wiring.json)
- R6 full：[r6_slate_gate.json](../output/category_anchor_gated_slate_v2/r6_slate_gate.json)
- R6 Shadow：[r6_shadow_gate.json](../output/category_anchor_gated_slate_v2/r6_shadow_gate.json)
- 固定分割：[split_manifest.json](../output/category_anchor_gated_slate_v2/split_manifest.json)

## 10. 质量门禁

本轮最终代码检查：

- 51 项单元测试通过；
- `python -m compileall -q shopping_copilot scripts` 通过；
- `git diff --check` 通过；
- 默认新开关关闭时 R0 指标完全复现；
- Agent 没有读取 public label 或 `ground_truth`；
- Shadow 标签只在运行完成后的离线 join 阶段使用。

这份报告的结论是“保留可解释工具，保留 B+G 作为候选，拒绝当前 C/O/R 累计实现”，不是把所有实验代码默认上线。
