import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    cache_logger_on_first_use=True,
)

get_logger = structlog.get_logger


def bind_trace_id(trace_id: str) -> None:
    clear_contextvars()
    bind_contextvars(trace_id=trace_id)
