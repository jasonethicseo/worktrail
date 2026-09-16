"""실 LLM 어댑터 — OpenAI Responses API. `.xs` 의 api.request 봉투를 그대로 옮겼다.

표준 라이브러리(urllib)만 쓴다 — Lambda 패키징에 의존성이 없다.
파라미터는 `.xs` 와 동일: model gpt-5.6-luna · reasoning effort(none/medium) ·
store false · stream false. 재시도 없음(불변식 7) — 실패는 워커의 catch 가 원장에 남긴다.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from casebook.core import prompts

MODEL = "gpt-5.6-luna"
# OPENAI_BASE_URL: 테스트 스텁·프록시용 오버라이드 (기본은 실서비스)
URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/responses")

# effort 선택지의 정본은 core.workers.EFFORT_OPTIONS 다. 여기서는 CASEBOOK_EFFORT
# env 를 어댑터 단독 사용 시의 기본값으로만 받는다(도메인이 값을 넘기면 그것이 우선).
from casebook.core.workers import EFFORT_OPTIONS  # noqa: E402

# turn_worker.xs tools 블록의 짝 — update_investigation_brief
BRIEF_TOOL = {
    "type": "function",
    "name": "update_investigation_brief",
    "description": prompts.BRIEF_TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "focus": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "considering": {"type": "array", "items": {"type": "string"}},
            "recent_updates": {"type": "array", "items": {"type": "string"}},
            "next_up": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["focus", "evidence", "considering", "recent_updates", "next_up"],
    },
}


class OpenAILLM:
    def __init__(self, api_key: str | None = None, answer_effort: str | None = None) -> None:
        self.api_key = api_key or os.environ["OPENAI_API_KEY"]
        effort = answer_effort or os.environ.get("CASEBOOK_EFFORT", "none")
        if effort not in EFFORT_OPTIONS:
            raise ValueError(f"CASEBOOK_EFFORT 는 {EFFORT_OPTIONS} 중 하나여야 한다: {effort!r}")
        self.answer_effort = effort

    def _request(self, params: dict[str, Any], timeout: int) -> dict[str, Any]:
        req = urllib.request.Request(
            URL,
            data=json.dumps(params).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as res:
            if res.status != 200:
                raise RuntimeError(f"API request failed with status {res.status}")
            return json.loads(res.read().decode("utf-8"))

    @staticmethod
    def _output_text(result: dict[str, Any]) -> str:
        msg = next((o for o in result.get("output", []) if o.get("type") == "message"), None)
        if msg is None:
            raise RuntimeError("No valid message returned by model")
        elem = next((c for c in msg.get("content", []) if c.get("type") == "output_text"), None)
        if elem is None or not (elem.get("text") or "").strip():
            raise RuntimeError("No valid message returned by model")
        return elem["text"]

    # 확장 3호 — OpenAI 가 인용 URL 에 붙이는 추적 파라미터. 원장·본문 양쪽에서 뗀다.
    # 본문 안의 URL 은 ")" 나 공백이 뒤따르므로 문자열 끝($)만 보면 안 된다.
    _UTM_FIRST_RE = re.compile(r"\?utm_source=openai&")          # ?utm=…&k=v → ?k=v
    _UTM_RE = re.compile(r"[?&]utm_source=openai(?![\w=&])")     # 마지막 파라미터면 통째로

    @classmethod
    def _strip_utm(cls, text: str) -> str:
        return cls._UTM_RE.sub("", cls._UTM_FIRST_RE.sub("?", text))

    @classmethod
    def _clean_url(cls, url: str) -> str:
        return cls._strip_utm(url).rstrip("?&")

    @classmethod
    def _web_info(cls, result: dict[str, Any]) -> dict[str, Any]:
        """web_search_call 의 질의와 message 의 url_citation 을 뽑는다. 순서 보존·중복 제거."""
        queries: list[str] = []
        for o in result.get("output", []):
            if o.get("type") != "web_search_call":
                continue
            action = o.get("action") or {}
            for q in action.get("queries") or ([action["query"]] if action.get("query") else []):
                if q not in queries:
                    queries.append(q)
        citations: list[dict[str, str]] = []
        seen: set[str] = set()
        for o in result.get("output", []):
            if o.get("type") != "message":
                continue
            for c in o.get("content", []):
                for a in c.get("annotations", []) or []:
                    if a.get("type") != "url_citation" or not a.get("url"):
                        continue
                    url = cls._clean_url(a["url"])
                    if url in seen:
                        continue
                    seen.add(url)
                    citations.append({"url": url, "title": a.get("title") or ""})
        return {"queries": queries, "citations": citations}

    def answer(self, system: str, prompt: str, effort: str | None = None,
               web: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": MODEL,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "text": {"format": {"type": "text"}},
            "reasoning": {"effort": effort if effort is not None else self.answer_effort},
            "store": False,
            "stream": False,
        }
        if web:
            # 확장 3호 — 내장 web_search. 요청은 여전히 1회(불변식 7·12); 검색은 그 안에서 모델이 한다.
            params["tools"] = [{"type": "web_search"}]
        result = self._request(params, timeout=180)
        text = self._output_text(result)
        out: dict[str, Any] = {"text": text, "usage": result.get("usage", {})}
        if web:
            out["web"] = self._web_info(result)
            out["text"] = self._strip_utm(text)
        return out

    def record_brief(self, system: str, prompt: str) -> dict[str, Any]:
        result = self._request({
            "model": MODEL,
            "reasoning": {"effort": "none"},
            "store": False,
            "stream": False,
            "text": {"format": {"type": "text"}},
            "tools": [BRIEF_TOOL],
            "tool_choice": {"type": "function", "name": "update_investigation_brief"},
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }, timeout=120)
        call = next(
            (o for o in result.get("output", [])
             if o.get("type") == "function_call" and o.get("name") == "update_investigation_brief"),
            None,
        )
        if call is None:
            raise RuntimeError("fallback_failed")
        return {"brief": json.loads(call["arguments"])}

    def draft_query(self, system: str, prompt: str) -> dict[str, Any]:
        result = self._request({
            "model": MODEL,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "text": {"format": {"type": "text"}},
            "reasoning": {"effort": "none"},
            "store": False,
            "stream": False,
        }, timeout=60)
        return {"query": self._output_text(result)}

    def assemble_record(self, system: str, prompt: str) -> dict[str, Any]:
        result = self._request({
            "model": MODEL,
            "reasoning": {"effort": "medium"},
            "store": False,
            "stream": False,
            "text": {"format": {"type": "text"}},
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }, timeout=180)
        return {"content": self._output_text(result)}
