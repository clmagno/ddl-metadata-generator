"""Generate ingestion metadata files for every target environment from one config.

A data platform that onboards tables through metadata (rather than hand-written
DDL) needs three files per change request:

* COLUMN file - every column of every target table, per target database
* TABLE file  - format, storage location, comment, keys and CDC column per table
* VIEW file   - the consumption views that expose each table

Preparing these by hand for every environment (dev, sit, preprod, prod) is slow
and error-prone: one typo in a storage path or a column type only shows up when
the deployment fails. This tool writes all three from a single JSON config,
expands every environment, and validates the config first so mistakes are
caught before anything is generated.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

COLUMN_HEADER = [
    "TICKET_ID",
    "TGT_DB_NAME",
    "TGT_TABLE_NAME",
    "COL_SEQ_ID",
    "TGT_COL_NAME",
    "TGT_COL_DTYPE",
    "TGT_COL_LENGTH",
    "TGT_PARTITION_COL",
    "TGT_COL_DESC",
    "STATUS",
    "IS_COL_PII",
]
TABLE_HEADER = [
    "TICKET_ID",
    "TGT_DB_NAME",
    "TGT_TABLE_NAME",
    "TGT_TABLE_FORMAT",
    "TGT_TABLE_LOCATION",
    "TGT_TABLE_COMMENT",
    "SRC_TABLE_PKEY",
    "SRC_TABLE_CDC",
    "STATUS",
]
VIEW_HEADER = ["TICKET_ID", "SRC_DB_NAME", "SRC_TABLE_NAME", "TGT_DB_NAME", "TGT_VIEW_NAME", "STATUS"]

DATA_TYPES = {"String", "Integer", "BigInt", "Decimal", "Date", "Timestamp", "Boolean"}
TYPES_NEEDING_LENGTH = {"String", "Decimal"}
TABLE_FORMATS = {"parquet", "orc", "avro"}
STATUSES = {"A", "D"}  # active, deprecated


class ConfigError(ValueError):
    """The config has problems. All of them are listed in the message."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        listed = "\n  - ".join(problems)
        super().__init__(f"Config has {len(problems)} problem(s):\n  - {listed}")


@dataclass
class Column:
    name: str
    dtype: str
    length: int | None = None
    is_partition: bool = False
    description: str = ""
    status: str = "A"
    is_pii: bool = False


@dataclass
class Table:
    name: str
    database: str
    layer: str
    format: str
    comment: str
    primary_key: str | None
    cdc_column: str | None
    status: str
    columns: list[Column] = field(default_factory=list)


@dataclass
class View:
    name: str
    source_database: str
    source_table: str
    target_database: str
    status: str = "A"


@dataclass
class Config:
    ticket_id: str
    environments: list[str]
    location_template: str
    folder: str
    tables: list[Table]
    views: list[View]


def _env_db(env: str, database: str) -> str:
    return f"{env}_{database}"


