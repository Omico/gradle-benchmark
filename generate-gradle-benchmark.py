#!/usr/bin/env python3
"""
生成用于构建性能测试的多模块 Gradle 工程。

示例:
  # 默认：1000 模块 × 每模块 10 文件 × 每文件 500 行、mixed、Gradle 7.5.1、Kotlin 2.0.21、JDK 11
  python3 generate-gradle-benchmark.py

  python3 generate-gradle-benchmark.py --out ./my-bench -n 100 \\
    --module-source-mode mixed --dependency-pattern chain

  # 全维度矩阵（固定 1000 模块、每文件 500 行）：Gradle×JDK×源码类型×Kotlin（纯 Java 无 Kotlin 版本维）→ 99 个子工程
  python3 generate-gradle-benchmark.py --matrix --matrix-out ./my-matrix
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import textwrap
from pathlib import Path


DEFAULT_BENCH_DIR_PREFIX = "gradle-bench-"
REPO_DIR = Path(__file__).resolve().parent

DEFAULT_GRADLE_VERSION = "7.5.1"
DEFAULT_KOTLIN_PLUGIN_VERSION = "2.0.21"
DEFAULT_MODULES = 1000
DEFAULT_LINES_PER_FILE = 500

# --matrix：固定规模 + 全因子（纯 java-only 不展开 Kotlin 版本）
MATRIX_MODULES = 1000
MATRIX_LINES_PER_FILE_FIXED = 500
MATRIX_GRADLE_VERSIONS = ("7.5.1", "8.14.4", "9.4.1")
MATRIX_JVM_TARGETS = (
    "11",
    # "17",
    # "21",
)
MATRIX_SOURCE_MODES = ("java-only", "kotlin-only", "mixed")
MATRIX_KOTLIN_VERSIONS = (
    "2.0.21",
    # "2.1.21",
    # "2.2.21",
    # "2.3.20",
    "2.4.0-Beta1",
)


def next_seq_project_dir(parent: Path, prefix: str = DEFAULT_BENCH_DIR_PREFIX) -> Path:
    """在 parent 下扫描「prefix + 纯数字」的目录名，返回 parent / prefix + (max+1) 四位补零。"""
    parent = parent.resolve()
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    max_n = 0
    if parent.is_dir():
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            m = pat.match(child.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    nxt = max_n + 1
    return parent / f"{prefix}{nxt:04d}"


def module_name(index: int) -> str:
    return f"m{index:04d}"


def kotlin_plugin_version() -> str:
    """与 --kotlin-version 默认值一致（文档/兼容用）。"""
    return DEFAULT_KOTLIN_PLUGIN_VERSION


def gradle_version() -> str:
    """与 --gradle-version 默认值一致（文档/兼容用）。"""
    return DEFAULT_GRADLE_VERSION


def develocity_plugin_version() -> str:
    return "4.4.0"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="生成多模块 Gradle 基准工程（可指定模块数、源码文件数、行数、Java/Kotlin、模块依赖图）。"
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出目录。省略则在当前工作目录下自动创建 "
        f"{DEFAULT_BENCH_DIR_PREFIX}<序号>（序号为该目录下已有同名前缀的最大数字 + 1）",
    )
    p.add_argument(
        "--project-parent",
        type=Path,
        default=None,
        help=f"与省略 --out 联用：在此目录下创建序号目录（默认：当前工作目录）",
    )
    p.add_argument(
        "--project-prefix",
        default=DEFAULT_BENCH_DIR_PREFIX,
        help=f"自动序号目录名前缀（默认：{DEFAULT_BENCH_DIR_PREFIX}）",
    )
    p.add_argument(
        "-n",
        "--modules",
        type=int,
        default=None,
        metavar="N",
        help=f"子模块数量（默认 {DEFAULT_MODULES}；与 --matrix 互斥，勿与 --matrix 同传）。",
    )
    p.add_argument(
        "--files-per-module",
        type=int,
        default=10,
        help="每个模块生成的源码文件个数（类文件数）",
    )
    p.add_argument(
        "--lines-per-file",
        type=int,
        default=DEFAULT_LINES_PER_FILE,
        help="每个源码文件中的业务代码行数（不含 package/imports/括号等骨架）",
    )
    p.add_argument(
        "--lines-per-module",
        type=int,
        default=None,
        help="可选。若指定，则在「文件数」内按文件均分总行数（会覆盖仅由 文件数×每文件行数 决定的总行数）",
    )
    p.add_argument(
        "--module-source-mode",
        choices=("java-only", "kotlin-only", "mixed"),
        default="mixed",
        help="模块内源码类型：仅 Java / 仅 Kotlin / 混合（Java+Kotlin 各占约一半文件）",
    )
    p.add_argument(
        "--dependency-pattern",
        choices=("chain", "star", "binary-tree"),
        default="chain",
        help="模块间依赖：链式(每模块依赖前一模块) / 星形(均依赖 m0000) / 二叉树索引依赖",
    )
    p.add_argument(
        "--root-package",
        default="benchgen",
        help="生成代码的根包名（会加上模块后缀，如 benchgen.m0003）",
    )
    p.add_argument(
        "--jvm-target",
        default="11",
        help="Java/Kotlin 编译目标 JVM 版本（工具链 languageVersion）",
    )
    p.add_argument(
        "--gradle-version",
        default=DEFAULT_GRADLE_VERSION,
        help=f"Gradle Wrapper 发行版（默认 {DEFAULT_GRADLE_VERSION}）",
    )
    p.add_argument(
        "--kotlin-version",
        default=DEFAULT_KOTLIN_PLUGIN_VERSION,
        help="根工程与子模块 Kotlin JVM 插件版本（仅 kotlin-only / mixed 生效；默认 "
        f"{DEFAULT_KOTLIN_PLUGIN_VERSION}）",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="若输出目录已存在则先清空",
    )
    p.add_argument(
        "--matrix",
        action="store_true",
        help="按固定规模生成全因子矩阵：每份均为 "
        f"{MATRIX_MODULES} 模块、每文件 {MATRIX_LINES_PER_FILE_FIXED} 行；"
        f"Gradle {', '.join(MATRIX_GRADLE_VERSIONS)} × JDK {', '.join(MATRIX_JVM_TARGETS)} × "
        "源码 java-only / kotlin-only / mixed；kotlin-only 与 mixed 再 × Kotlin "
        f"{len(MATRIX_KOTLIN_VERSIONS)} 档，java-only 不展开 Kotlin 版本（共 99 份），写入 --matrix-out",
    )
    p.add_argument(
        "--matrix-out",
        type=Path,
        default=Path("gradle-bench-matrix"),
        help="--matrix 时：所有组合工程的父目录（默认 ./gradle-bench-matrix）",
    )
    p.add_argument(
        "--matrix-files-per-module",
        type=int,
        default=10,
        help="--matrix 时各组合共用的每模块文件数（默认 10，便于横向对比）",
    )
    return p.parse_args()


def lines_allocation(
    total_lines: int | None, files: int, default_per_file: int
) -> list[int]:
    if files < 1:
        raise ValueError("files-per-module 至少为 1")
    if total_lines is None:
        per = max(1, default_per_file)
        return [per] * files
    if total_lines < files:
        raise ValueError(
            "lines-per-module 不能小于 files-per-module（每文件至少 1 行有效代码）"
        )
    base, rem = divmod(total_lines, files)
    return [base + (1 if i < rem else 0) for i in range(files)]


def deps_for_index(pattern: str, index: int, n: int) -> list[str]:
    if index == 0:
        return []
    if pattern == "chain":
        return [module_name(index - 1)]
    if pattern == "star":
        return [module_name(0)]
    if pattern == "binary-tree":
        parent = (index - 1) // 2
        if parent >= 0:
            return [module_name(parent)]
        return []
    raise ValueError(pattern)


def write_if_changed(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def gen_java_class(
    package: str,
    class_name: str,
    body_lines: int,
    peer_simple_names: list[str],
) -> str:
    """peer_simple_names: 同包下其它类名，用于生成少量跨类调用。"""
    lines: list[str] = [
        f"package {package};",
        "",
        f"public final class {class_name} {{",
        f"    private {class_name}() {{}}",
        "",
        "    public static int run(int seed) {",
        "        int acc = seed;",
    ]
    for i in range(body_lines):
        lines.append(f"        acc = (acc * 1315423911 + {i}) ^ (acc >>> 7);")
    for peer in peer_simple_names[:5]:
        lines.append(f"        acc ^= {peer}.run(acc);")
    lines.extend(
        [
            "        return acc;",
            "    }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def gen_kotlin_file(
    package: str,
    class_name: str,
    body_lines: int,
    peer_simple_names: list[str],
) -> str:
    lines: list[str] = [
        f"package {package}",
        "",
        f"object {class_name} {{",
        "    @JvmStatic",
        "    fun run(seed: Int): Int {",
        "        var acc = seed",
    ]
    for i in range(body_lines):
        lines.append(f"        acc = (acc * 1315423911 + {i}) xor (acc ushr 7)")
    for peer in peer_simple_names[:5]:
        lines.append(f"        acc = acc xor {peer}.run(acc)")
    lines.extend(
        [
            "        return acc",
            "    }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def file_kind_for_slot(mode: str, slot: int, total: int) -> str:
    if mode == "java-only":
        return "java"
    if mode == "kotlin-only":
        return "kt"
    # mixed: alternate by slot
    return "java" if slot % 2 == 0 else "kt"


def root_gradle_properties() -> str:
    """根目录 gradle.properties：多模块构建并行执行（与 ./gradlew --parallel 一致）。"""
    return "org.gradle.parallel=true\n"


def root_settings_kts(names: list[str]) -> str:
    # 与 gradle-bench-matrix Kotlin 子工程 settings 一致：@file:Suppress、
    # dependencyResolutionManagement(mavenCentral)、Develocity、buildscan 空格字段
    includes = "\n".join(f'include("{n}")' for n in names)
    dv = develocity_plugin_version()
    template = """@file:Suppress("UnstableApiUsage")

