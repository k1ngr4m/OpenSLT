import csv
import math

import pytest

from app.builtin_statistics import build_output, parse_arguments
from app.services.statistics_execution import StatisticsScriptOutput


@pytest.mark.parametrize(('mode', 'expected', 'above_limit'), [
    ('ordinary', [100, 120, 150, 200], 2),
    ('batch_first', [100], 1),
    ('batch_interval', [20, 30, 50, 150], 0),
])
def test_builtin_statistics_contract_and_batch_boundaries(tmp_path, mode, expected, above_limit):
    path = tmp_path / 'rem_client_new_to_market_speed.csv'
    with path.open('w', newline='', encoding='utf-8-sig') as output:
        writer = csv.writer(output)
        writer.writerow(['header'] * 11)
        for timestamp, latency in [(1, 100), (1, 120), (1, 150), (2, 200), (2, 250), (2, 400)]:
            writer.writerow([0] * 8 + [timestamp, 0, latency])
    result = StatisticsScriptOutput.model_validate(build_output(path, 200, mode))
    metrics = {item.key: item.value for item in result.metrics}
    assert result.sample_count == len(expected)
    assert result.excluded_counts.above_limit == above_limit
    assert metrics['average'] == sum(expected) / len(expected)
    assert metrics['median'] == expected[len(expected) // 2]
    assert metrics['p99_9'] == expected[-1]
    assert all(math.isfinite(item.value) for item in result.metrics)


def test_batch_invalid_rows_do_not_bridge_intervals_and_empty_data_fails(tmp_path):
    path = tmp_path / 'rem_client_new_to_market_speed.csv'
    with path.open('w', newline='') as output:
        writer = csv.writer(output)
        writer.writerow(['header'] * 11)
        for timestamp, latency in [(1, 100), (1, 'bad'), (1, 150), (1, 170), (2, -1), (2, 20)]:
            writer.writerow([0] * 8 + [timestamp, 0, latency])
    result = build_output(path, 1000, 'batch_interval')
    assert result['sample_count'] == 1
    assert result['metrics'][0]['value'] == 20
    assert result['excluded_counts'] == {'above_limit': 0, 'negative': 1, 'invalid': 1}
    path.write_text('header\n', encoding='utf-8')
    with pytest.raises(ValueError, match='没有'):
        build_output(path, 1000, 'batch_first')
    assert parse_arguments(['statistics_order.py', str(path), '1000', '2'])[-1] == 'batch_interval'
