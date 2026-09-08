#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import sys
import hashlib
from pathlib import Path


DEFAULT_MAX_LATENCY_NS = 999999999
BUILTIN_MODES = {
    "ordinary": "普通延迟",
    "batch_first": "批量首单延迟",
    "batch_interval": "批内发单间隔",
}


def script_detail(mode):
    if mode not in BUILTIN_MODES:
        raise ValueError("未知的内置统计口径")
    return {
        "name": "builtin_{}.py".format(mode),
        "checksum": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "executable": True,
    }

CSV_VALUE_COLUMNS = (
    ("rem_market_accept_to_client_speed", 11),
    ("rem_client_new_to_market_speed", 10),
    ("rem_client_new_quote_to_market_speed", 10),
    ("rem_market_accept_quote_to_client_speed", 11),
    ("rem_client_action_to_market_speed", 10),
    ("rem_client_action_quote_to_market_speed", 10),
)

PERCENTILES = (
    ("p0_1", "0.1%", 0.001),
    ("p0_5", "0.5%", 0.005),
    ("p1", "1%", 0.01),
    ("p5", "5%", 0.05),
    ("p10", "10%", 0.10),
    ("p25", "25%", 0.25),
    ("p50", "50%", 0.50),
    ("p75", "75%", 0.75),
    ("p90", "90%", 0.90),
    ("p95", "95%", 0.95),
    ("p99", "99%", 0.99),
    ("p99_5", "99.5%", 0.995),
    ("p99_9", "99.9%", 0.999),
)


def value_column(filename):
    for marker, column in CSV_VALUE_COLUMNS:
        if marker in filename:
            return column
    raise ValueError("无法根据 CSV 文件名确定延迟数据列")


def percentile(values, ratio):
    return values[int(len(values) * ratio)]


def read_values(csv_path, column, max_latency_ns):
    values = []
    excluded = {
        "above_limit": 0,
        "negative": 0,
        "invalid": 0,
    }

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        next(reader, None)
        for row in reader:
            if len(row) <= column:
                excluded["invalid"] += 1
                continue
            try:
                value = int(row[column].strip())
            except (TypeError, ValueError):
                excluded["invalid"] += 1
                continue
            if value > max_latency_ns:
                excluded["above_limit"] += 1
                continue
            if value < 0:
                excluded["negative"] += 1
                continue
            values.append(value)

    values.sort()
    return values, excluded


def build_metrics(values):
    average = float(sum(values)) / len(values)
    variance = sum((value - average) ** 2 for value in values) / len(values)
    metrics = [
        {"key": "average", "label": "平均值", "value": average},
        {"key": "maximum", "label": "最大值", "value": float(max(values))},
        {"key": "minimum", "label": "最小值", "value": float(min(values))},
        {"key": "median", "label": "中位数", "value": float(percentile(values, 0.50))},
        {"key": "stddev", "label": "标准差", "value": variance ** 0.5},
    ]
    metrics.extend(
        {
            "key": key,
            "label": label,
            "value": float(percentile(values, ratio)),
        }
        for key, label, ratio in PERCENTILES
    )
    return metrics


def read_batch_values(csv_path, max_latency_ns, mode):
    # 批次按原始相邻行的 msg2_ns 划分；异常行不能拼接出虚假的发送间隔。
    values = []
    excluded = {"above_limit": 0, "negative": 0, "invalid": 0}
    previous_timestamp = None
    previous_latency = None
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        next(reader, None)
        for row in reader:
            timestamp = None
            try:
                timestamp = int(row[8].strip())
                latency = int(row[10].strip())
            except (IndexError, TypeError, ValueError):
                excluded["invalid"] += 1
                previous_timestamp = timestamp
                previous_latency = None
                continue
            first = timestamp != previous_timestamp
            value = latency if mode == "batch_first" and first else None
            if mode == "batch_interval" and not first and previous_latency is not None:
                value = latency - previous_latency
            previous_timestamp = timestamp
            previous_latency = latency if latency >= 0 else None
            if latency < 0:
                excluded["negative"] += 1
                continue
            if value is None:
                continue
            if value < 0:
                excluded["negative"] += 1
            elif value >= max_latency_ns:
                excluded["above_limit"] += 1
            elif mode == "batch_first" or value > 0:
                values.append(value)
    values.sort()
    return values, excluded


def build_output(csv_path, max_latency_ns=DEFAULT_MAX_LATENCY_NS, mode="ordinary"):
    if max_latency_ns < 1:
        raise ValueError("异常大值上限必须是正整数")
    source_file = os.path.basename(csv_path)
    if mode == "ordinary":
        values, excluded = read_values(csv_path, value_column(source_file), max_latency_ns)
    elif mode in {"batch_first", "batch_interval"}:
        if "rem_client_new_to_market_speed" not in source_file:
            raise ValueError("批量单统计仅支持 rem_client_new_to_market_speed CSV")
        values, excluded = read_batch_values(csv_path, max_latency_ns, mode)
    else:
        raise ValueError("未知的内置统计口径")
    if not values:
        raise ValueError("过滤后没有可用于统计的有效延迟数据")
    return {
        "schema_version": 1,
        "source_file": source_file,
        "unit": "ns",
        "sample_count": len(values),
        "excluded_counts": excluded,
        "metrics": build_metrics(values),
    }


def parse_arguments(argv):
    if len(argv) not in (2, 3, 4):
        raise ValueError("用法: statistics_order.py <CSV 文件> [异常大值上限(ns)] [ordinary|batch_first|batch_interval]")
    csv_path = argv[1]
    max_latency_ns = DEFAULT_MAX_LATENCY_NS if len(argv) == 2 else int(argv[2])
    if max_latency_ns <= 0:
        raise ValueError("异常大值上限必须是正整数")
    mode = argv[3] if len(argv) == 4 else "ordinary"
    mode = {"1": "batch_first", "2": "batch_interval"}.get(mode, mode)
    return csv_path, max_latency_ns, mode


def main(argv=None):
    argv = sys.argv if argv is None else argv
    try:
        csv_path, max_latency_ns, mode = parse_arguments(argv)
        output = build_output(csv_path, max_latency_ns, mode)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write("统计失败: {}\n".format(exc))
        return 1

    json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
