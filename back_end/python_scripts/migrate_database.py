# back_end/python_scripts/migrate_database.py
"""
数据库迁移脚本 - 容器镜像支持

为 jobs 和 services 表添加 container_image 和 system_entry_command 字段。

运行方式:
    cd back_end && uv run python python_scripts/migrate_database.py
    cd back_end && uv run python python_scripts/migrate_database.py --develop
"""
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import load_from_yaml


def _add_column(cursor: sqlite3.Cursor, table: str, column: str, col_type: str = "TEXT"):
    """尝试添加列，如果已存在则跳过。"""
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
        print(f"✅ [{table}] {column} 列已添加。")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print(f"⏭️  [{table}] {column} 列已存在，跳过。")
        else:
            raise


def migrate(develop: bool = False):
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "configs", "magnus_config.yaml"
    )
    config = load_from_yaml(config_path)

    root_path = config["server"]["root"]
    if develop:
        root_path += "-develop"

    db_path = os.path.join(root_path, "database", "magnus.db")

    print(f"📂 目标数据库: {db_path}")

    if not os.path.exists(db_path):
        print("❌ 数据库文件不存在！请先运行 Server 生成数据库。")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # === Jobs 表 ===
    _add_column(cursor, "jobs", "container_image")
    _add_column(cursor, "jobs", "system_entry_command")

    # 填充 jobs.container_image NULL 值为默认值
    default_image = config["cluster"]["default_container_image"]
    cursor.execute(
        "UPDATE jobs SET container_image = ? WHERE container_image IS NULL;",
        (default_image,)
    )
    print(f"✅ [jobs] 已更新 {cursor.rowcount} 条记录的 container_image。")

    # === Services 表 ===
    _add_column(cursor, "services", "container_image")
    _add_column(cursor, "services", "system_entry_command")

    # services.container_image 保持 NULL（可选字段，使用时 fallback 到默认值）

    conn.commit()
    conn.close()
    print("\n🎉 迁移完成。")


if __name__ == "__main__":
    develop = "--develop" in sys.argv
    migrate(develop=develop)
