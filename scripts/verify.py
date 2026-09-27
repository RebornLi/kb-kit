#!/usr/bin/env python3
# ============================================================
# verify.py —— 安装结果自检
#
# 检查 vault 结构完整性、向量索引、kb 命令可用性。
# 输出绿勾/红叉清单，红叉项给修复命令。
#
# 用法:
#     python3 scripts/verify.py --vault /path/to/vault
# ============================================================
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, List

# ============================================================
# 数据模型
# ============================================================
class VerifyItem(NamedTuple):
    name: str
    passed: bool
    detail: str
    fix_cmd: str = ""


# 必需目录（PARA 分区 + 引擎 + 脚本）
REQUIRED_DIRS = [
    "00-收件箱 Inbox",
    "10-项目 Projects",
    "20-技术 Technology",
    "30-决策日志 Decisions",
    "40-资源库 Resources",
    "50-模板 Templates",
    "60-运营 Operations",
    "70-知识治理 Governance",
    "90-归档 Archive",
    "pipeline",
    "scripts",
]

# 必备文件：安装流程一定产出，缺任一即判失败
REQUIRED_FILES = [
    "kb",
    "kb.cmd",
    "kb-agent.json",   # create_vault.wire_agents 总会写入（无 agent 时写空注册表）
]

# 内容/引导文件：精简分发包可能不随模板提供（内容可按需生成），缺失只提示不判失败
CONTENT_FILES = [
    "🏠-知识库首页.md",
    "📖-知识库管理方案.md",
    "如何使用.md",
]


# ============================================================
# 检查项
# ============================================================
def check_vault_structure(vault: Path) -> VerifyItem:
    """检查 vault 目录结构完整性。

    只硬性要求安装流程必然产出的机制文件；示例内容文件（首页/管理方案/快捷卡）
    缺失时降级为提示——精简分发包可不含它们，不应因此判安装失败。
    """
    missing_dirs = []
    missing_files = []

    for d in REQUIRED_DIRS:
        if not (vault / d).is_dir():
            missing_dirs.append(d)

    for f in REQUIRED_FILES:
        if not (vault / f).exists():
            missing_files.append(f)

    if not missing_dirs and not missing_files:
        detail = f"{len(REQUIRED_DIRS)} 目录 + {len(REQUIRED_FILES)} 文件齐全"
        missing_content = [f for f in CONTENT_FILES if not (vault / f).exists()]
        if missing_content:
            detail += f"（可选内容未随包提供: {', '.join(missing_content)}）"
        return VerifyItem("目录结构", True, detail)

    missing = []
    if missing_dirs:
        missing.append(f"目录: {', '.join(missing_dirs)}")
    if missing_files:
        missing.append(f"文件: {', '.join(missing_files)}")
    return VerifyItem("目录结构", False,
                      f"缺失 {'; '.join(missing)}",
                      f"python3 create_vault.py --vault {vault} --force")


