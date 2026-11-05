# -*- coding: utf-8 -*-
"""creem_orders 数据层测试（离线：无需 MySQL / Creem 账号）。

策略：
1. 用「假连接」捕获 MySqlTrialStore 真正执行的 SQL 与参数 → 验证语句形状、参数顺序、幂等语义
2. 把捕获到的 SQL 里的**列名**与 init_rds_mysql.sql 的 DDL 列名交叉校验
   （离线版 schema lint：能抓出「SQL 写了 DDL 里不存在的列」这类最常见的线上事故）
3. DDL 自身的索引列必须存在于同表列集合
4. 接口一致性：MySqlTrialStore 必须覆盖 BaseTrialStore 的全部公开方法
"""

import inspect
import re
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trial_db_backends import BaseTrialStore, MySqlTrialStore  # noqa: E402

DDL_PATH = ROOT / "init_rds_mysql.sql"
DDL = DDL_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------- DDL 解析
def _ddl_tables() -> dict:
    """{'creem_orders': {'id', 'dedupe_key', ...}, ...}"""
    tables = {}
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\) ENGINE", DDL, re.S):
        name, body = match.group(1), match.group(2)
        columns = set()
        for line in body.splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped or stripped.startswith(("PRIMARY KEY", "UNIQUE KEY", "KEY ", "CONSTRAINT")):
                continue
            first = stripped.split()[0]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", first):
                columns.add(first)
        tables[name] = columns
    return tables


DDL_TABLES = _ddl_tables()

# 注意：不要把 "id"/"key"/"status" 这类真实列名放进来 —— 过滤掉就等于放弃校验它们。
# 这里只放 SQL 语法词（不会被当成列名单独出现的位置）。
_SQL_KEYWORDS = {
    "select", "from", "where", "insert", "into", "update", "set", "values",
    "limit", "order", "by", "desc", "asc", "and", "or", "as", "count", "null", "default",
}


# --------------------------------------------------------------- 假连接
class _Dup(Exception):
    def __init__(self, errno=1062):
        super().__init__("Duplicate entry")
        self.errno = errno


class _Cursor:
    def __init__(self, log, rows=None, rowcount=1, error=None, error_on=""):
        self._log, self._rows, self._error = log, rows, error
        # 真实 mysql-connector 游标在 execute 后提供 .rowcount；假游标必须同样提供，
        # 否则被测代码取 rowcount 时会 AttributeError（这是假游标最易漏的属性）。
        self.rowcount = rowcount
        # error_on="" 表示所有语句都抛；给 "INSERT" 则只让 INSERT 抛错
        # ——否则前置的幂等 SELECT 也会抛，异常从 SELECT 逃出去而不是走 INSERT 的分支
        self._error_on = str(error_on or "").strip().upper()

    def execute(self, sql, args=None):
        self._log.append({"sql": " ".join(str(sql).split()), "args": args})
        if self._error and (
            not self._error_on or str(sql).strip().upper().startswith(self._error_on)
        ):
            raise self._error

    def fetchall(self):
        return list(self._rows or [])

    def fetchone(self):
        rows = self._rows or []
        return rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _store(rows=None, rowcount=1, error=None, error_on=""):
    store = MySqlTrialStore(host="h", port=3306, user="u", password="p", database="d")
    log = []
    cursor = _Cursor(log, rows=rows, rowcount=rowcount, error=error, error_on=error_on)
    store._connect = lambda: _Conn(cursor)  # type: ignore[assignment]
    return store, log


def _insert_args(log):
    """取 INSERT 语句及其参数（跳过前置的幂等 SELECT）。"""
    for entry in log:
        if entry["sql"].startswith("INSERT INTO"):
            return entry
    raise AssertionError(f"未捕获 INSERT：{[e['sql'][:40] for e in log]}")


