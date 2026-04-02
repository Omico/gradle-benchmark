#!/usr/bin/env python3
"""
在矩阵父目录下**依次**对每个子工程运行 gradle-profiler（单进程 for 循环，不做并行）。

前置：各子目录内已可使用 Gradle（建议已生成 gradle wrapper 并能 ./gradlew classes）。
本机需已安装 gradle-profiler（PATH 中可执行，或 --profiler 指定路径）。

Gradle 行为对比（--compare-gradle-modes）：
  在 profiler/gradle-modes.scenarios 中定义多种场景；均以 clean + classes 为前提，
  并统一附加 --rerun-tasks 作为基准（避免任务 UP-TO-DATE 跳过）。
  各场景差异：构建缓存 / 配置缓存 / configure-on-demand 的组合。
  跑完后在 <results-root>/summary.md 与 summary.tsv 生成汇总表；可选附上 Build Scan 风格 URL（路径含 /s/），
  默认将 gradle-profiler 输出写入各次输出目录下的 profiler-console.log 并从中解析；加 --no-capture-profiler-console 可关闭。

示例:
  cd /path/to/gradle-bench-matrix
  python3 ../run-gradle-profiler-matrix.py --matrix-root .

  python3 run-gradle-profiler-matrix.py --matrix-root ./gradle-bench-matrix \\
    --warmups 3 --iterations 10

  # 对每个矩阵子工程顺序跑完 baseline / build-cache / configuration-cache 等场景，并写 summary
  python3 run-gradle-profiler-matrix.py --matrix-root ./gradle-bench-matrix \\
    --compare-gradle-modes --warmups 2 --iterations 5

  # 只测前几个目录（调试用）
  python3 run-gradle-profiler-matrix.py --matrix-root ./gradle-bench-matrix --limit 2 --dry-run

  # 指定 Gradle 守护进程/构建 JDK（写入 <gradle-user-home>/gradle.properties 的 org.gradle.java.home；Tooling API 会读取）
  python3 run-gradle-profiler-matrix.py --matrix-root ./gradle-bench-matrix --jdk-version 11
  # 或直接 --java-home <JDK 根目录>；与 --gradle-version 无关；请在仓库根等与 profiler 相同的 cwd 下运行（默认 GUH 为 ./gradle-user-home）
  # 超大工程若遇守护进程 OOM（GC thrashing），可增大堆，例如：
  #   --gradle-jvmargs '-Xmx8g -XX:MaxMetaspaceSize=1g'
  # 中断后续跑（跳过已产出有效 benchmark.csv 的子工程/场景）：
  #   加 --resume（与上次相同的 --matrix-root、--results-root 等）

默认在每次执行 gradle-profiler 前清理子工程内 .gradle 与各模块 build/。
加 --no-clean-workspace 可关闭。

遇 gradle-profiler 非零退出码时**立即退出**整个进程（不继续后续子工程或对比场景）；`--compare-gradle-modes`
下出错时不会写入 summary 表。
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_PROFILER_DIR = Path(__file__).resolve().parent / "profiler"
DEFAULT_SCENARIO_FILE = REPO_PROFILER_DIR / "classes.scenarios"
DEFAULT_SCENARIO_NAME = "classes"
DEFAULT_MODES_SCENARIO_FILE = REPO_PROFILER_DIR / "gradle-modes.scenarios"

# 须与 gradle-modes.scenarios 内场景名一致，且顺序即执行顺序（全单进程顺序执行）
GRADLE_MODE_SCENARIO_NAMES: tuple[str, ...] = (
    "baseline",
    "build-cache",
    "configuration-cache",
    "configure-on-demand",
    "build-cache-configuration-cache",
    "all-three",
)

# 表格展示用：场景 -> (构建缓存, 配置缓存, 按需配置)
SCENARIO_FLAGS: dict[str, tuple[str, str, str]] = {
    "baseline": ("否", "否", "否"),
    "build-cache": ("是", "否", "否"),
    "configuration-cache": ("否", "是", "否"),
    "configure-on-demand": ("否", "否", "是"),
    "build-cache-configuration-cache": ("是", "是", "否"),
    "all-three": ("是", "是", "是"),
}

# 与生成器子模块目录名 m0000… 一致
_MODULE_DIR = re.compile(r"^m\d{4}$")


def _macos_java_home_for_version(version: str) -> Path:
    try:
        out = subprocess.check_output(
            ["/usr/libexec/java_home", "-v", version],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        msg = f"无法解析 JDK 版本 {version!r}（/usr/libexec/java_home -v）"
        if err:
            msg += f": {err}"
        raise SystemExit(f"{msg}\n请安装对应 JDK 或改用 --java-home 指定目录。") from e
    return Path(out)


def _ensure_jdk_has_java(java_home: Path) -> None:
    java_bin = java_home / "bin"
    exe = java_bin / ("java.exe" if sys.platform == "win32" else "java")
    if not exe.is_file():
        print(
            f"在 {java_bin} 下未找到 java 可执行文件，请检查 --java-home / --jdk-version。",
            file=sys.stderr,
        )
        raise SystemExit(2)


def resolve_gradle_java_home(args: argparse.Namespace) -> Path | None:
    """根据 CLI 得到 Gradle 守护进程/构建应使用的 JDK（org.gradle.java.home）；未指定则 None。"""
    if args.java_home is not None:
        home = Path(args.java_home).expanduser().resolve()
        if not home.is_dir():
            print(f"--java-home 不是目录: {home}", file=sys.stderr)
            raise SystemExit(2)
        _ensure_jdk_has_java(home)
        return home
    if args.jdk_version:
        if sys.platform != "darwin":
            print(
                "--jdk-version 仅在 macOS 上可用（使用 /usr/libexec/java_home）；"
                "其他系统请使用 --java-home。",
                file=sys.stderr,
            )
            raise SystemExit(2)
        mac = _macos_java_home_for_version(args.jdk_version)
        _ensure_jdk_has_java(mac)
        return mac
    return None


def upsert_gradle_user_home_property(
    gradle_user_home: Path,
    key: str,
    value: str,
    *,
    dry_run: bool = False,
) -> None:
    """在 gradle_user_home/gradle.properties 中设置单行属性（供 Gradle Tooling API / 守护进程读取）。

    gradle-profiler 通过 GradleConnector 起守护进程时不会走 gradlew，环境变量 GRADLE_OPTS 往往不生效；
    写入该文件与 gradle-profiler 的行为一致。
    """
    assign = f"{key}={value}\n"
    props = gradle_user_home / "gradle.properties"
    if dry_run:
        print(f"    [dry-run] 将写入 {props}: {key}={value}", flush=True)
        return
    gradle_user_home.mkdir(parents=True, exist_ok=True)
    if props.is_file():
        lines = props.read_text(encoding="utf-8").splitlines(keepends=True)
        out: list[str] = []
        found = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                out.append(line)
                continue
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                if not found:
                    out.append(assign)
                    found = True
                continue
            out.append(line)
        if not found:
            if out and not out[-1].endswith(("\n", "\r")):
                out[-1] = out[-1] + "\n"
            out.append(assign)
        props.write_text("".join(out), encoding="utf-8")
    else:
        props.write_text(assign, encoding="utf-8")
    print(f"  已写入 {props.name}：{key}={value}", flush=True)


def upsert_gradle_user_home_java_home(
    gradle_user_home: Path, java_home: Path, *, dry_run: bool = False
) -> None:
    """在 gradle_user_home/gradle.properties 中设置 org.gradle.java.home。"""
    _ensure_jdk_has_java(java_home)
    key = "org.gradle.java.home"
    value = str(java_home.resolve()).replace("\\", "/")
    upsert_gradle_user_home_property(gradle_user_home, key, value, dry_run=dry_run)


def clean_gradle_workspace(project: Path, *, dry_run: bool = False) -> None:
    """删除子工程根下 .gradle、根 build/、各 mNNNN/build/（不删 gradle wrapper）。"""
    to_remove: list[Path] = []
    gradle_state = project / ".gradle"
    if gradle_state.is_dir():
        to_remove.append(gradle_state)
    root_build = project / "build"
    if root_build.is_dir():
        to_remove.append(root_build)
    try:
        for child in project.iterdir():
            if child.is_dir() and _MODULE_DIR.match(child.name):
                mb = child / "build"
                if mb.is_dir():
                    to_remove.append(mb)
    except OSError:
        pass
    for p in to_remove:
        if dry_run:
            print(f"    [dry-run] 将删除 {p}", flush=True)
        else:
            shutil.rmtree(p, ignore_errors=True)


def maybe_clean_workspace(args: argparse.Namespace, project: Path) -> None:
    if getattr(args, "no_clean_workspace", False):
        return
    print(f"  清理工作区（.gradle 与各模块 build/）: {project.name}", flush=True)
    clean_gradle_workspace(project, dry_run=args.dry_run)


def find_subprojects(matrix_root: Path) -> list[Path]:
    """含有 settings.gradle.kts 的子目录，按名称排序。"""
    if not matrix_root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(matrix_root.iterdir()):
        if p.is_dir() and (p / "settings.gradle.kts").is_file():
            out.append(p)
    return out


# gradle-profiler / Gradle 控制台里出现的链接（行尾可能有标点）
_PROFILER_CONSOLE_URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+")


def _dedupe_urls_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _strip_trailing_url_punctuation(url: str) -> str:
    return url.rstrip(').,;]>"')


def urls_from_profiler_console_text(text: str) -> list[str]:
    """从捕获的 profiler 控制台输出中提取 Build Scan 风格 URL（路径含 /s/）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _PROFILER_CONSOLE_URL_RE.finditer(text):
        u = _strip_trailing_url_punctuation(m.group(0))
        if "/s/" not in u:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def find_benchmark_csv(output_dir: Path) -> Path | None:
    for p in (
        output_dir / "benchmark.csv",
        output_dir / "profile-out" / "benchmark.csv",
    ):
        if p.is_file():
            return p
    return None