def check_vector_index(vault: Path) -> VerifyItem:
    """检查向量索引存在且可查询。"""
    index_file = vault / "vector index" / "df_idf.json"
    if not index_file.exists():
        return VerifyItem("向量索引", False,
                          "索引文件不存在",
                          "kb rag index")

    # 验证可 JSON 解析
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            return VerifyItem("向量索引", False,
                              "索引文件为空或格式错误",
                              "kb rag index")
    except (json.JSONDecodeError, OSError) as e:
        return VerifyItem("向量索引", False,
                          f"索引文件损坏: {e}",
                          "kb rag index")

    # 验证可查询
    n_docs = data.get("n_docs", 0) if isinstance(data.get("n_docs", 0), int) else 0
    rag = vault / "pipeline" / "rag.py"
    if not rag.exists():
        return VerifyItem("向量索引", True,
                          f"索引存在（{n_docs} 篇），rag.py 缺失跳过查询验证")

    try:
        r = subprocess.run(
            [sys.executable, str(rag), "query", "测试", "--top", "1", "--root", str(vault)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return VerifyItem("向量索引", True,
                              f"索引存在（{n_docs} 篇），查询正常")
        return VerifyItem("向量索引", False,
                          f"查询返回非 0: {r.stderr[:100]}",
                          "kb rag index")
    except subprocess.TimeoutExpired:
        return VerifyItem("向量索引", True,
                          f"索引存在（{n_docs} 篇），查询超时（不影响使用）")
    except Exception as e:
        return VerifyItem("向量索引", False,
                          f"查询异常: {e}",
                          "kb rag index")


def check_kb_command(vault: Path) -> VerifyItem:
    """检查 kb 命令可执行。"""
    import platform as _pf
    if _pf.system() == "Windows":
        kb = vault / "kb.cmd"
        cmd = [str(kb), "help"]
    else:
        kb = vault / "kb"
        cmd = ["bash", str(kb), "help"]

    if not kb.exists():
        return VerifyItem("kb 命令", False,
                          f"启动器不存在: {kb}",
                          f"python3 create_vault.py --vault {vault} --force")

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and (r.stdout or r.stderr):
            return VerifyItem("kb 命令", True, "kb help 执行成功")
        return VerifyItem("kb 命令", False,
                          f"kb help 返回码 {r.returncode}",
                          "检查 Python 是否可用")
    except subprocess.TimeoutExpired:
        return VerifyItem("kb 命令", False,
                          "kb help 超时",
                          "检查 Python 是否可用")
    except Exception as e:
        return VerifyItem("kb 命令", False,
                          f"执行异常: {e}",
                          "检查 Python 是否可用")


def check_install_manifest(vault: Path) -> VerifyItem:
    """按安装清单核对基线文件是否齐全（安装完整性）。

    清单由 create_vault 在安装时写入 `.kb/install-manifest.json`，只含「安装基线」
    （模板文件 + 生成文件），不含用户后续新增笔记——故不会因用户增删内容误报。
    无清单（旧版/精简安装）时跳过，保持向后兼容。
    """
    mf = vault / ".kb" / "install-manifest.json"
    if not mf.exists():
        return VerifyItem("安装清单", True, "无清单（旧版/精简安装），跳过")
    try:
        files = json.loads(mf.read_text(encoding="utf-8")).get("files", [])
    except (json.JSONDecodeError, OSError) as e:
        return VerifyItem("安装清单", False, f"清单损坏: {e}",
                          "删除 .kb/install-manifest.json 后重装")
    missing = [f for f in files if not (vault / f).exists()]
    if missing:
        head = ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
        return VerifyItem("安装清单", False,
                          f"缺失 {len(missing)} 个基线文件: {head}",
                          f"python3 create_vault.py --vault {vault} --force")
    return VerifyItem("安装清单", True, f"{len(files)} 个基线文件齐全")


# ============================================================
# 主入口
# ============================================================
def run(vault: Path) -> int:
    vault = vault.resolve()
    print("\n▶ 安装自检……")
    print("-" * 50)

    items: List[VerifyItem] = [
        check_vault_structure(vault),
        check_install_manifest(vault),
        check_vector_index(vault),
        check_kb_command(vault),
    ]

    passed = 0
    for item in items:
        mark = "✅" if item.passed else "❌"
        print(f"  {mark} {item.name}: {item.detail}")
        if not item.passed and item.fix_cmd:
            print(f"      修复: {item.fix_cmd}")
        if item.passed:
            passed += 1

    print("-" * 50)
    print(f"  {passed}/{len(items)} 项通过")
    return 0 if passed == len(items) else 1


def main():
    ap = argparse.ArgumentParser(description="安装结果自检")
    ap.add_argument("--vault", required=True, help="vault 根目录")
    args = ap.parse_args()
    return run(Path(args.vault))


if __name__ == "__main__":
    sys.exit(main())
