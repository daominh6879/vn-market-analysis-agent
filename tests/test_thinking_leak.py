"""
tests/test_thinking_leak.py — Tests for DeepSeek thinking-leak fix.

Unit tests (fast, no LLM):
  - llm/utils.py: strip_thinking handles all known leak patterns
  - agents/intents/__init__.py: extract_report fence extraction
  - OpenAIClient: strip_thinking_output flag applied at client level

End-to-end tests (real LLM, @pytest.mark.e2e):
  - "đánh giá ngắn gọn MBB" — response must not contain system-prompt echo
  - "đánh giá ngắn gọn ngân hàng quân đội" — same (company name, no ticker)
  - Technical query — response starts with markdown heading, no preamble

Run unit only (fast):
    pytest tests/test_thinking_leak.py -v -m "not e2e"

Run all (slow, costs tokens):
    pytest tests/test_thinking_leak.py -v -m e2e -s
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv()


# ── strip_thinking unit tests ─────────────────────────────────────────────────

from llm.utils import strip_thinking


def test_strip_explicit_think_tags():
    raw = "<think>I need to figure out...\nLet me reason step by step.</think>\n# Report\nContent here."
    result = strip_thinking(raw)
    assert "<think>" not in result
    assert "# Report" in result
    assert "Content here" in result


def test_strip_inline_reasoning_lines():
    raw = "Let me analyze this.\nBased on the data provided.\n# Phân tích\nSố liệu tốt."
    result = strip_thinking(raw)
    assert "Let me analyze" not in result
    assert "Based on the data" not in result
    assert "# Phân tích" in result


def test_strip_system_prompt_echo():
    raw = (
        "Điều đầu tiên: người dùng yêu cầu 'đánh giá' nhưng cung cấp dữ liệu kỹ thuật MBB.\n"
        "Yêu cầu cuối: \"Xuất NGAY báo cáo Markdown — bắt đầu bằng '# Phân tích Kỹ thuật'.\"\n"
        "TUYỆT ĐỐI KHÔNG viết suy nghĩ hay meta-commentary. Chỉ báo cáo cuối cùng.\n"
        "# Phân tích Kỹ thuật MBB\n"
        "## Xu hướng\nTăng."
    )
    result = strip_thinking(raw)
    assert "Điều đầu tiên" not in result
    assert "TUYỆT ĐỐI KHÔNG" not in result
    assert "Yêu cầu cuối" not in result
    assert "# Phân tích Kỹ thuật MBB" in result
    assert "Tăng" in result


def test_strip_viet_reasoning_starters():
    raw = (
        "Tôi cần xem lại yêu cầu.\n"
        "Cần phân tích thêm.\n"
        "Hãy kiểm tra dữ liệu.\n"
        "# Kết quả\nNội dung báo cáo."
    )
    result = strip_thinking(raw)
    assert "Tôi cần" not in result
    assert "Cần phân tích" not in result
    assert "Hãy kiểm tra" not in result
    assert "# Kết quả" in result


def test_strip_dedup_h1():
    """When LLM rewrites the report, keep the last H1 occurrence."""
    raw = (
        "# Phân tích Kỹ thuật MBB\n"
        "Draft nháp...\n\n"
        "# Phân tích Kỹ thuật MBB\n"
        "## Xu hướng\nBản cuối."
    )
    result = strip_thinking(raw)
    assert result.count("# Phân tích Kỹ thuật MBB") == 1
    assert "Bản cuối" in result
    assert "Draft nháp" not in result


def test_strip_thinking_preserves_table_rows():
    """Pipe-prefixed lines (table rows) must not be stripped."""
    raw = (
        "# Kế hoạch Giao dịch\n"
        "| | Giá |\n"
        "|---|---|\n"
        "| **Entry** | 25,000 |\n"
        "| **Stop Loss** | 24,000 |\n"
    )
    result = strip_thinking(raw)
    assert "Entry" in result
    assert "Stop Loss" in result


def test_strip_thinking_preserves_bullet_points():
    raw = "# Bull Case\n- Doanh thu tăng 20%\n- ROE cao nhất ngành\n"
    result = strip_thinking(raw)
    assert "Doanh thu tăng 20%" in result
    assert "ROE cao nhất ngành" in result


# ── extract_report unit tests ─────────────────────────────────────────────────

from agents.intents import extract_report


def test_extract_report_with_fence():
    raw = "Thinking here...\n<report>\n# Report\nContent.\n</report>\nMore thinking."
    result = extract_report(raw)
    assert result == "# Report\nContent."
    assert "Thinking here" not in result
    assert "More thinking" not in result


def test_extract_report_no_fence_passthrough():
    raw = "# Direct report\nContent."
    result = extract_report(raw)
    assert result == raw


def test_extract_report_multiline():
    inner = "# Phân tích Kỹ thuật MBB\n## Xu hướng\nTăng mạnh.\n## Momentum\nRSI 65."
    raw = f"<think>reasoning</think>\n<report>\n{inner}\n</report>"
    result = extract_report(raw)
    assert result == inner


# ── OpenAIClient strip_thinking_output flag ───────────────────────────────────

def _make_mock_response(text: str, tool_calls=None):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 20
    resp.model = "deepseek-chat"
    return resp


def test_openai_client_strips_when_flag_true():
    from llm.openai_client import OpenAIClient
    from llm.types import Message

    client = OpenAIClient(api_key="fake", strip_thinking_output=True)
    leaked = (
        "Điều đầu tiên: người dùng yêu cầu X.\n"
        "TUYỆT ĐỐI KHÔNG viết suy nghĩ.\n"
        "# Phân tích\nKết quả."
    )
    mock_resp = _make_mock_response(leaked)

    with patch.object(client._client.chat.completions, "create", return_value=mock_resp):
        result = client.generate([Message(role="user", content="test")])

    assert "Điều đầu tiên" not in result.text
    assert "TUYỆT ĐỐI KHÔNG" not in result.text
    assert "# Phân tích" in result.text


def test_openai_client_no_strip_when_flag_false():
    from llm.openai_client import OpenAIClient
    from llm.types import Message

    client = OpenAIClient(api_key="fake", strip_thinking_output=False)
    text_with_based = "Based on the analysis, the stock looks strong.\n# Report\nContent."
    mock_resp = _make_mock_response(text_with_based)

    with patch.object(client._client.chat.completions, "create", return_value=mock_resp):
        result = client.generate([Message(role="user", content="test")])

    # "Based on" must NOT be stripped for non-DeepSeek providers
    assert "Based on the analysis" in result.text


def test_openai_client_strips_think_tags_even_without_flag():
    """<think> tags stripped regardless of flag (existing behavior)."""
    from llm.openai_client import OpenAIClient
    from llm.types import Message

    client = OpenAIClient(api_key="fake", strip_thinking_output=False)
    raw = "<think>secret reasoning</think>Final answer."
    mock_resp = _make_mock_response(raw)

    with patch.object(client._client.chat.completions, "create", return_value=mock_resp):
        result = client.generate([Message(role="user", content="test")])

    assert "<think>" not in result.text
    assert "secret reasoning" not in result.text
    assert "Final answer" in result.text


# ── End-to-end tests (real LLM) ───────────────────────────────────────────────

_LEAK_MARKERS = [
    "Điều đầu tiên:",
    "Yêu cầu cuối:",
    "TUYỆT ĐỐI KHÔNG viết",
    "Xuất NGAY báo cáo",
    "BẮT BUỘC có Entry",
    "Chỉ báo cáo cuối cùng",
    "người dùng yêu cầu",
    "cung cấp dữ liệu phân tích",
]


def _assert_no_leak(text: str):
    for marker in _LEAK_MARKERS:
        assert marker not in text, f"Thinking leak detected: {marker!r} found in output"


@pytest.mark.e2e
def test_e2e_danh_gia_ngan_gon_hpg():
    """'đánh giá ngắn gọn HPG' must not leak system-prompt text."""
    from agents.intents import technical
    result = technical.run("HPG", "đánh giá ngắn gọn HPG")
    _assert_no_leak(result)
    assert isinstance(result, str) and len(result) > 50


@pytest.mark.e2e
def test_e2e_danh_gia_ngan_gon_mbb():
    """Original reported bug query — no system-prompt echo."""
    from agents.intents import technical
    result = technical.run("MBB", "đánh giá ngắn gọn MBB")
    _assert_no_leak(result)
    assert isinstance(result, str) and len(result) > 50


@pytest.mark.e2e
def test_e2e_danh_gia_ngan_hang_quan_doi():
    """Original reported bug query (company name) — no system-prompt echo."""
    from agents.intents import technical
    result = technical.run("MBB", "đánh giá ngắn gọn ngân hàng quân đội")
    _assert_no_leak(result)
    assert isinstance(result, str) and len(result) > 50


@pytest.mark.e2e
def test_e2e_phan_tich_ky_thuat_hpg():
    """Standard technical query — no leak."""
    from agents.intents import technical
    result = technical.run("HPG", "phân tích kỹ thuật HPG hôm nay")
    _assert_no_leak(result)
    assert isinstance(result, str) and len(result) > 50


@pytest.mark.e2e
def test_e2e_phan_tich_ky_thuat_vcb():
    """VCB technical query — no system-prompt leak."""
    from agents.intents import technical
    result = technical.run("VCB", "phân tích kỹ thuật VCB hôm nay")
    _assert_no_leak(result)
    assert isinstance(result, str) and len(result) > 50


@pytest.mark.e2e
def test_e2e_report_fence_extracted():
    """Verify that extract_report + strip_thinking pipeline produces clean output."""
    from agents.intents import extract_report, strip_preamble
    from llm.utils import strip_thinking

    # Simulate a leaky LLM response
    leaky = (
        "Điều đầu tiên: người dùng yêu cầu đánh giá HPG.\n"
        "<report>\n"
        "# Phân tích Kỹ thuật HPG\n"
        "## Xu hướng\nTăng mạnh.\n"
        "</report>\n"
        "Yêu cầu cuối: Chỉ báo cáo cuối cùng."
    )
    result = strip_thinking(strip_preamble(extract_report(leaky)))
    _assert_no_leak(result)
    assert "# Phân tích Kỹ thuật HPG" in result
    assert "Tăng mạnh" in result