def median_measured_seconds(csv_path: Path) -> float | None:
    """解析 gradle-profiler 默认/WIDE 或 LONG 的 benchmark.csv，取 measured 构建耗时中位数（秒）。"""
    raw_head = csv_path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    if not raw_head:
        return None
    first = raw_head[0]
    if "Phase" in first and "Duration" in first:
        med = _median_from_long_csv(csv_path)
        if med is not None:
            return med
        return _median_long_any_sample(csv_path)
    return _median_from_wide_csv(csv_path)


def _median_from_long_csv(csv_path: Path) -> float | None:
    measured: list[float] = []
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return None
        for row in reader:
            phase = (row.get("Phase") or "").lower()
            if "measur" not in phase:
                continue
            sample = (row.get("Sample") or "").lower()
            if "total" not in sample or "execution" not in sample:
                continue
            raw = (row.get("Duration") or "").strip()
            if not raw:
                continue
            try:
                measured.append(float(raw))
            except ValueError:
                pass
    return float(statistics.median(measured)) if measured else None


def _median_long_any_sample(csv_path: Path) -> float | None:
    buckets: dict[int, list[float]] = {}
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phase = (row.get("Phase") or "").lower()
            if "measur" not in phase:
                continue
            try:
                it = int(row.get("Iteration") or "-1")
            except ValueError:
                it = -1
            raw = (row.get("Duration") or "").strip()
            if not raw:
                continue
            try:
                buckets.setdefault(it, []).append(float(raw))
            except ValueError:
                pass
    per_iter = [statistics.mean(v) for v in buckets.values() if v]
    if not per_iter:
        return None
    return float(statistics.median(per_iter))


