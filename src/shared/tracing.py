"""관측(Observability): 요청 하나의 LLM·도구 호출을 단계별로 기록해 JSONL 파일로 남긴다.

API 키 없이 동작한다. 기록 위치는 .env TRACE_LOG_DIR(기본 data/traces), 끄려면 TRACE_LOG_ENABLED=false.
LangSmith를 함께 쓰려면 .env에 LANGSMITH_TRACING=true와 LANGSMITH_API_KEY를 넣으면 된다(코드 변경 없음).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.shared import pii
from src.shared.config import ROOT, env as _env


def enabled() -> bool:
    """트레이스 기록을 켰는지."""
    return _env("TRACE_LOG_ENABLED", "true").strip().lower() != "false"


def trace_dir() -> Path:
    """트레이스 파일 폴더."""
    return Path(_env("TRACE_LOG_DIR", str(ROOT / "data" / "traces")))


class TraceRecorder(BaseCallbackHandler):
    """LangChain 콜백으로 LLM 호출과 도구 호출을 span으로 모은다(시작·끝 시각, 모델, 토큰 사용량, 오류)."""

    def __init__(self) -> None:
        self.started = time.time()
        self.spans: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}

    def _start(self, run_id: Any, kind: str, name: str) -> None:
        self._open[str(run_id)] = {"kind": kind, "name": name, "start_ms": round((time.time() - self.started) * 1000)}

    def _end(self, run_id: Any, **extra: Any) -> None:
        span = self._open.pop(str(run_id), None)
        if span is None:
            return
        span["duration_ms"] = round((time.time() - self.started) * 1000) - span["start_ms"]
        span.update({k: v for k, v in extra.items() if v is not None})
        self.spans.append(span)

    def on_chat_model_start(self, serialized: dict[str, Any] | None, messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        """LLM 호출 시작."""
        name = (kwargs.get("invocation_params") or {}).get("model") or (kwargs.get("metadata") or {}).get("ls_model_name")
        self._start(run_id, "llm", str(name or "chat_model"))

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        """LLM 호출 끝(토큰 사용량 기록)."""
        usage = None
        try:
            usage = getattr(response.generations[0][0].message, "usage_metadata", None)
        except Exception:
            pass
        tokens = {k: usage[k] for k in ("input_tokens", "output_tokens") if k in usage} if usage else None
        self._end(run_id, tokens=tokens)

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        """LLM 호출 오류."""
        self._end(run_id, error=type(error).__name__)

    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, *, run_id: Any, **kwargs: Any) -> None:
        """도구 호출 시작."""
        self._start(run_id, "tool", str((serialized or {}).get("name") or kwargs.get("name") or "tool"))

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        """도구 호출 끝."""
        self._end(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        """도구 호출 오류."""
        self._end(run_id, error=type(error).__name__)


def write(route: str, question: str | None, response: dict[str, Any] | None, recorder: TraceRecorder | None = None,
          started: float | None = None, error: str | None = None) -> None:
    """요청 하나의 트레이스를 날짜별 JSONL 파일에 한 줄로 남긴다. 기록 실패는 요청을 막지 않는다."""
    if not enabled():
        return
    t0 = recorder.started if recorder else (started or time.time())
    trace = (response or {}).get("trace") or {}
    record = {
        "trace_id": uuid.uuid4().hex, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "route": route,
        "model": _env("BEDROCK_MODEL_ID"), "question": pii.mask(question), "latency_ms": round((time.time() - t0) * 1000),
        "tool_calls": [{"name": c.get("name"), "args": {k: pii.mask(v) if isinstance(v, str) else v
                                                         for k, v in (c.get("args") or {}).items()}}
                       for c in trace.get("tool_calls", [])], "problem_id": trace.get("problem_id"),
        "refusal_reason": trace.get("refusal_reason"), "db_writes": trace.get("db_writes"),
        "n_contexts": len((response or {}).get("contexts") or []), "spans": recorder.spans if recorder else [],
        "error": error,
    }
    try:
        d = trace_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{time.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