# --------------------------------------------------------------- 插入 / 幂等
class TestInsertCreemOrder:
    RECORD = {
        "dedupe_key": "checkout.completed:ch_1",
        "event_type": "checkout.completed",
        "order_id": "ord_1",
        "checkout_id": "ch_1",
        "request_id": "sc_dev_1",
        "product_id": "prod_x",
        "customer_id": "cust_1",
        "customer_email": "buyer@example.com",
        "customer_country": "US",
        "license_key": "CREEM-ABCD-1234",
        "amount": 1990,
        "raw_event": "{}",
    }

    def test_sql_and_args_shape(self):
        store, log = _store(rows=None)
        assert store.insert_creem_order(dict(self.RECORD)) is True
        assert log[0]["sql"].startswith("SELECT")  # 先做幂等检查
        entry = _insert_args(log)
        # SQL 的列清单末尾还有 created_at/updated_at，但它们用 UTC_TIMESTAMP() 字面量，
        # 不占参数位 → 只对比前 18 列与参数个数
        sql_cols = entry["sql"].split("(", 1)[1].split(")", 1)[0].split(", ")
        n = len(MySqlTrialStore._CREEM_ORDER_COLUMNS)
        assert sql_cols[:n] == list(MySqlTrialStore._CREEM_ORDER_COLUMNS)
        assert sql_cols[n:] == ["created_at", "updated_at"]
        assert entry["sql"].startswith(f"INSERT INTO {store.creem_order_table}")
        assert len(entry["args"]) == n
        row = dict(zip(sql_cols[:n], entry["args"]))
        assert row["dedupe_key"] == "checkout.completed:ch_1"
        assert row["order_id"] == "ord_1"
        assert row["license_key"] == "CREEM-ABCD-1234"
        assert row["amount"] == 1990
        # 未提供 → 走默认值
        assert row["plan_type"] == "lifetime"
        assert row["currency"] == "USD"
        assert row["status"] == "completed"
        # id 为 uuid4
        assert uuid.UUID(row["id"]).version == 4
        assert "UTC_TIMESTAMP()" in entry["sql"]

    def test_explicit_status_and_plan_are_kept(self):
        store, log = _store(rows=None)
        record = dict(self.RECORD, status="refunded", plan_type="yearly")
        store.insert_creem_order(record)
        cols = _insert_args(log)["sql"].split("(", 1)[1].split(")", 1)[0].split(", ")
        row = dict(zip(cols, _insert_args(log)["args"]))
        assert row["status"] == "refunded" and row["plan_type"] == "yearly"

    def test_duplicate_short_circuits_without_insert(self):
        store, log = _store(rows=[{"id": "x", "dedupe_key": "checkout.completed:ch_1"}])
        assert store.insert_creem_order(dict(self.RECORD)) is False
        assert len(log) == 1 and log[0]["sql"].startswith("SELECT")

    def test_duplicate_race_errno_1062_is_idempotent(self):
        # 幂等 SELECT 说"没有"，INSERT 却撞唯一键 = 并发下别人先落单 → 必须当成功处理
        store, log = _store(rows=None, error=_Dup(1062), error_on="INSERT")
        assert store.insert_creem_order(dict(self.RECORD)) is False  # 不抛异常
        assert len(log) == 2 and log[1]["sql"].startswith("INSERT")

    def test_other_db_error_returns_false(self):
        store, _ = _store(rows=None, error=_Dup(1054), error_on="INSERT")
        assert store.insert_creem_order(dict(self.RECORD)) is False

    def test_requires_dedupe_key(self):
        store, _ = _store(rows=None)
        with pytest.raises(ValueError):
            store.insert_creem_order({"order_id": "o"})
        with pytest.raises(ValueError):
            store.insert_creem_order({"dedupe_key": "   "})

    def test_raw_event_truncated(self):
        store, log = _store(rows=None)
        store.insert_creem_order(dict(self.RECORD, raw_event="x" * 70000))
        cols = _insert_args(log)["sql"].split("(", 1)[1].split(")", 1)[0].split(", ")
        row = dict(zip(cols, _insert_args(log)["args"]))
        assert len(row["raw_event"]) == MySqlTrialStore._RAW_EVENT_MAX_CHARS


