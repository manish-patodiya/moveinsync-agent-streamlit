from __future__ import annotations

import duckdb
import pandas as pd


def create_memory_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def load_dataframe(con: duckdb.DuckDBPyConnection, table_name: str, df: pd.DataFrame) -> None:
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.register("_ingest_df", df)
    con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _ingest_df")
    con.unregister("_ingest_df")