def load_config(path: Path) -> Config:
    raw = json.loads(Path(path).read_text())
    problems: list[str] = []

    for key in ("ticket_id", "environments", "location_template", "folder", "tables"):
        if key not in raw:
            problems.append(f"missing top-level key '{key}'")
    if problems:
        raise ConfigError(problems)

    if not raw["environments"]:
        problems.append("'environments' is empty")
    for placeholder in ("{env}", "{table}"):
        if placeholder not in raw["location_template"]:
            problems.append(f"location_template must contain {placeholder}")

    tables: list[Table] = []
    for tname, t in raw["tables"].items():
        where = f"table '{tname}'"
        fmt = t.get("format", "parquet")
        if fmt not in TABLE_FORMATS:
            problems.append(f"{where}: format '{fmt}' not in {sorted(TABLE_FORMATS)}")
        status = t.get("status", "A")
        if status not in STATUSES:
            problems.append(f"{where}: status '{status}' not in {sorted(STATUSES)}")
        if not t.get("columns"):
            problems.append(f"{where}: has no columns")

        columns: list[Column] = []
        seen: set[str] = set()
        partition_started = False
        for i, c in enumerate(t.get("columns", []), start=1):
            cwhere = f"{where} column {i} ('{c.get('name', '?')}')"
            col = Column(
                name=c.get("name", ""),
                dtype=c.get("type", ""),
                length=c.get("length"),
                is_partition=bool(c.get("partition", False)),
                description=c.get("description", ""),
                status=c.get("status", "A"),
                is_pii=bool(c.get("pii", False)),
            )
            if not col.name:
                problems.append(f"{cwhere}: missing name")
            elif col.name.lower() in seen:
                problems.append(f"{cwhere}: duplicate column name")
            seen.add(col.name.lower())
            if col.dtype not in DATA_TYPES:
                problems.append(f"{cwhere}: type '{col.dtype}' not in {sorted(DATA_TYPES)}")
            if col.dtype in TYPES_NEEDING_LENGTH and not (isinstance(col.length, int) and col.length > 0):
                problems.append(f"{cwhere}: type {col.dtype} needs a positive integer length")
            if col.status not in STATUSES:
                problems.append(f"{cwhere}: status '{col.status}' not in {sorted(STATUSES)}")
            if col.is_partition:
                partition_started = True
            elif partition_started:
                problems.append(f"{cwhere}: partition columns must come last")
            columns.append(col)

        for key_name, key_col in (("primary_key", t.get("primary_key")), ("cdc_column", t.get("cdc_column"))):
            if key_col and key_col.lower() not in seen:
                problems.append(f"{where}: {key_name} '{key_col}' is not one of its columns")

        tables.append(
            Table(
                name=tname,
                database=t.get("database", ""),
                layer=t.get("layer", ""),
                format=fmt,
                comment=t.get("comment", ""),
                primary_key=t.get("primary_key"),
                cdc_column=t.get("cdc_column"),
                status=status,
                columns=columns,
            )
        )
        if not t.get("database"):
            problems.append(f"{where}: missing database")

    tables_by_name = {t.name: t for t in tables}
    views: list[View] = []
    for vname, v in raw.get("views", {}).items():
        source = tables_by_name.get(v.get("table", ""))
        if source is None:
            problems.append(f"view '{vname}': table '{v.get('table')}' is not defined under tables")
        if not v.get("target_database"):
            problems.append(f"view '{vname}': missing target_database")
        view = View(
            name=vname,
            source_database=source.database if source else "",
            source_table=source.name if source else "",
            target_database=v.get("target_database", ""),
            status=v.get("status", "A"),
        )
        if view.status not in STATUSES:
            problems.append(f"view '{vname}': status '{view.status}' not in {sorted(STATUSES)}")
        views.append(view)

    if problems:
        raise ConfigError(problems)

    return Config(
        ticket_id=raw["ticket_id"],
        environments=raw["environments"],
        location_template=raw["location_template"],
        folder=raw["folder"],
        tables=tables,
        views=views,
    )


def column_rows(cfg: Config) -> list[list]:
    rows = []
    for env in cfg.environments:
        for t in cfg.tables:
            for seq, c in enumerate(t.columns, start=1):
                rows.append(
                    [
                        cfg.ticket_id,
                        _env_db(env, t.database),
                        t.name,
                        seq,
                        c.name,
                        c.dtype,
                        c.length if c.length is not None else "",
                        "Y" if c.is_partition else "N",
                        c.description,
                        c.status,
                        "Y" if c.is_pii else "N",
                    ]
                )
    return rows


def table_rows(cfg: Config) -> list[list]:
    rows = []
    for env in cfg.environments:
        for t in cfg.tables:
            location = cfg.location_template.format(env=env, layer=t.layer, folder=cfg.folder, table=t.name)
            rows.append(
                [
                    cfg.ticket_id,
                    _env_db(env, t.database),
                    t.name,
                    t.format,
                    location,
                    t.comment,
                    t.primary_key or "N/A",
                    t.cdc_column or "N/A",
                    t.status,
                ]
            )
    return rows


def view_rows(cfg: Config) -> list[list]:
    rows = []
    for env in cfg.environments:
        for v in cfg.views:
            rows.append(
                [
                    cfg.ticket_id,
                    _env_db(env, v.source_database),
                    v.source_table,
                    _env_db(env, v.target_database),
                    v.name,
                    v.status,
                ]
            )
    return rows


def write_files(cfg: Config, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ticket = cfg.ticket_id.replace("-", "_")
    outputs = [
        (f"COLUMN_DETAILS_{ticket}.csv", COLUMN_HEADER, column_rows(cfg)),
        (f"TABLE_DETAILS_{ticket}.csv", TABLE_HEADER, table_rows(cfg)),
    ]
    if cfg.views:
        outputs.append((f"VIEW_DETAILS_{ticket}.csv", VIEW_HEADER, view_rows(cfg)))

    written = []
    for name, header, rows in outputs:
        path = out_dir / name
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        written.append(path)
    return written
