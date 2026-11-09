"""Issue AF-AW spreadsheet table builder."""

import zipfile
from pathlib import Path

from langfuse_export.table import build_table
from langfuse_export.writer import write_xlsx


def _metrics(rounds=2, triggered=True):
    detail = [
        {
            "round": 1,
            "proposeAt": "2026-11-08T07:00:01.000Z",
            "notifiedAt": "2026-11-08T07:00:01.000Z",
            "decisionAt": "2026-11-08T07:00:02.000Z",
            "sdkConstructedAt": "2026-11-08T07:00:03.000Z",
            "replySentAt": "2026-11-08T07:00:04.000Z",
            "negotiationStartedAt": "2026-11-08T07:00:00.500Z",
        }
    ]
    if rounds > 1:
        detail.append(
            {
                "round": 2,
                "proposeAt": "2026-11-08T07:00:05.000Z",
                "decisionAt": "2026-11-08T07:00:06.000Z",
            }
        )
    return {
        "traceId": "0123456789abcdef0123456789abcdef",
        "scenario": "night",
        "runMode": "create",
        "chainId": "conv-1",
        "timeline": {
            "receivedAt": "2026-11-08T06:59:00.000Z",
            "sdkCalledAt": "2026-11-08T06:59:01.000Z",
            "requestSentAt": "2026-11-08T06:59:02.000Z",
            "taskCreatedAt": "2026-11-08T07:00:10.000Z",
        },
        "latency": {"endToEndMs": 70000},
        "tokens": {
            "input": 100,
            "cachedInput": 20,
            "output": 30,
            "total": 130,
            "bySide": {
                "client": {"input": 60, "cachedInput": 10, "output": 20, "total": 80},
                "server": {"input": 40, "cachedInput": 10, "output": 10, "total": 50},
            },
        },
        "negotiation": {
            "triggered": triggered,
            "rounds": rounds,
            "latencyMs": 1500,
            "roundsDetail": detail if triggered else [],
        },
    }


def test_build_table_expands_each_round_into_columns():
    headers, rows = build_table(_metrics(rounds=2))
    assert headers[:4] == ["trace ID", "场景", "运行模式", "链 ID"]
    assert headers[4:13] == [
        "输入token数（客户端）",
        "输入缓存token数（客户端）",
        "输出token数（客户端）",
        "总token数（客户端）",
        "输入token数（服务端）",
        "输入缓存token数（服务端）",
        "输出token数（服务端）",
        "总token数（服务端）",
        "端到端时延(ms)",
    ]
    assert headers[13:17] == [
        "原始意图起始时间点",
        "OSS调用A2A-T SDK时间点",
        "OSS下发请求时间点",
        "任务创建/修改成功时间点",
    ]
    assert "第1轮 协商发起时间点" in headers
    assert "第2轮 意图理解（发起协商）时间点" in headers
    assert headers.index("第2轮 协商发起时间点") == headers.index("第1轮 协商发起时间点") + 6
    assert headers[-3:] == ["协商时延(ms)", "协商轮次", "是否触发协商"]
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "0123456789abcdef0123456789abcdef"
    assert row[4:13] == [60, 10, 20, 80, 40, 10, 10, 50, 70000]
    assert row[13] == "2026-11-08T06:59:00.000Z"
    assert row[17] == "2026-11-08T07:00:01.000Z"  # 第1轮 协商发起
    assert row[23] == "2026-11-08T07:00:05.000Z"  # 第2轮 协商发起
    assert row[-3:] == [1500, 2, "是"]


def test_build_table_without_negotiation_keeps_one_empty_round_group():
    headers, rows = build_table(_metrics(rounds=0, triggered=False))
    assert "第1轮 协商发起时间点" in headers
    assert "第2轮 协商发起时间点" not in headers
    row = rows[0]
    assert row[17] is None
    assert row[-1] == "否"


def test_write_xlsx_creates_valid_archive_with_chinese_headers():
    path = Path("tmp_table_test.xlsx")
    try:
        headers, rows = build_table(_metrics(rounds=1))
        write_xlsx(path, headers, rows)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            assert "xl/worksheets/sheet1.xml" in names
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "原始意图起始时间点" in sheet
        assert "输入token数（客户端）" in sheet
        assert "2026-11-08T06:59:00.000Z" in sheet
        assert "<v>70000</v>" in sheet
    finally:
        path.unlink(missing_ok=True)


def test_build_table_many_traces_uses_widest_round_block():
    first = _metrics(rounds=0, triggered=False)
    first["traceId"] = "a" * 32
    second = _metrics(rounds=2, triggered=True)
    second["traceId"] = "b" * 32
    headers, rows = build_table([first, second])
    assert len(rows) == 2
    assert "第2轮 协商发起时间点" in headers
    assert rows[0][0] == "a" * 32
    assert rows[1][0] == "b" * 32
    # First trace has no round 2, so its R2 cells stay empty.
    start = headers.index("第2轮 协商发起时间点")
    assert start == 23
    assert rows[0][start] is None
    assert rows[1][start] == "2026-11-08T07:00:05.000Z"
