# back_end/python_scripts/migrate_database.py
"""
数据库迁移脚本

运行方式:
    cd back_end && uv run python python_scripts/migrate_database.py
    cd back_end && uv run python python_scripts/migrate_database.py --develop
"""
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import load_from_yaml



def _create_table(cursor: sqlite3.Cursor, ddl: str, table: str):
    """创建表（IF NOT EXISTS），幂等。"""
    cursor.execute(ddl)
    print(f"✅ [{table}] 表已就绪。")


def _create_index(cursor: sqlite3.Cursor, index_name: str, table: str, column: str):
    """创建索引（IF NOT EXISTS），幂等。"""
    cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column});")
    print(f"✅ [{table}] 索引 {index_name} 已就绪。")


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

    # === CachedImages 表 ===
    _create_table(cursor, """
        CREATE TABLE IF NOT EXISTS cached_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uri TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'cached',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """, "cached_images")

    # === Skills 表 ===
    _create_table(cursor, """
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """, "skills")

    # === SkillFiles 表 ===
    _create_table(cursor, """
        CREATE TABLE IF NOT EXISTS skill_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_id TEXT NOT NULL REFERENCES skills(id),
            path TEXT NOT NULL,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """, "skill_files")
    _create_index(cursor, "ix_skill_files_skill_id", "skill_files", "skill_id")

    conn.commit()
    conn.close()
    print("\n🎉 迁移完成。")


if __name__ == "__main__":
    develop = "--develop" in sys.argv
    migrate(develop=develop)
