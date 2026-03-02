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


def _add_column(cursor: sqlite3.Cursor, table: str, column: str, col_type: str, default: str = ""):
    """幂等地添加列。如果列已存在则跳过。"""
    cursor.execute(f"PRAGMA table_info({table});")
    existing = {row[1] for row in cursor.fetchall()}
    if column in existing:
        print(f"⏭️  [{table}.{column}] 列已存在，跳过。")
        return
    default_clause = f" DEFAULT {default}" if default else ""
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause};")
    print(f"✅ [{table}.{column}] 列已添加。")


def _make_column_nullable(cursor: sqlite3.Cursor, table: str, column: str):
    """将 NOT NULL 列改为 nullable（SQLite 不支持 ALTER COLUMN，需重建表）。"""
    cursor.execute(f"PRAGMA table_info({table});")
    columns = cursor.fetchall()
    col_info = None
    for c in columns:
        if c[1] == column:
            col_info = c
            break
    if col_info is None:
        print(f"⏭️  [{table}.{column}] 列不存在，跳过。")
        return
    # notnull = c[3]: 0 means nullable, 1 means NOT NULL
    if col_info[3] == 0:
        print(f"⏭️  [{table}.{column}] 已是 nullable，跳过。")
        return

    # 重建表：收集所有列定义
    col_defs = []
    col_names = []
    for c in columns:
        cid, cname, ctype, notnull, dflt, pk = c
        col_names.append(cname)
        parts = [cname, ctype or "TEXT"]
        if pk:
            parts.append("PRIMARY KEY")
        if notnull and cname != column:
            parts.append("NOT NULL")
        if dflt is not None:
            parts.append(f"DEFAULT {dflt}")
        col_defs.append(" ".join(parts))

    tmp = f"_{table}_migrate_tmp"
    cols_csv = ", ".join(col_names)
    cursor.execute(f"CREATE TABLE {tmp} ({', '.join(col_defs)});")
    cursor.execute(f"INSERT INTO {tmp} ({cols_csv}) SELECT {cols_csv} FROM {table};")
    cursor.execute(f"DROP TABLE {table};")
    cursor.execute(f"ALTER TABLE {tmp} RENAME TO {table};")
    print(f"✅ [{table}.{column}] 已改为 nullable。")


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

    # === Users 表扩展：Agent 支持 ===
    _add_column(cursor, "users", "user_type", "TEXT NOT NULL", "'human'")
    _add_column(cursor, "users", "parent_id", "TEXT REFERENCES users(id)")
    _add_column(cursor, "users", "headcount", "INTEGER")

    # feishu_open_id 需要 nullable（Agent 没有飞书 ID）
    _make_column_nullable(cursor, "users", "feishu_open_id")

    conn.commit()
    conn.close()
    print("\n🎉 迁移完成。")


if __name__ == "__main__":
    develop = "--develop" in sys.argv
    migrate(develop=develop)
