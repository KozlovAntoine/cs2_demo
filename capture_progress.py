import os
import re
from pathlib import Path


SERVER_LOG = Path.home() / ".csdm" / "logs" / "csdm.log"
SEQUENCE_CAMERA = re.compile(r"^\[\d+\] Executed: mirv_script_exec globalThis\.csUtilityVideo\((\d+)\)$")
RECORD_START = re.compile(r"^\[\d+\] Executed: mirv_streams record start$")
RECORD_END = re.compile(r"^\[\d+\] Executed: mirv_streams record end$")
FINAL_VIDEO = re.compile(r"/sequence-(\d+)-tick-\d+-to-\d+\.mp4$")


def log_path(path):
    return os.path.normcase(str(path)).replace("\\", "/")


class LogTail:
    def __init__(self, path, *, from_end=False):
        self.path = path
        self.offset = 0
        self.identity = None
        if from_end:
            try:
                info = path.stat()
            except OSError:
                return
            self.identity = (info.st_dev, info.st_ino)
            self.offset = info.st_size

    def read(self):
        try:
            with self.path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                if self.identity != identity or self.offset > info.st_size:
                    self.identity = identity
                    self.offset = 0
                stream.seek(self.offset)
                complete, newline, _ = stream.read().rpartition(b"\n")
        except OSError:
            return []
        self.offset += len(complete) + len(newline)
        return complete.decode("utf-8", errors="replace").splitlines()


class CaptureProgress:
    def __init__(self, demo, render_folder, total, owners=None):
        """total clips; owners gives the clip number of each sequence when a clip is recorded in several."""
        self.demo = log_path(demo)
        self.render_folder = log_path(render_folder) + "/"
        self.owners = owners or list(range(1, total + 1))
        self.total = len(self.owners)
        self.server_log = LogTail(SERVER_LOG, from_end=True)
        self.game_log = None
        self.game_matches = False
        self.capture_known = False
        self.capturing_sequence = None
        self.capture_started = False
        self.captured = set()
        self.generated = set()
        self.pending_mux = None
        self.state = "starting"

    def read_server_log(self):
        for line in self.server_log.read():
            _, header, message = line.partition(" | DEBUG | server | ")
            if not header:
                continue
            if message.startswith("Starting HLAE ") and f'{self.demo}/"' in log_path(message):
                executable = re.search(r'-programPath "([^"]+)"', message)
                if executable:
                    self.game_log = LogTail(Path(executable[1]).with_name("csdm.log"))
            elif message.startswith("Getting HLAE take folder path "):
                path = message.removeprefix("Getting HLAE take folder path ")
                if log_path(path).startswith(self.render_folder):
                    self.state = "generating"
            elif message.startswith("Starting FFmpeg "):
                outputs = re.findall(r'"([^"]+)"', message)
                path = log_path(outputs[-1]) if outputs else ""
                self.pending_mux = None
                # CS Demo Manager can also run FFmpeg to prepare audio before writing the final MP4.
                if path.startswith(self.render_folder) and (match := FINAL_VIDEO.search(path)):
                    self.state = "generating"
                    number = int(match[1])
                    if 1 <= number <= self.total:
                        self.pending_mux = number
            elif message.startswith("FFmpeg exit "):
                if message == "FFmpeg exit 0" and self.pending_mux is not None:
                    self.generated.add(self.pending_mux)
                self.pending_mux = None

    def read_game_log(self):
        if self.game_log is None:
            return
        for line in self.game_log.read():
            if line.startswith("Loading JSON file "):
                self.game_matches = log_path(line.removeprefix("Loading JSON file ")) == f"{self.demo}.json"
                self.capture_started = False
                self.capturing_sequence = None
                if self.game_matches:
                    self.capture_known = True
                    if self.state == "starting":
                        self.state = "capture"
            elif self.game_matches:
                if match := SEQUENCE_CAMERA.fullmatch(line):
                    number = int(match[1])
                    self.capturing_sequence = number if 1 <= number <= self.total else None
                    self.capture_started = False
                elif RECORD_START.fullmatch(line):
                    self.capture_started = True
                elif RECORD_END.fullmatch(line) and self.capture_started:
                    if self.capturing_sequence is not None:
                        self.captured.add(self.capturing_sequence)
                    self.capture_started = False

    def snapshot(self):
        self.read_server_log()
        self.read_game_log()
        return {"state": self.state, "captured": self.clips(self.captured) if self.capture_known else None,
                "generated": self.clips(self.generated)}

    def clips(self, sequences):
        """How many clips have all their sequences in the set."""
        pending = {self.owners[number - 1] for number in range(1, self.total + 1) if number not in sequences}
        return len(set(self.owners) - pending)
