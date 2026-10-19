# DDL Metadata Generator

![ci](https://github.com/clmagno/ddl-metadata-generator/actions/workflows/ci.yml/badge.svg)

Generates the metadata files a metadata-driven data platform needs to onboard new
tables (**column**, **table** and **view** definitions) for every environment, from one
validated JSON config.

## Why

On platforms where ingestion is driven by metadata rather than hand-written DDL, every
change request needs the same three files, repeated for each environment (dev, SIT,
pre-prod, prod). Prepared by hand, that is hours of copy-and-edit per ticket, and a
typo in a column type or storage path only shows up when the deployment fails.

This tool writes all of it from a single config:

- every column of every table, with sequence, type, length, partition flag, description and PII flag;
- every table's format, storage location, comment, primary key and CDC column;
- the consumption views over those tables;

each expanded across all environments, so one config produces the full set consistently.

I first built this as a script at work to replace manual metadata preparation for Hive/Impala
ingestion. This repository is a clean rewrite with an invented example domain (no client
data), config validation and tests.

## Validation

The config is checked before anything is written, and **every** problem is reported in one go:

- unknown data types, missing lengths for `String`/`Decimal`, duplicate column names;
- partition columns not placed last;
- primary key or CDC column not among the table's columns;
- unknown table format or status;
- views that reference a table not defined in the config;
- a location template missing `{env}` or `{table}`.

```text
$ python -m metagen bad_config.json
Config has 2 problem(s):
  - table 'orders_daily': format 'csv' not in ['avro', 'orc', 'parquet']
  - table 'customers_daily' column 1 ('customer_id'): type 'Varchar' not in [...]
```

## Usage

```bash
pip install -e ".[dev]"
python -m metagen examples/retail_orders.json --out output
```

```text
wrote output/COLUMN_DETAILS_DATA_1042.csv     # 13 columns x 4 environments = 52 rows
wrote output/TABLE_DETAILS_DATA_1042.csv      # 2 tables x 4 environments = 8 rows
wrote output/VIEW_DETAILS_DATA_1042.csv       # 2 views x 4 environments = 8 rows
```

Sample of `TABLE_DETAILS_DATA_1042.csv`:

| TICKET_ID | TGT_DB_NAME | TGT_TABLE_NAME | TGT_TABLE_FORMAT | TGT_TABLE_LOCATION | SRC_TABLE_PKEY | SRC_TABLE_CDC |
|---|---|---|---|---|---|---|
| DATA-1042 | dev_retail_cnsm | orders_daily | parquet | /data/dev/consumption/analytics/retail_orders/orders_daily | order_id | updated_at |
| DATA-1042 | prd_retail_cnsm | orders_daily | parquet | /data/prd/consumption/analytics/retail_orders/orders_daily | order_id | updated_at |

## Config format

See [`examples/retail_orders.json`](examples/retail_orders.json). In short:

```json
{
  "ticket_id": "DATA-1042",
  "environments": ["dev", "sit", "ppt", "prd"],
  "location_template": "/data/{env}/consumption/{layer}/{folder}/{table}",
  "folder": "retail_orders",
  "tables": {
    "orders_daily": {
      "database": "retail_cnsm", "layer": "analytics", "format": "parquet",
      "primary_key": "order_id", "cdc_column": "updated_at",
      "columns": [
        {"name": "order_id", "type": "BigInt", "description": "Order identifier"},
        {"name": "load_date", "type": "String", "length": 10, "partition": true}
      ]
    }
  },
  "views": {"v_orders_daily": {"table": "orders_daily", "target_database": "retail_view"}}
}
```

Databases are prefixed with the environment (`dev_retail_cnsm`), and a column can be
flagged `"pii": true` so downstream masking can be applied.

## Tests

```bash
pytest -q
```

15 tests cover the row counts per environment, column sequencing, location expansion,
view-to-table environment consistency, PII flags and each validation rule.

---
Built by [Christopher Lloyd Magno](https://www.linkedin.com/in/clmagno/), data engineer.
