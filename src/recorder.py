"""Per-run lossless event journal for reproducible research traces."""
import contextvars
import time

_events = contextvars.ContextVar("blockresearch_events", default=None)
_sink = contextvars.ContextVar("blockresearch_event_sink", default=None)


def start_run():
    _events.set([])


def record(kind, **payload):
    events = _events.get()
    event = {"seq": len(events or []) + 1, "time": time.time(), "kind": kind, **payload}
    if events is not None:
        events.append(event)
    sink = _sink.get()
    if sink:
        sink(event)


def snapshot():
    return list(_events.get() or [])


def set_sink(sink):
    return _sink.set(sink)


def reset_sink(token):
    _sink.reset(token)
