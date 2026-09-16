"""검색 백엔드 없음 — SerpApi 를 뺀 자리(2026-09-02).

`web_lookup` 표면·워커·원장 시퀀스는 그대로 둔다(오라클 불변). 검색 백엔드가 없으면
워커의 catch 가 `web_lookup_failed` 를 남기고 턴은 실패로 종결된다 — `.xs` 의 SerpApi
장애 경로와 같은 모양이다. 어떤 검색을 어떻게 쓸지는 별도 결정(README 참고).
"""
from __future__ import annotations

from typing import Any


class NoSearch:
    def lookup(self, query: str, engine: str) -> dict[str, Any]:
        raise RuntimeError("web lookup is not configured (no search backend)")
