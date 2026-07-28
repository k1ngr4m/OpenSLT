from __future__ import annotations


PARSER_TRADING_TABLES = (
    "t_fut_orders",
    "t_fut_quotes",
    "t_fut_arbi_orders",
)
PARSER_CONFIG_TABLES = ("t_account_exchange_code",)
PARSER_TABLES = (*PARSER_TRADING_TABLES, *PARSER_CONFIG_TABLES)

TRADING_DATABASE_SUFFIX = "_trading_data"
CONFIG_DATABASE_SUFFIX = "_config"


def parser_config_database_name(trading_database_name: str) -> str:
    name = trading_database_name.strip()
    if not name.endswith(TRADING_DATABASE_SUFFIX):
        return ""
    prefix = name[: -len(TRADING_DATABASE_SUFFIX)]
    return f"{prefix}{CONFIG_DATABASE_SUFFIX}" if prefix else ""


def parser_table_database_name(table: str, trading_database_name: str) -> str:
    if table in PARSER_TRADING_TABLES:
        return trading_database_name.strip()
    if table in PARSER_CONFIG_TABLES:
        return parser_config_database_name(trading_database_name)
    return ""