# --------------------------------------------------------------- 查询
class TestQueries:
    def test_get_by_dedupe_key(self):
        store, log = _store(rows=None)
        assert store.get_creem_order_by_dedupe_key("k") is None
        assert store.creem_order_table in log[0]["sql"] and "dedupe_key = %s" in log[0]["sql"]
        assert log[0]["args"] == ("k",)

    def test_empty_key_skips_db(self):
        store, log = _store(rows=None)
        assert store.get_creem_order_by_dedupe_key("") is None
        assert store.get_creem_order_by_license_key(None) is None
        assert log == []

    def test_get_by_license_key_uses_select_not_star(self):
        store, log = _store(rows=[{"license_key": "K"}])
        assert store.get_creem_order_by_license_key("K") == {"license_key": "K"}
        sql = log[0]["sql"]
        assert "SELECT *" not in sql
        for col in ("dedupe_key", "customer_email", "created_at", "updated_at"):
            assert col in sql

    def test_list_clamps_limit(self):
        store, log = _store(rows=[])
        store.list_creem_orders(limit=99999)
        assert "LIMIT 1000" in log[0]["sql"]
        store2, log2 = _store(rows=[])
        store2.list_creem_orders(limit=0)
        assert "LIMIT 1" in log2[0]["sql"]


# --------------------------------------------------------------- 退款 / 状态
class TestRefundAndStatus:
    def test_mark_refunded_single_key(self):
        store, log = _store(rowcount=1)
        assert store.mark_creem_order_refunded(license_key="K") is True
        sql = log[0]["sql"]
        assert sql.startswith(f"UPDATE {store.creem_order_table} SET status = 'refunded'")
        assert "refunded_at = UTC_TIMESTAMP()" in sql
        assert "license_key = %s" in sql
        assert "status <> 'refunded'" in sql
        assert log[0]["args"] == ("K",)

    def test_mark_refunded_multiple_keys_are_ored(self):
        store, log = _store(rowcount=1)
        store.mark_creem_order_refunded(dedupe_key="d", order_id="o", license_key="")
        sql = log[0]["sql"]
        assert "(dedupe_key = %s OR order_id = %s)" in sql
        assert log[0]["args"] == ("d", "o")

    def test_mark_refunded_no_key_raises(self):
        store, _ = _store(rowcount=1)
        with pytest.raises(ValueError):
            store.mark_creem_order_refunded()

    def test_mark_refunded_already_refunded_returns_false(self):
        store, _ = _store(rowcount=0)
        assert store.mark_creem_order_refunded(order_id="o") is False

    def test_update_license_status(self):
        store, log = _store(rowcount=1)
        assert store.update_license_status("K", "Revoked") is True
        sql = log[0]["sql"]
        assert sql.startswith(f"UPDATE {store.license_table} SET status = %s")
        assert log[0]["args"] == ("revoked", "K", "revoked")  # 统一小写
        store2, _ = _store(rowcount=0)
        assert store2.update_license_status("K", "active") is False

    def test_update_license_status_requires_args(self):
        store, _ = _store(rowcount=1)
        with pytest.raises(ValueError):
            store.update_license_status("", "active")
        with pytest.raises(ValueError):
            store.update_license_status("K", "")


# --------------------------------------------------------------- 表名白名单
class TestTableNameGuard:
    def test_injection_rejected(self):
        with pytest.raises(ValueError):
            MySqlTrialStore(host="h", port=1, user="u", password="p", database="d",
                            creem_order_table="creem_orders; DROP TABLE licenses")

    def test_default_table_name(self):
        store = MySqlTrialStore(host="h", port=1, user="u", password="p", database="d")
        assert store.creem_order_table == "creem_orders"


# --------------------------------------------------------------- schema lint
_NON_COLUMNS = {"UTC_TIMESTAMP", "CURRENT_TIMESTAMP", "NOW"}


def _identifiers(sql: str) -> list:
    """从 SQL 中抽出所有"应该是列名"的标识符（不含表名）。"""
    found = []
    for match in re.finditer(r"INSERT INTO \w+ \(([^)]*)\)", sql):
        found += [c.strip() for c in match.group(1).split(",")]
    for match in re.finditer(r"SELECT (.+?) FROM", sql):
        for col in match.group(1).split(","):
            found.append(col.strip().split()[0])
    for match in re.finditer(r"UPDATE \w+ SET (.+?) WHERE", sql, re.S):
        for part in match.group(1).split(","):
            left = part.split("=")[0].strip()
            if left:
                found.append(left)
    found += re.findall(r"(?:WHERE|AND|OR|\()\s*([a-z_][a-z0-9_]*)\s*=\s*%s", sql)
    out = []
    for name in found:
        name = name.strip().rstrip(")")
        # 允许大小写混合：否则 "License_Key" 这种 typo 会被静默跳过（校验形同虚设）
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name.lower() not in _SQL_KEYWORDS:
            out.append(name)
    return out


