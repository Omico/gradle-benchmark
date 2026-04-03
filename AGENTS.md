# Agent 说明

本仓库用于**生成**多模块 Gradle 基准工程，便于测量构建/配置阶段性能。交付物是生成器脚本，而非预置的大体量示例工程。

## 核心文件

- `generate-gradle-benchmark.py`：唯一需要维护的生成逻辑（Python 3 标准库，无第三方依赖）。
- `run-gradle-profiler-matrix.py`：对矩阵（或任一含多子工程的父目录）下**每个** Gradle 工程**依次**调用 [gradle-profiler](https://github.com/gradle/gradle-profiler)，**不并行**（单进程 `for` 循环）。**默认**在每次运行 profiler 前清理该子工程的 `.gradle` 与各 `mNNNN/build/`；`--no-clean-workspace` 关闭。`--compare-gradle-modes` 时在**同一子工程**上按顺序跑多种 Gradle 开关组合（见 `profiler/gradle-modes.scenarios`），统一带 **`--rerun-tasks`** 与 **`--parallel`**；结果写入 `<matrix-root>/profiler-results/summary.md` 与 `summary.tsv`（中位耗时；可选 Build Scan 风格 URL，默认将控制台写入 `profiler-console.log` 并解析；`--no-capture-profiler-console` 关闭）。任一次 `gradle-profiler` 非零退出码则**立即终止**整脚本；`--compare-gradle-modes` 下出错时**不写** summary。
- `profiler/classes.scenarios`：单场景模式默认 `classes`（`clean` + `classes`）。
- `profiler/gradle-modes.scenarios`：`baseline`（无缓存）/`build-cache`/`build-cache-configuration-cache`/`all-three`（构建缓存 + 配置缓存 + configure-on-demand）；均带 `--rerun-tasks` 与 `--parallel`。

## 生成器行为摘要

- **默认单工程**：`-n` 默认 **1000**；`--files-per-module` 默认 **10**；`--lines-per-file` 默认 **500**；`--module-source-mode` 默认 **mixed**；`--gradle-version` 默认 **7.5.1**；`--kotlin-version` 默认 **2.0.21**（仅 kotlin-only / mixed 时写入根与子模块；**java-only 根工程不应用 Kotlin 插件**）。省略 `--out` 时，在**当前工作目录**（或 `--project-parent`）下创建 `gradle-bench-0001` 样式目录；序号为已有「前缀 + 数字」目录名的最大值 + 1（四位补零）。前缀由 `--project-prefix` 控制（默认 `gradle-bench-`）。
- **`--matrix`**：固定 **1000** 模块、每文件 **500** 行；全因子 **Gradle 7.5.1 / 8.14.4 / 9.4.1 × JDK 11 / 17 / 21 × java-only / kotlin-only / mixed**；**kotlin-only 与 mixed** 再 × **Kotlin 2.0.21、2.1.21、2.2.21、2.3.20、2.4.0-Beta1**；**java-only 不展开 Kotlin 版本**（共 **99** 份子工程，见 `MATRIX-MANIFEST.txt`）。
- **模块名**：`m0000` … `m{N-1}`，在 `settings.gradle.kts` 中 `include`。
- **源码**：每模块若干 `GenXXXX` 类；`--module-source-mode` 控制仅 Java / 仅 Kotlin / 混合；同模块内类之间会互相调用（含 Java ↔ Kotlin）。
- **模块依赖**：`--dependency-pattern`：`chain`（依次依赖前一模块）、`star`（均依赖 `m0000`）、`binary-tree`（父索引 `(i-1)/2`）。
- **行数**：`--files-per-module`、`--lines-per-file`；可选 `--lines-per-module` 在文件间均分总行数。
- **Wrapper**：若存在与 **`--gradle-version`** 同名的目录 `templates/gradle-<版本>`（且含 `gradlew`），则将该目录下完整 Wrapper 复制到生成根目录；否则仅写 `gradle-wrapper.properties`，需在生成目录内执行 `gradle wrapper`（生成器结束时会打印建议命令）。

修改生成器时，请保持 `python3 generate-gradle-benchmark.py --help` 与文档一致；能用标准库解决的问题不要新增依赖。

## 常用命令

```bash
# 完整参数
python3 generate-gradle-benchmark.py --help

# 默认规模（1000 模块 × 10 文件 × 500 行，mixed）
python3 generate-gradle-benchmark.py

# 固定输出目录
python3 generate-gradle-benchmark.py --out ./my-bench -n 10 --module-source-mode mixed

# 全维度矩阵（99 份子工程，磁盘与生成时间极大）
python3 generate-gradle-benchmark.py --matrix --matrix-out ./gradle-bench-matrix

# 顺序 profiler（需本机已安装 gradle-profiler；各子目录建议已有 gradlew）
python3 run-gradle-profiler-matrix.py --matrix-root ./gradle-bench-matrix --warmups 2 --iterations 5

# 多 Gradle 行为对比 + 汇总表（默认捕获控制台以解析 Build Scan 链接；不需要时可加 --no-capture-profiler-console）
python3 run-gradle-profiler-matrix.py --matrix-root ./gradle-bench-matrix --compare-gradle-modes
```

## Agent 协作约定

1. **小改动**：只改与任务相关的函数或参数；保持现有命名与输出格式（Gradle Kotlin DSL、模块目录结构）。
2. **验证**：小样本可 `python3 generate-gradle-benchmark.py -n 2 --files-per-module 3 --lines-per-file 5 --out /tmp/test-bench --force` 检查目录与 `build.gradle.kts` 是否合理；大 `-n` 或 `--matrix` 仅在有需要时由用户本地运行。
3. **生成目录**：`gradle-bench-*` 或与用户 `--out` 路径一般由用户生成，**默认不要**把大规模生成结果提交进仓库，除非用户明确要求。
4. **语言**：对用户说明以简体中文为主（若用户规则要求）。

## 相关版本（脚本内硬编码 / 默认值）

- Gradle Wrapper 默认：`--gradle-version` → **7.5.1**（矩阵另含 **8.14.4**、**9.4.1**）
- Kotlin Gradle 插件默认：`--kotlin-version` → **2.0.21**（矩阵另含 **2.1.21** … **2.4.0-Beta1**）
- 默认 JVM 工具链 / `--jvm-target`：**11**（矩阵另含 **17**、**21**）

调整时需同时考虑三者与 `build.gradle.kts` 模板的兼容性。
