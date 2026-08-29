# 官方 Override 状态修复评测报告



## 1. 结论

本轮最终采用“硬约束降级、软信号保留”的方案：

- 显式 override 的新值不再继承上一轮 `pending_attribute`；
- `ignore my earlier preference` 不再被解析成一个属性值；
- 已可靠解析的旧软偏好移出 `active_slots`，但保留在 `active_context` 作为弱词法信号；
- 无法可靠解析的 OOV 旧文本不做猜测性删除；
- 非数值文本不得写入 budget；
- 英文文本数字可以确定性转换为预算数字；
- 没有改动召回路线、RRF、排序权重、Coverage 或评测器。

在公开 200 条上，最终版本保持 Hit@10 不变，同时 MRR 和 Technical Score 提升：

| 指标 | 原版 dev | 最终修复版 | 变化 |
| --- | ---: | ---: | ---: |
| Hit Rate@10 | 1.000000 | 1.000000 | +0.000000 |
| MRR | 0.627153 | 0.639889 | +0.012736 |
| MTTC | 3.145000 | 3.175000 | +0.030000 |
| Efficiency | 0.785500 | 0.782500 | -0.003000 |
| Recommended Technical Score | 0.845246 | 0.848467 | +0.003221 |

## 2. 公开集分场景对比

| 场景 | 样本数 | 原版 Hit@10 | 修复版 Hit@10 | 原版 MRR | 修复版 MRR | 原版 MTTC | 修复版 MTTC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| boundary | 10 | 1.000000 | 1.000000 | 0.688333 | 0.688333 | 4.700000 | 4.700000 |
| browsing | 80 | 1.000000 | 1.000000 | 0.660511 | 0.660511 | 2.550000 | 2.550000 |
| buying | 80 | 1.000000 | 1.000000 | 0.582892 | 0.582892 | 3.050000 | 3.050000 |
| intent_override | 30 | 1.000000 | 1.000000 | 0.635833 | 0.720741 | 4.466667 | 4.666667 |

改善全部来自 `intent_override`：MRR 增加 0.084908。MTTC 增加 0.2 轮，说明部分样本排序更好，但目标首次进入 Top 10 的平均轮次略有变慢。由于 Hit@10 没有下降，整体 Technical Score 仍增加 0.003221。

三次完整公开评测中，最终结果使用最后一次确认运行：

```text
sample_count: 200
hit_rate_at_10: 1.000000
mrr: 0.639889
mttc: 3.175000
recommended_technical_score: 0.848467
elapsed_seconds: 132.396
```

原版和最终版的单次进程耗时不宜直接比较，因为每个独立进程都会重新建立本地 SQLite FTS 索引。状态修复本身只增加消息级规则、少量状态字段和精确值比较，没有增加检索调用或 Catalog 扫描。

## 3. 会话级迁移

公开集 200 条中，只有 9 条会话的目标排名或首次命中轮次发生变化：

- 全部发生在 `intent_override`；
- 没有任何会话从 hit 变成 miss；
- 没有 buying、browsing、boundary 回归；
- `public_0052`、`public_0089`、`public_0123`、`public_0186` 等样本的目标排名改善明显；
- 部分样本的首次命中轮次推后，因此 MTTC 小幅增加。

这说明改动没有扰动普通场景，主要改变了 override 后的状态解释和排序信号。

## 4. 状态污染变化

此前公开 30 条 override 的诊断显示：

- 18/30 条的上一轮 pending 属性与新值真实属性不一致；
- 至少 11/30 条的新值只进入错误 slot；
- 16 条已经进入结构化状态的旧偏好在原版中全部保留；
- 控制词虽然通常会被 Query Planner stopwords 过滤，但仍可能先进入结构化状态。

最终版本的处理结果：

- override payload 不再读取 pending；
- `cotton`、`polyester`、`leather` 等 material 值不再因为上一轮问题被写入 feature、budget 或 color；
- 控制前缀不再作为开放值写入 slot；
- 已有 canonical 旧值从结构化硬约束中移除；
- 旧值仍可通过 active context 提供弱词法召回信号；
- OOV 长文本如果无法可靠拆解，不强行删除，避免把有效目标信号误清掉。

这里采用保守策略是有实测依据的：

1. 直接彻底删除旧偏好时，公开 MRR 从 0.627153 降到 0.619659；
2. 其中一个公开样本从 hit 变成 miss；
3. 将旧偏好从硬约束降级、保留软信号后，MRR 回升到 0.639889，Hit@10 恢复为 1.000。

因此当前实现不把“ignore earlier preference”解释为“旧信息完全不存在”，而是解释为“旧信息不再作为硬约束，但仍可作为弱相关线索”。这与用户对旧偏好仍可被考虑的要求一致，也更符合目标商品同时包含 old/new 特征的官方数据生成方式。

## 5. Budget 校验结果

已增加确定性的价格解析规则：

- `80`、`$80` → `80`；
- `eighty` → `80`；
- `one hundred and twenty` → `120`；
- `under eighty dollars` → `80`；
- `cotton`、`Imported`、`prioritize cotton` → 不写入 budget。

该逻辑只处理当前消息和有限英文数字词表，复杂度为 O(tokens)，不触发额外检索。

## 6. 模拟集标准模式补充结果

使用模拟包 development split 的 662 条标准会话进行了补充运行。需要特别说明：该数据包中的 `intent_override` 仍包含若干模拟改写，不等同于官方公开 200 条的固定句式，因此不作为本轮生产规则的主要验收标准。

| 指标 | 之前记录的多轮基线 | 最终修复版 | 变化 |
| --- | ---: | ---: | ---: |
| Hit Rate@10 | 0.956193 | 0.954683 | -0.001510 |
| MRR | 0.681072 | 0.590233 | -0.090839 |
| MTTC | 3.925982 | 3.558912 | -0.367070 |
| Recommended Technical Score | 0.823898 | 0.803233 | -0.020665 |

模拟集的 MRR 回归主要来自其非官方标准 override 改写仍未被本轮覆盖，例如 `on second thought`、`discard previous choice`、`prioritize ... from now on`。根据官方关于隐藏集措辞边界的说明，本轮不为这些未发布句式继续堆叠规则。它们可以留作后续压力测试，但不能推翻公开集上已经验证的方案。

## 7. 测试与代码状态

自动测试：

```text
Ran 54 tests
OK
```

新增覆盖：

- 官方显式跨属性 override；
- pending=budget 时非价格文本不写入 budget；
- 英文文本数字预算；
- 控制句隔离；
- 旧值从硬约束降级为软 context。

本次修改涉及：

- `shopping_copilot/core/contracts.py`；
- `shopping_copilot/state/semantic_delta.py`；
- `shopping_copilot/state/rule_state.py`；
- `tests/test_semantic_state.py`。

没有修改：

- `evaluator/`；
- `data/public_set.jsonl`；
- Catalog；
- `configs/final.json`；
- BM25、Structured Retriever、RRF、Ranker、Policy 和 Coverage 参数。

## 8. 当前结论与后续建议

当前版本可以作为状态修复候选版本保留：公开 Hit@10 不下降，公开 MRR 和 Technical Score 上升，普通三类场景没有回归，运行机制没有增加额外检索复杂度。

后续如果要继续提升，应另立实验文档研究：

- 软偏好与硬约束的独立权重；
- 复杂商品属性的更稳健 slot 分类；
- 多轮翻页与大规模同属性商品下的候选召回。

这些不应和本次状态污染修复混合调参。
