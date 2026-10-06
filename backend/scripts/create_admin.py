"""管理后台账号创建/重置（文档 9.5）。

用法（在 backend 目录下）：
    python scripts/create_admin.py --username admin --password '你的口令'
    python scripts/create_admin.py --username op1 --password 'xxx' --role operator
    python scripts/create_admin.py --list

首次部署也可直接设 ADMIN_USERNAME / ADMIN_PASSWORD 环境变量，API 启动时自动初始化超管。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.db.models import AdminUser  # noqa: E402
from app.services.admin_auth import set_admin_password  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AlleyBite 管理后台账号管理")
    parser.add_argument("--username", default=None, help="账号名")
    parser.add_argument("--password", default=None, help="口令（明文，仅本地执行，不落日志）")
    parser.add_argument(
        "--role",
        choices=["superadmin", "operator", "reviewer"],
        default="operator",
        help="角色（RBAC 细分属 M3/M4）",
    )
    parser.add_argument("--list", action="store_true", help="列出已有账号")
    args = parser.parse_args(argv)

    settings = get_settings()
    init_db()

    with SessionLocal() as session:
        if args.list:
            rows = session.scalars(select(AdminUser).order_by(AdminUser.id)).all()
            for row in rows:
                state = "启用" if row.is_active else "停用"
                print(f"#{row.id} {row.username:<16} {row.role:<10} {state}")
            if not rows:
                print("（暂无账号）")
            return 0

        if not args.username or not args.password:
            parser.error("需同时提供 --username 与 --password（或用 --list）")
        user = set_admin_password(
            session,
            args.username,
            args.password,
            settings=settings,
            role=args.role,
        )
        print(f"账号 {user.username} 已就绪（角色 {user.role}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())