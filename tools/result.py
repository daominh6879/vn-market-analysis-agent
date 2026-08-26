"""
tools/result.py — Hợp đồng lỗi chuẩn cho mọi tool (bài 20).

Mọi tool đều trả ToolResult — không bao giờ raise ra ngoài,
không bao giờ trả list rỗng trần. Agent đọc message để quyết định
bước tiếp theo.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ToolResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["ok", "no_data", "invalid_input", "upstream_error", "rate_limited"]
    data: Any | None
    message: str  # hướng dẫn agent bước tiếp — viết như nói với đồng nghiệp mới

    def __repr__(self) -> str:
        data_repr = f"{type(self.data).__name__}" if self.data is not None else "None"
        return f"ToolResult(status={self.status!r}, data={data_repr}, message={self.message!r})"
