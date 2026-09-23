#!/usr/bin/env python3
# ============================================================
# user_manager.py —— 多用户管理（FR-3.0.11 + FR-3.0.12）
#   kb user add <name> [--role editor] [--display "显示名"]
#   kb user list
#   kb user remove <name>
#   kb user inbox <name>     # 显示/创建用户专属收件箱
# 角色：admin / editor / reader
# ============================================================
import argparse, sys, json, os
from pathlib import Path
import datetime

try:
    from kb_common import ROOT_DEFAULT
except ImportError:
    ROOT_DEFAULT = os.environ.get("KB_ROOT", ".")

USERS_PATH = "reference/users.json"
ROLES = ("admin", "editor", "reader")
ROLE_PERMS = {
    "admin":  {"ingest", "query", "edit", "archive", "manage_users", "config"},
    "editor": {"ingest", "query", "edit"},
    "reader": {"query"},
}
INBOX_BASE = "00-收件箱 Inbox"


def _users_path(root):
    return Path(root) / USERS_PATH


def load_users(root):
    p = _users_path(root)
    if not p.exists():
        return {"users": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data


def save_users(root, data):
    p = _users_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_add(args):
    root = Path(args.root)
    data = load_users(root)
    users = data.setdefault("users", {})
    name = args.name
    if name in users:
        print(f"✗ 用户已存在: {name}")
        return 1
    role = args.role or "editor"
    if role not in ROLES:
        print(f"✗ 未知角色: {role}（可选: {', '.join(ROLES)}）")
        return 1
    today = datetime.date.today().strftime("%F")
    inbox = f"{INBOX_BASE}/{name}" if name != "default" else INBOX_BASE
    users[name] = {
        "role": role,
        "display_name": args.display or name,
        "created": today,
        "inbox": inbox,
    }
    # 创建用户收件箱目录
    inbox_path = Path(root) / inbox
    inbox_path.mkdir(parents=True, exist_ok=True)
    save_users(root, data)
    print(f"✅ 用户已添加: {name} (role={role}, inbox={inbox})")
    return 0


def cmd_list(args):
    root = Path(args.root)
    data = load_users(root)
    users = data.get("users", {})
    if not users:
        print("（无用户）")
        return 0
    print(f"👥 注册用户（{len(users)}）:")
    for name, info in sorted(users.items()):
        perms = ROLE_PERMS.get(info.get("role", "reader"), set())
        print(f"  {name:15s}  role={info.get('role', '?'):8s}  display={info.get('display_name', name):10s}  perms={len(perms)}  inbox={info.get('inbox', '-')}")
    return 0


def cmd_remove(args):
    root = Path(args.root)
    data = load_users(root)
    users = data.get("users", {})
    name = args.name
    if name not in users:
        print(f"✗ 用户不存在: {name}")
        return 1
    if name == "default":
        print("✗ 不能删除 default 用户")
        return 1
    del users[name]
    save_users(root, data)
    print(f"✅ 用户已删除: {name}")
    return 0


def cmd_inbox(args):
    root = Path(args.root)
    data = load_users(root)
    users = data.get("users", {})
    name = args.name
    if name not in users:
        print(f"✗ 用户不存在: {name}")
        return 1
    inbox = users[name].get("inbox", f"{INBOX_BASE}/{name}")
    inbox_path = Path(root) / inbox
    inbox_path.mkdir(parents=True, exist_ok=True)
    files = sorted(inbox_path.glob("*.md")) if inbox_path.exists() else []
    print(f"📥 {name} 的收件箱: {inbox}")
    print(f"   待处理: {len(files)} 篇")
    for f in files[:10]:
        print(f"   - {f.name}")
    return 0


def cmd_check_perm(args):
    """检查用户是否有某权限"""
    root = Path(args.root)
    data = load_users(root)
    users = data.get("users", {})
    name = args.name
    if name not in users:
        print(f"✗ 用户不存在: {name}")
        return 1
    role = users[name].get("role", "reader")
    perms = ROLE_PERMS.get(role, set())
    has = args.perm in perms
    print(f"{'✅' if has else '✗'} {name} (role={role})  perm={args.perm}  → {'有' if has else '无'}权限")
    return 0 if has else 1


def main():
    ap = argparse.ArgumentParser(description="多用户管理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # add
    a = sub.add_parser("add", help="添加用户")
    a.add_argument("name", help="用户名")
    a.add_argument("--root", default=ROOT_DEFAULT)
    a.add_argument("--role", default="editor", choices=ROLES)
    a.add_argument("--display", default=None, help="显示名")
    a.set_defaults(func=cmd_add)
    # list
    l = sub.add_parser("list", help="列出所有用户")
    l.add_argument("--root", default=ROOT_DEFAULT)
    l.set_defaults(func=cmd_list)
    # remove
    rm = sub.add_parser("remove", help="删除用户")
    rm.add_argument("name", help="用户名")
    rm.add_argument("--root", default=ROOT_DEFAULT)
    rm.set_defaults(func=cmd_remove)
    # inbox
    ib = sub.add_parser("inbox", help="查看用户收件箱")
    ib.add_argument("name", help="用户名")
    ib.add_argument("--root", default=ROOT_DEFAULT)
    ib.set_defaults(func=cmd_inbox)
    # check-perm
    cp = sub.add_parser("check-perm", help="检查权限")
    cp.add_argument("name", help="用户名")
    cp.add_argument("perm", help="权限名 (ingest/query/edit/archive/manage_users/config)")
    cp.add_argument("--root", default=ROOT_DEFAULT)
    cp.set_defaults(func=cmd_check_perm)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
