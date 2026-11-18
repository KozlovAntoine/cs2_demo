import subprocess
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic


_active_control = ContextVar("utility_job_control", default=None)


class JobCancelled(Exception):
    gallery = None
    stdout = None
    stderr = None


class JobControl:
    def __init__(self):
        self._condition = threading.Condition()
        self._pause_requested = False
        self._paused = False
        self._cancelled = False
        self._pause_started = None
        self._paused_seconds = 0

    @contextmanager
    def bind(self):
        token = _active_control.set(self)
        try:
            yield
        finally:
            _active_control.reset(token)

    def status(self):
        with self._condition:
            seconds = self._paused_seconds + (monotonic() - self._pause_started if self._pause_started is not None else 0)
            return {"pauseRequested": self._pause_requested, "paused": self._paused,
                    "cancelRequested": self._cancelled, "pausedMs": round(seconds * 1000)}

    def request(self, action):
        with self._condition:
            if action == "cancel":
                self._cancelled = True
            elif action == "resume":
                self._pause_requested = False
            elif action == "pause" and not self._cancelled:
                self._pause_requested = True
            else:
                raise ValueError("Cette commande de traitement est invalide.")
            self._condition.notify_all()

    def check_cancelled(self):
        with self._condition:
            if self._cancelled:
                raise JobCancelled()

    def checkpoint(self):
        with self._condition:
            try:
                while self._pause_requested and not self._cancelled:
                    self._paused = True
                    if self._pause_started is None:
                        self._pause_started = monotonic()
                    self._condition.wait()
            finally:
                self._paused = False
                if self._pause_started is not None:
                    self._paused_seconds += monotonic() - self._pause_started
                    self._pause_started = None
            self.check_cancelled()

    def run(self, command, **options):
        self.check_cancelled()
        timeout = options.pop("timeout", None)
        input_data = options.pop("input", None)
        if input_data is not None:
            options["stdin"] = subprocess.PIPE
        if options.pop("capture_output", False):
            options["stdout"] = options["stderr"] = subprocess.PIPE
        deadline = monotonic() + timeout if timeout is not None else None
        with subprocess.Popen(command, **options) as process:
            try:
                while True:
                    self.check_cancelled()
                    remaining = deadline - monotonic() if deadline is not None else 0.2
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        stdout, stderr = process.communicate(input_data, timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        input_data = None
                self.check_cancelled()
            except BaseException as error:
                process.kill()
                stdout, stderr = process.communicate()
                if isinstance(error, (JobCancelled, subprocess.TimeoutExpired)):
                    error.stdout = stdout
                    error.stderr = stderr
                raise
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            result.check_returncode()
            return result


def current_control():
    return _active_control.get()


def job_checkpoint():
    if control := current_control():
        control.checkpoint()


def check_job_cancelled():
    if control := current_control():
        control.check_cancelled()