import java.io.File
import java.time.Instant

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}

plugins {
    id("com.gradle.develocity") version "__DV__"
}

rootProject.name = "gradle-bench-generated"

__INCLUDES__

develocity {
    buildScan {
        uploadInBackground.set(false)
        termsOfUseUrl.set("https://gradle.com/help/legal-terms-of-use")
        termsOfUseAgree.set("yes")
    }
}
"""
    return template.replace("__DV__", dv).replace("__INCLUDES__", includes)


def root_build_kts(module_source_mode: str, kotlin_version: str | None) -> str:
    """java-only 时不应用 Kotlin 插件；kotlin-only / mixed 时在根工程声明版本供子模块使用。"""
    sub = textwrap.dedent(
        """
        subprojects {
            group = "bench.generated"
            version = "1.0"
        }
        """
    ).lstrip()
    if module_source_mode == "java-only":
        return sub
    if not kotlin_version:
        raise ValueError("kotlin-only / mixed 需要 kotlin_version")
    return textwrap.dedent(
        f"""
        plugins {{
            kotlin("jvm") version "{kotlin_version}" apply false
        }}

        {sub}
        """
    ).lstrip()


def sub_build_kts(
    _proj: str,
    mode: str,
    deps: list[str],
    jvm_target: str,
) -> str:
    """与子工程 build.gradle.kts 对照格式：plugins / java toolchain / 可选 kotlin / dependencies。"""
    impl_lines: list[str] = [f'    implementation(project(":{d}"))' for d in deps]
    if mode != "java-only":
        impl_lines.append('    implementation(kotlin("stdlib"))')
    if not impl_lines:
        dep_inner = "    // no project dependencies (root module)"
    else:
        dep_inner = "\n".join(impl_lines)

    if mode == "java-only":
        template = """plugins {
    `java-library`
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(__JVM__))
    }
}

