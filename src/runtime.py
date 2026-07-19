"""Request-local runtime configuration for CLI and hosted demos."""
import contextvars, os

_config = contextvars.ContextVar("blockresearch_config", default={})


def configure(values=None):
    clean = {str(k): str(v) for k, v in (values or {}).items() if v not in (None, "")}
    return _config.set(clean)


def reset(token):
    _config.reset(token)


def env(name, default=None):
    return _config.get().get(name, os.environ.get(name, default))

