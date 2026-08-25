"""OpenTelemetry spans exported to Cloud Trace.

Cloud Run already records one span per request, which tells you an obligation
took four seconds and nothing about where they went. These spans cover the hops
inside: interpret, policy, dispatch, evidence. That is the difference between a
latency graph and a reasoning chain.

Every span carries the obligation id, so one trace answers "what did the system
do about this obligation and in what order" without correlating log lines by eye.

Tracing must never be the reason a request fails. If the exporter cannot start,
this falls back to a provider that records nothing and says so once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import logs
from config import get_settings

_ready = False


def setup(app: Any) -> bool:
    """Configure the exporter and instrument the app. Safe to call once."""
    global _ready
    if _ready:
        return True

    settings = get_settings()
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "sentinel-engine",
                    "service.version": settings.git_sha,
                }
            )
        )
        # Simple rather than batched. Cloud Run throttles a container's CPU to
        # near zero once a request returns, so a batch processor's background
        # thread may not run before the instance is reclaimed and its buffered
        # spans go with it. Exporting inside the request costs a little latency
        # and removes that whole class of missing-trace problem on a service
        # that scales to zero.
        provider.add_span_processor(
            SimpleSpanProcessor(CloudTraceSpanExporter(project_id=settings.project_id))
        )
        trace.set_tracer_provider(provider)

        # The health endpoint is polled by the deploy script and would otherwise
        # be most of the trace volume while carrying none of the meaning.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
        _ready = True
        logs.info("tracing enabled", exporter="cloud-trace")
        return True
    except Exception as exc:
        logs.warning(
            "tracing could not be enabled, continuing without it",
            error=str(exc),
        )
        return False


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Record one hop.

    A no-op when tracing failed to start, so call sites do not need to know
    whether the exporter came up.
    """
    if not _ready:
        yield
        return
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("sentinel")
        with tracer.start_as_current_span(name) as current:
            for key, value in attributes.items():
                if value is not None:
                    current.set_attribute(f"sentinel.{key}", str(value))
            yield
    except Exception:
        # A tracing failure must not become a request failure.
        yield


def annotate(**attributes: Any) -> None:
    """Add attributes to whichever span is currently open."""
    if not _ready:
        return
    try:
        from opentelemetry import trace

        current = trace.get_current_span()
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(f"sentinel.{key}", str(value))
    except Exception:
        return


def current_trace_id() -> str | None:
    """The trace this request belongs to, for the link from the UI."""
    if not _ready:
        return None
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return None
        return format(context.trace_id, "032x")
    except Exception:
        return None
