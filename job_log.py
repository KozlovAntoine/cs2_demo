import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime


_active_log = ContextVar("utility_job_log", default=None)


def create_job_log(output, kind, job_id):
    folder = output / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = folder / f"{stamp}-{kind}-{job_id[:12]}.log"
    handler = logging.FileHandler(path, mode="x", encoding="utf-8", errors="backslashreplace")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.Logger("utility-job")
    logger.addHandler(handler)
    return logger


@contextmanager
def bind_job_log(logger):
    token = _active_log.set(logger)
    try:
        yield
    finally:
        _active_log.reset(token)
        for handler in logger.handlers:
            handler.close()


def current_job_log():
    return _active_log.get()


def log_command_output(logger, result, *, log_stdout=True):
    streams = ("stdout", "stderr") if log_stdout else ("stderr",)
    for stream in streams:
        output = getattr(result, stream, None)
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if output and (output := output.strip()):
            logger.info("%s\n%s", stream, output)
