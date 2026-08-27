# V2 双测试：运行、迁移与打包

## 测试范围

本分支只保留两项会话级计分测试：

1. **标准会话测试**：固定 session 最多运行 10 轮，输出 Hit Rate@10、MRR、
   MTTC 和技术分。
2. **英文措辞压力测试**：保持目标、约束和场景不变，分别使用 canonical、
   explicit、conversational、contextual 四种措辞运行配对轨迹，并输出整体、
   分模式和配对稳健性指标。

数据完整性校验是前置步骤，不算第三项测试。sentence-level parser cases 和生成器
诊断也不包含在双测试运行流程内。

## 仓库内结构

```text
configs/language_stress_modes.json       四种措辞模式配置
data/templates/user_utterance_templates.json
evaluator/v2_session_evaluator.py        标准会话入口
evaluator/language_stress_driver.py      措辞压力入口
evaluator/v2_language_support.py         模板与稳定哈希公共代码
scripts/run_v2_holdout.py                两项测试、报告和产物编排
scripts/run_v2_holdout.ps1               Windows 一键入口
scripts/build_portable_v2_tests.py       生成队友可运行 ZIP
tests/test_v2_*.py                        小型 fixture/契约测试
packaging/portable_v2_tests/             便携包启动器与包内说明
packages/*.zip                           已构建便携测试包
```

大型 catalog 和合成 session 不在源码目录展开，避免污染 Git；它们只压缩存放在
便携 ZIP 中。日常开发可把 testkit 解压到主项目的同级 `_testkit` 目录。

## 本项目直接运行

快速开发烟测：

```powershell
.\scripts\run_v2_holdout.ps1 --split development --limit 40 --trace-level none
```

完整锁定 holdout：

```powershell
.\scripts\run_v2_holdout.ps1 --split holdout --trace-level failures
```

默认使用 `configs/final.json`，报告生成在 `artifacts/v2_holdout_*`。

## 给队友使用 ZIP

队友不需要覆盖自己的 `starter/`、`evaluator/` 或 `scripts/`。解压 ZIP 后，在包目录
运行：

```powershell
.\run_tests.ps1 --project-root D:\path\to\teammate-project
```

便携入口会加载该项目的 `starter.agent.Agent`，测试数据和 evaluator 使用 ZIP 内的
命名空间版本，因此不会覆盖队友文件。默认只跑 8 个 development session；使用
`--limit 0` 运行所选 split 全量。

## 重新打包

```powershell
python scripts/build_portable_v2_tests.py `
  --testkit ..\_testkit\techjam-test-v2-simplified-20260827
```

输出：

```text
packages/techjam-v2-two-tests-portable-20260827.zip
packages/techjam-v2-two-tests-portable-20260827.zip.sha256
```

打包脚本会校验核心输入哈希、复制主项目当前 evaluator 源码、转为独立命名空间，
并以确定性的文件顺序和时间戳生成 ZIP。

## 成绩边界

- 数据来自 frozen catalog 的合成标签，不是主办方私有集分数。
- 当前“language/wording pressure”只衡量英语措辞风格，不衡量跨语言翻译。
- development 可用于调试；holdout 只应用于里程碑检查。
- 只认可前 10 个唯一、合法 catalog `parent_asin`，命中后结束该 session。