class TestSchemaLint:
    """离线 schema 校验：SQL 里用到的列必须在 DDL 里存在；DDL 索引列名必须存在。"""

    def test_ddl_has_both_tables(self):
        assert "creem_orders" in DDL_TABLES
        assert {"id", "dedupe_key", "license_key", "raw_event", "refunded_at"} <= DDL_TABLES["creem_orders"]

    def test_index_columns_exist(self):
        for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\) ENGINE", DDL, re.S):
            table, body = match.group(1), match.group(2)
            cols = DDL_TABLES[table]
            for idx in re.finditer(r"(?:UNIQUE )?KEY \w+ \(([^)]*)\)", body):
                for col in idx.group(1).split(","):
                    assert col.strip() in cols, f"{table} 索引引用了不存在的列 {col.strip()!r}"

    def test_new_methods_sql_columns_exist_in_ddl(self):
        captured = []

        # 覆盖全部新增方法，捕获真实 SQL
        for rows, rowcount in ((None, 1), ([], 1)):
            store, log = _store(rows=rows, rowcount=rowcount)
            store.insert_creem_order({"dedupe_key": "d", "license_key": "K"})
            store.get_creem_order_by_dedupe_key("d")
            store.get_creem_order_by_license_key("K")
            store.list_creem_orders(limit=5)
            store.mark_creem_order_refunded(order_id="o")
            store.update_license_status("K", "revoked")
            captured += log

        assert captured, "未捕获任何 SQL"
        checked = 0
        for entry in captured:
            sql = entry["sql"]
            table = store.creem_order_table if store.creem_order_table in sql else store.license_table
            assert table in DDL_TABLES, f"SQL 引用了未定义的表 {table}"
            cols = DDL_TABLES[table]
            for name in _identifiers(sql):
                assert name in cols, f"{table} 的 SQL 引用了 DDL 中不存在的列 {name!r} → {sql[:90]}"
                checked += 1
        assert checked >= 20, f"实际校验列数偏少（{checked}），检查解析逻辑"

    def test_lint_actually_catches_typos(self):
        """反证：lint 必须能抓出 DDL 里不存在的列名（否则这个校验是空跑）。"""
        cols = DDL_TABLES["creem_orders"]
        bad_lower = _identifiers("INSERT INTO creem_orders (id, license_kay) VALUES (%s, %s)")
        assert "license_kay" not in cols and "license_kay" in bad_lower
        bad_case = _identifiers("UPDATE creem_orders SET LicenseKey = %s WHERE id = %s")
        assert "LicenseKey" not in cols and "LicenseKey" in bad_case
        # 正确列名必须被识别（否则 lint 会说"没问题"是因为什么都没解析到）
        good = _identifiers("SELECT license_key, dedupe_key FROM creem_orders WHERE order_id = %s")
        assert {"license_key", "dedupe_key", "order_id"} <= set(good)

    def test_sql_star_never_used(self):
        store, log = _store(rows=[], rowcount=1)
        store.list_creem_orders()
        store.get_creem_order_by_license_key("K")
        assert not any("SELECT *" in e["sql"] for e in log)


# --------------------------------------------------------------- 接口一致性
class TestInterfaceConformance:
    def test_mysql_store_overrides_every_base_method(self):
        base_methods = {
            name for name, _ in inspect.getmembers(BaseTrialStore, inspect.isfunction)
            if not name.startswith("_")
        }
        assert base_methods, "BaseTrialStore 未解析到方法"
        missing = {m for m in base_methods if m not in vars(MySqlTrialStore)}
        assert not missing, f"MySqlTrialStore 未实现: {sorted(missing)}"

    def test_new_interface_methods_present(self):
        for name in (
            "insert_creem_order",
            "get_creem_order_by_dedupe_key",
            "get_creem_order_by_license_key",
            "list_creem_orders",
            "mark_creem_order_refunded",
            "update_license_status",
        ):
            assert name in vars(BaseTrialStore), f"接口缺少 {name}"
            assert name in vars(MySqlTrialStore), f"实现缺少 {name}"

    def test_env_plumbing_exists(self):
        source = (ROOT / "trial_db_backends.py").read_text(encoding="utf-8")
        assert "TRIAL_REG_MYSQL_CREEM_ORDER_TABLE" in source