def _median_from_wide_csv(csv_path: Path) -> float | None:
    """WIDE：首列为构建序号标签，measured build 各行取第一个数值列作为秒。"""
    vals: list[float] = []
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    for row in rows:
        if not row:
            continue
        label = row[0].strip().lower()
        if "measured" not in label or "build" not in label:
            continue
        for cell in row[1:]:
            cell = cell.strip()
            if not cell:
                continue
            try:
                vals.append(float(cell))
                break
            except ValueError:
                continue
    return float(statistics.median(vals)) if vals else None


def profiler_output_has_median(out_dir: Path) -> bool:
    """输出目录是否已有可解析出 measured 耗时中位数的 benchmark.csv（用于 --resume）。"""
    csv_p = find_benchmark_csv(out_dir)
    if csv_p is None:
        return False
    return median_measured_seconds(csv_p) is not None


@dataclass
class SummaryRow:
    project: str
    scenario: str
    build_cache: str
    config_cache: str
    configure_on_demand: str
    median_seconds: str
    build_scans: str


def write_summary_tables(rows: list[SummaryRow], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    tsv = dest_dir / "summary.tsv"
    md = dest_dir / "summary.md"

    tsv.write_text(
        "\t".join(
            [
                "project",
                "scenario",
                "build_cache",
                "configuration_cache",
                "configure_on_demand",
                "median_seconds",
                "build_scan_urls",
            ]
        )
        + "\n"
        + "\n".join(
            "\t".join(
                [
                    r.project,
                    r.scenario,
                    r.build_cache,
                    r.config_cache,
                    r.configure_on_demand,
                    r.median_seconds,
                    r.build_scans.replace("\n", " ").replace("\t", " "),
                ]
            )
            for r in rows
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Gradle Profiler 汇总（可选：Build Scan URL）",
        "",
        "所有场景均在 `clean` 后执行 `classes`，并统一带 **`--rerun-tasks`** 作为基准（避免 UP-TO-DATE 跳过任务）。",
        "",
        "说明：默认在**每次**运行 gradle-profiler 前会清理该子工程根目录 `.gradle` 与各 `mNNNN/build/`（`--no-clean-workspace` 可关闭），"
        "以便场景之间尽量不共用本地配置/构建状态。",
        "",
        "中位耗时来自 gradle-profiler 的 `benchmark.csv`（默认 WIDE；若为新版本 LONG 亦可）中对 **measured** 构建行的耗时中位数。",
        "",
        "| 子工程 | 场景 | 构建缓存 | 配置缓存 | 按需配置 | 中位耗时 (s) | Build Scan URL（默认从 profiler-console.log 解析含 /s/；`--no-capture-profiler-console` 时无） |",
        "|--------|------|----------|----------|----------|----------------|----------------------------------------------------------------------------------------|",
    ]
    for r in rows:
        scans_md = r.build_scans.replace("|", "\\|").replace("\n", "<br>")
        lines.append(
            f"| {r.project} | {r.scenario} | {r.build_cache} | {r.config_cache} | {r.configure_on_demand} "
            f"| {r.median_seconds} | {scans_md} |"
        )
    lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")


def build_summary_row_for_mode_scenario(
    project_name: str,
    scenario: str,
    out_dir: Path,
    args: argparse.Namespace,
) -> SummaryRow:
    """从已有 profiler 输出目录组装一行对比汇总（新跑或 --resume 跳过均可）。"""
    urls_console: list[str] = []
    console_log = (
        out_dir / "profiler-console.log" if args.capture_profiler_console else None
    )
    if console_log is not None and console_log.is_file():
        urls_console = urls_from_profiler_console_text(
            console_log.read_text(encoding="utf-8", errors="replace")
        )
    urls = _dedupe_urls_preserve_order(urls_console)

    median_s = ""
    csv_p = find_benchmark_csv(out_dir)
    if csv_p:
        med = median_measured_seconds(csv_p)
        if med is not None:
            median_s = f"{med:.2f}"

    flags = SCENARIO_FLAGS.get(scenario, ("?", "?", "?"))
    scan_text = (
        "；".join(urls)
        if urls
        else (
            "（无 URL；已使用 --no-capture-profiler-console，无法从控制台日志解析链接）"
            if not args.capture_profiler_console
            else "（profiler-console.log 中未解析到含 /s/ 的链接）"
        )
    )

    return SummaryRow(
        project=project_name,
        scenario=scenario,
        build_cache=flags[0],
        config_cache=flags[1],
        configure_on_demand=flags[2],
        median_seconds=median_s or "—",
        build_scans=scan_text,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="对矩阵内每个 Gradle 子工程**顺序**运行 gradle-profiler（不并行）。"
        "任一次 gradle-profiler 非零退出码即终止整个脚本（含 --compare-gradle-modes，且不写 summary）。",
    )
    p.add_argument(
        "--matrix-root",
        type=Path,
        default=Path("gradle-bench-matrix"),
        help="矩阵父目录（其下每个含 settings.gradle.kts 的子目录会单独测一轮）",
    )
    p.add_argument(
        "--scenario-file",
        type=Path,
        default=DEFAULT_SCENARIO_FILE,
        help=f"单场景模式下的场景文件（默认：{DEFAULT_SCENARIO_FILE}）",
    )
    p.add_argument(
        "--scenario-name",
        default=DEFAULT_SCENARIO_NAME,
        help="单场景模式下的场景名（默认 classes）",
    )
    p.add_argument(
        "--compare-gradle-modes",
        action="store_true",
        help="对每个子工程按 gradle-modes.scenarios 顺序跑多种 Gradle 行为（构建/配置缓存、按需配置等），"
        "并生成 summary.md / summary.tsv",
    )
    p.add_argument(
        "--modes-scenario-file",
        type=Path,
        default=DEFAULT_MODES_SCENARIO_FILE,
        help=f"--compare-gradle-modes 时使用的场景文件（默认：{DEFAULT_MODES_SCENARIO_FILE}）",
    )
    p.add_argument(
        "--profiler",
        default="gradle-profiler",
        help="gradle-profiler 可执行文件（默认从 PATH 查找）",
    )
    p.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help="profiler 输出根目录（默认：<matrix-root>/profiler-results）",
    )
    p.add_argument(
        "--warmups", type=int, default=2, help="预热次数（交给 gradle-profiler）"
    )
    p.add_argument(
        "--iterations", type=int, default=5, help="计时迭代次数（交给 gradle-profiler）"
    )
    p.add_argument(
        "--gradle-version",
        default="7.5.1",
        help="传给 gradle-profiler 的 --gradle-version（默认 7.5.1；可多版本对比时逗号分隔按 profiler 文档）",
    )
    jg = p.add_mutually_exclusive_group()
    jg.add_argument(
        "--java-home",
        type=Path,
        default=None,
        help="Gradle 守护进程使用的 JDK 根目录（写入 --gradle-user-home 下 gradle.properties 的 org.gradle.java.home；"
        "gradle-profiler 仍用自身 JVM。与 --gradle-version / 工程 JVM 工具链无关）",
    )
    jg.add_argument(
        "--jdk-version",
        default=None,
        metavar="VERSION",
        help="macOS：java_home -v 解析路径并同上写入 org.gradle.java.home（例 17、11、1.8）；"
        "其他系统请用 --java-home",
    )
    p.add_argument(
        "--gradle-user-home",
        type=Path,
        default=None,
        help="传给 gradle-profiler 的 --gradle-user-home。未指定时与 profiler 一致：当前目录下 gradle-user-home。"
        "若使用 --java-home/--jdk-version 且未指定本项，则使用 ./gradle-user-home 的绝对路径并写入 org.gradle.java.home。",
    )
    p.add_argument(
        "--gradle-jvmargs",
        default=None,
        metavar="JVMARGS",
        help="写入上述 gradle-user-home 下 gradle.properties 的 org.gradle.jvmargs（守护进程堆等；"
        "适合千模块工程避免 OOM）。示例：-Xmx8g -XX:MaxMetaspaceSize=1g（含空格时请整段加引号）。",
    )
    p.add_argument(
        "--skip-gradlew-check",
        action="store_true",
        help="不在子工程下检查 gradlew（可配合 --gradle-version 等由 profiler 拉取 Gradle；默认会检查）",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅处理前 N 个子目录（调试用）",
    )
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不运行")
    p.add_argument(
        "--no-clean-workspace",
        action="store_true",
        help="运行 gradle-profiler 前不清理子工程内的 .gradle 与各模块 build/（默认每次运行前会清理）",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="续跑：若目标输出目录下已有可解析 measured 中位数的 benchmark.csv，则跳过该次子工程/场景"
        "（须与上次使用相同的 --matrix-root、--results-root/--compare-gradle-modes 布局；dry-run 时不跳过以便预览命令）",
    )
    p.add_argument(
        "--capture-profiler-console",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="将 gradle-profiler 的 stdout+stderr 合并写入本次 --output-dir 下的 profiler-console.log"
        "（等同 2>&1|tee）；默认开启；--compare-gradle-modes 时从该文件解析 Build Scan 风格 URL（含 /s/）写入汇总表",
    )
    p.add_argument(
        "extra_profiler_args",
        nargs="*",
        help="附加参数原样传给 gradle-profiler（放在命令末尾）",
    )
    return p.parse_args()


def _run_profiler_tee(cmd: list[str], log_path: Path) -> int:
    """运行命令并将合并后的 stdout+stderr 写入 log_path，同时打印到当前进程 stdout。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as logf:
            for line in proc.stdout:
                logf.write(line)
                print(line, end="", flush=True)
    finally:
        proc.stdout.close()
    return int(proc.wait())


def run_profiler_once(
    args: argparse.Namespace,
    project: Path,
    scenario_file: Path,
    scenario_name: str,
    output_dir: Path,
    *,
    profiler_gradle_user_home: Path | None,
    console_log: Path | None = None,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        args.profiler,
        "--benchmark",
        "--project-dir",
        str(project),
        "--scenario-file",
        str(scenario_file.resolve()),
        "--output-dir",
        str(output_dir),
        "--warmups",
        str(args.warmups),
        "--iterations",
        str(args.iterations),
    ]
    if args.gradle_version:
        cmd.extend(["--gradle-version", args.gradle_version])
    if profiler_gradle_user_home is not None:
        cmd.extend(["--gradle-user-home", str(profiler_gradle_user_home)])
    cmd.append(scenario_name)
    cmd.extend(args.extra_profiler_args)

    print(f"    → {subprocess.list2cmdline(cmd)}", flush=True)
    if args.dry_run:
        return 0
    if console_log is not None:
        return _run_profiler_tee(cmd, console_log)
    return subprocess.call(cmd)


def main() -> int:
    args = parse_args()
    gradle_java_home = resolve_gradle_java_home(args)
    profiler_guh: Path | None = None
    if gradle_java_home is not None:
        profiler_guh = (
            args.gradle_user_home or Path.cwd() / "gradle-user-home"
        ).resolve()
        upsert_gradle_user_home_java_home(
            profiler_guh, gradle_java_home, dry_run=args.dry_run
        )
    elif args.gradle_user_home is not None:
        profiler_guh = args.gradle_user_home.expanduser().resolve()

    if args.gradle_jvmargs:
        jv = args.gradle_jvmargs.strip()
        if not jv:
            print("--gradle-jvmargs 不能为空", file=sys.stderr)
            return 2
        if profiler_guh is None:
            profiler_guh = (
                args.gradle_user_home or Path.cwd() / "gradle-user-home"
            ).resolve()
        upsert_gradle_user_home_property(
            profiler_guh,
            "org.gradle.jvmargs",
            jv,
            dry_run=args.dry_run,
        )

    root = args.matrix_root.resolve()
    if not root.is_dir():
        print(f"--matrix-root 不是目录: {root}", file=sys.stderr)
        return 2

    results_root = (args.results_root or (root / "profiler-results")).resolve()
    projects = find_subprojects(root)
    if args.limit is not None:
        projects = projects[: max(0, args.limit)]

    if not projects:
        print(f"未在 {root} 下找到含 settings.gradle.kts 的子工程。", file=sys.stderr)
        return 2

    if args.compare_gradle_modes:
        modes_file = args.modes_scenario_file.resolve()
        if not modes_file.is_file():
            print(f"场景文件不存在: {modes_file}", file=sys.stderr)
            return 2
        return run_compare_modes(
            args,
            projects,
            results_root,
            modes_file,
            profiler_gradle_user_home=profiler_guh,
        )

    scenario_path = args.scenario_file.resolve()
    if not scenario_path.is_file():
        print(f"场景文件不存在: {scenario_path}", file=sys.stderr)
        return 2

    resume_note = "（--resume：有效 benchmark 已存在则跳过）" if args.resume else ""
    print(
        f"将对 {len(projects)} 个子工程**顺序**执行 gradle-profiler（单进程，不并行）{resume_note}。",
        flush=True,
    )

    failed = 0
    for idx, proj in enumerate(projects, start=1):
        rel = proj.name
        out_dir = results_root / rel
        out_dir.mkdir(parents=True, exist_ok=True)

        if not args.skip_gradlew_check and not (proj / "gradlew").is_file():
            print(
                f"[{idx}/{len(projects)}] 跳过（无 gradlew）: {proj}\n"
                f"  请在该目录执行: gradle wrapper --gradle-version <版本>",
                file=sys.stderr,
                flush=True,
            )
            failed += 1
            continue

        print(f"\n[{idx}/{len(projects)}] {rel}\n  → 输出: {out_dir}", flush=True)
        if args.resume and not args.dry_run and profiler_output_has_median(out_dir):
            print(
                f"  （--resume）跳过：已有可解析的 benchmark 结果",
                flush=True,
            )
            continue
        maybe_clean_workspace(args, proj)
        console_log = (
            out_dir / "profiler-console.log" if args.capture_profiler_console else None
        )
        rc = run_profiler_once(
            args,
            proj,
            scenario_path,
            args.scenario_name,
            out_dir,
            profiler_gradle_user_home=profiler_guh,
            console_log=console_log,
        )
        if rc != 0:
            print(
                f"  gradle-profiler 退出码 {rc}，立即退出", file=sys.stderr, flush=True
            )
            return rc

    if args.dry_run:
        print("\n(dry-run，未实际执行)", flush=True)
        return 0

    print(
        f"\n完成。成功子项约 {len(projects) - failed}/{len(projects)}；结果根目录: {results_root}",
        flush=True,
    )
    return 1 if failed else 0


def run_compare_modes(
    args: argparse.Namespace,
    projects: list[Path],
    results_root: Path,
    modes_file: Path,
    *,
    profiler_gradle_user_home: Path | None,
) -> int:
    resume_extra = ""
    if args.resume:
        resume_extra = " 已开启 **--resume**（有效 benchmark 已存在则跳过该场景，dry-run 仍打印命令）。"
    print(
        f"对比 Gradle 行为：将对 {len(projects)} 个子工程 × {len(GRADLE_MODE_SCENARIO_NAMES)} 个场景"
        f"**顺序**执行（单进程，不并行）。{resume_extra}",
        flush=True,
    )

    summary_rows: list[SummaryRow] = []
    failed = 0

    for idx, proj in enumerate(projects, start=1):
        rel = proj.name
        if not args.skip_gradlew_check and not (proj / "gradlew").is_file():
            print(
                f"[{idx}/{len(projects)}] 跳过（无 gradlew）: {proj}\n"
                f"  请在该目录执行: gradle wrapper --gradle-version <版本>",
                file=sys.stderr,
                flush=True,
            )
            failed += 1
            continue

        for scenario in GRADLE_MODE_SCENARIO_NAMES:
            out_dir = results_root / rel / scenario
            print(
                f"\n[{idx}/{len(projects)}] {rel} / 场景 {scenario}\n  → {out_dir}",
                flush=True,
            )
            skip_completed = (
                args.resume and not args.dry_run and profiler_output_has_median(out_dir)
            )
            if skip_completed:
                print(
                    "  （--resume）跳过：已有可解析的 benchmark 结果",
                    flush=True,
                )
            else:
                maybe_clean_workspace(args, proj)

                console_log = (
                    out_dir / "profiler-console.log"
                    if args.capture_profiler_console
                    else None
                )
                rc = run_profiler_once(
                    args,
                    proj,
                    modes_file,
                    scenario,
                    out_dir,
                    profiler_gradle_user_home=profiler_gradle_user_home,
                    console_log=console_log,
                )
                if rc != 0:
                    print(
                        f"  gradle-profiler 退出码 {rc}，立即退出（未写 summary）",
                        file=sys.stderr,
                        flush=True,
                    )
                    return rc

            summary_rows.append(
                build_summary_row_for_mode_scenario(rel, scenario, out_dir, args)
            )

    if args.dry_run:
        print("\n(dry-run，未写汇总表)", flush=True)
        return 0

    write_summary_tables(summary_rows, results_root)
    print(
        f"\n完成。汇总表: {results_root / 'summary.md'} 与 {results_root / 'summary.tsv'}；"
        f"异常退出约 {failed} 次（含跳过的子工程）。",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
