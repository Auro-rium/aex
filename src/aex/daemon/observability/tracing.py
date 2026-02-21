"""Lightweight deterministic tracing utilities."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from ..utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)


@dataclass
class Span:
    trace_id: str
    span_name: str
    start: float


def start_span(trace_id: str, span_name: str) -> Span:
    return Span(trace_id=trace_id, span_name=span_name, start=perf_counter())


def end_span(span: Span, **attrs):
    duration_ms = int((perf_counter() - span.start) * 1000)
    logger.info(
        "trace.span",
        trace_id=span.trace_id,
        span=span.span_name,
        duration_ms=duration_ms,
        **attrs,
    )