dependencies {
__DEP__
}
"""
    else:
        template = """plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(__JVM__)
}

dependencies {
__DEP__
}
"""
    return template.replace("__JVM__", jvm_target).replace("__DEP__", dep_inner)


def gradle_wrapper_template_dir(gradle_ver: str) -> Path | None:
    """若存在 `templates/gradle-<版本>` 且含 gradlew，则返回该目录。"""
    d = REPO_DIR / "templates" / f"gradle-{gradle_ver}"
    if d.is_dir() and (d / "gradlew").is_file():
        return d
    return None


def copy_gradle_wrapper_template(template_root: Path, dest_root: Path) -> None:
    """将模板目录下所有文件复制到工程根（保留 gradle/wrapper 等相对路径）。"""
    for src in template_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(template_root)
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def write_gradle_wrapper_properties(root: Path, gradle_ver: str) -> None:
    dist = f"https\\://services.gradle.org/distributions/gradle-{gradle_ver}-bin.zip"
    props = textwrap.dedent(
        f"""
        distributionBase=GRADLE_USER_HOME
        distributionPath=wrapper/dists
        distributionUrl={dist}
        networkTimeout=10000
        validateDistributionUrl=true
        zipStoreBase=GRADLE_USER_HOME
        zipStorePath=wrapper/dists
        """
    ).lstrip()
    write_if_changed(root / "gradle" / "wrapper" / "gradle-wrapper.properties", props)


def install_gradle_wrapper(out: Path, gradle_ver: str) -> bool:
    """安装 Wrapper：优先复制同版本仓库模板，否则仅写 gradle-wrapper.properties。
    返回是否已从模板复制完整 Wrapper。"""
    tpl = gradle_wrapper_template_dir(gradle_ver)
    if tpl is not None:
        copy_gradle_wrapper_template(tpl, out)
        return True
    write_gradle_wrapper_properties(out, gradle_ver)
    return False


def matrix_cell_dir_name(
    gradle_ver: str, jvm_target: str, source_mode: str, kotlin_ver: str | None
) -> str:
    g = gradle_ver.replace("/", "_")
    j = jvm_target.replace("/", "_")
    if source_mode == "java-only":
        return f"gradle-{g}-jdk{j}-java"
    kv = (kotlin_ver or "").replace("/", "_")
    if source_mode == "kotlin-only":
        return f"gradle-{g}-jdk{j}-kotlin-{kv}"
    return f"gradle-{g}-jdk{j}-mixed-{kv}"


def generate_benchmark_project(
    out: Path,
    *,
    modules: int,
    files_per_module: int,
    lines_per_file: int,
    lines_per_module: int | None,
    module_source_mode: str,
    dependency_pattern: str,
    root_package: str,
    jvm_target: str,
    gradle_ver: str,
    kotlin_ver: str | None,
) -> bool:
    """在已存在的 `out` 目录中写入完整工程（不创建/删除父路径）。
    kotlin_ver 在 java-only 时应为 None；kotlin-only / mixed 时必须提供。
    返回 True 表示已从本仓库 templates/gradle-* 复制完整 Wrapper。"""
    if module_source_mode == "java-only":
        root_kv: str | None = None
    else:
        if not kotlin_ver:
            raise ValueError("kotlin-only / mixed 需要 kotlin_ver")
        root_kv = kotlin_ver
    n = modules
    names = [module_name(i) for i in range(n)]
    alloc = lines_allocation(lines_per_module, files_per_module, lines_per_file)

    write_if_changed(out / "settings.gradle.kts", root_settings_kts(names))
    write_if_changed(
        out / "build.gradle.kts", root_build_kts(module_source_mode, root_kv)
    )
    write_if_changed(out / "gradle.properties", root_gradle_properties())
    wrapper_bundled = install_gradle_wrapper(out, gradle_ver)

    for i, proj in enumerate(names):
        pkg = f"{root_package}.{proj}"
        deps = deps_for_index(dependency_pattern, i, n)
        sub_dir = out / proj
        write_if_changed(
            sub_dir / "build.gradle.kts",
            sub_build_kts(proj, module_source_mode, deps, jvm_target),
        )

        java_files: list[tuple[str, Path, str]] = []
        kt_files: list[tuple[str, Path, str]] = []

        class_names: list[str] = [f"Gen{j:04d}" for j in range(files_per_module)]

        for j in range(files_per_module):
            kind = file_kind_for_slot(module_source_mode, j, files_per_module)
            class_name = class_names[j]
            peers = [class_names[k] for k in range(files_per_module) if k != j]

            body_lines = alloc[j]
            if kind == "java":
                rel = (
                    Path("src/main/java") / Path(*pkg.split(".")) / f"{class_name}.java"
                )
                content = gen_java_class(pkg, class_name, body_lines, peers)
                java_files.append((class_name, rel, content))
            else:
                rel = (
                    Path("src/main/kotlin") / Path(*pkg.split(".")) / f"{class_name}.kt"
                )
                content = gen_kotlin_file(pkg, class_name, body_lines, peers)
                kt_files.append((class_name, rel, content))

        for _, rel, content in java_files + kt_files:
            write_if_changed(sub_dir / rel, content)

    return wrapper_bundled


def prepare_output_dir(out: Path, force: bool) -> int | None:
    """若无法准备目录则返回退出码，成功返回 None。"""
    if out.exists():
        if not force:
            print(f"输出目录已存在: {out}（使用 --force 覆盖）", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    out.mkdir(parents=True)
    return None


def run_matrix(args: argparse.Namespace) -> int:
    if args.modules is not None:
        print("--matrix 与 -n/--modules 不能同时使用", file=sys.stderr)
        return 2
    if args.out is not None:
        print(
            "--matrix 时不要使用 --out；请用 --matrix-out 指定父目录", file=sys.stderr
        )
        return 2

    root = args.matrix_out.resolve()
    err = prepare_output_dir(root, args.force)
    if err is not None:
        return err

    combos: list[tuple[str, str, str, str | None]] = []
    for gv in MATRIX_GRADLE_VERSIONS:
        for jvm in MATRIX_JVM_TARGETS:
            for mode in MATRIX_SOURCE_MODES:
                if mode == "java-only":
                    combos.append((gv, jvm, mode, None))
                else:
                    for kv in MATRIX_KOTLIN_VERSIONS:
                        combos.append((gv, jvm, mode, kv))

    manifest_lines = [
        "# gradle_version × jdk × source_mode × kotlin_version → subdir",
        f"# modules (fixed) = {MATRIX_MODULES}",
        f"# lines_per_file (fixed) = {MATRIX_LINES_PER_FILE_FIXED}",
        f"# files_per_module = {args.matrix_files_per_module}",
        f"# dependency_pattern = {args.dependency_pattern}",
        "",
    ]

    for gv, jvm, mode, kv in combos:
        cell = root / matrix_cell_dir_name(gv, jvm, mode, kv)
        if cell.exists():
            shutil.rmtree(cell)
        cell.mkdir(parents=True)
        generate_benchmark_project(
            cell,
            modules=MATRIX_MODULES,
            files_per_module=args.matrix_files_per_module,
            lines_per_file=MATRIX_LINES_PER_FILE_FIXED,
            lines_per_module=None,
            module_source_mode=mode,
            dependency_pattern=args.dependency_pattern,
            root_package=args.root_package,
            jvm_target=jvm,
            gradle_ver=gv,
            kotlin_ver=kv,
        )
        rel = cell.name
        kv_col = kv or "-"
        manifest_lines.append(f"{gv}\t{jvm}\t{mode}\t{kv_col}\t{rel}")
        print(
            f"已生成 {rel}（Gradle {gv}, JDK {jvm}, {mode}"
            + ("" if kv is None else f", Kotlin {kv}")
            + f", {MATRIX_MODULES} 模块）"
        )

    write_if_changed(root / "MATRIX-MANIFEST.txt", "\n".join(manifest_lines) + "\n")
    print(f"\n矩阵共 {len(combos)} 份工程 -> {root}（见 MATRIX-MANIFEST.txt）")
    if any(gradle_wrapper_template_dir(gv) for gv in MATRIX_GRADLE_VERSIONS):
        print(
            "各子目录已含 Gradle Wrapper（来自本仓库 templates），可直接 ./gradlew 构建。"
        )
    else:
        print("各子目录内可单独安装 Wrapper 并执行构建。")
    return 0


def main() -> int:
    args = parse_args()
    if args.matrix:
        return run_matrix(args)

    n = args.modules if args.modules is not None else DEFAULT_MODULES
    if n < 1:
        print("modules 至少为 1", file=sys.stderr)
        return 2

    if args.out is not None:
        out = args.out.resolve()
    else:
        base = (args.project_parent or Path.cwd()).resolve()
        out = next_seq_project_dir(base, args.project_prefix)

    err = prepare_output_dir(out, args.force)
    if err is not None:
        return err

    gver = args.gradle_version.strip()
    if not gver:
        print("--gradle-version 不能为空", file=sys.stderr)
        return 2
    kver: str | None = None
    if args.module_source_mode != "java-only":
        kver = args.kotlin_version.strip()
        if not kver:
            print("--kotlin-version 不能为空（kotlin-only / mixed）", file=sys.stderr)
            return 2

    bundled = generate_benchmark_project(
        out,
        modules=n,
        files_per_module=args.files_per_module,
        lines_per_file=args.lines_per_file,
        lines_per_module=args.lines_per_module,
        module_source_mode=args.module_source_mode,
        dependency_pattern=args.dependency_pattern,
        root_package=args.root_package,
        jvm_target=args.jvm_target,
        gradle_ver=gver,
        kotlin_ver=kver,
    )

    print(
        f"已生成 {n} 个模块 -> {out}（Gradle {gver}"
        + (f", Kotlin {kver}" if kver else "")
        + ")"
    )
    if bundled:
        print(
            "下一步: cd 到输出目录并执行构建（如 ./gradlew compileJava compileKotlin）。"
        )
    else:
        print(
            f"下一步: cd 到输出目录并执行 gradle wrapper --gradle-version {gver} "
            "生成 gradlew，然后执行构建（如 ./gradlew compileJava compileKotlin）。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
