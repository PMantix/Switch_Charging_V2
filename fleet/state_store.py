"""
Fleet state store — in-memory cache of per-Pi state + command queue.

Thread-safe: the cycler writes state, the web server reads it and
enqueues commands, and both can happen concurrently.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PiStatus(Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"


class CommandStatus(Enum):
    PENDING = "pending"
    SENT = "sent"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass
class QueuedCommand:
    id: str
    pi_num: int
    cmd: dict
    status: CommandStatus = CommandStatus.PENDING
    result: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


@dataclass
class PiState:
    pi_num: int
    status: PiStatus = PiStatus.UNKNOWN
    last_seen: Optional[float] = None
    signal_dbm: Optional[int] = None
    state: Optional[dict] = None

    def to_dict(self) -> dict:
        age = None
        if self.last_seen is not None:
            age = round(time.time() - self.last_seen, 1)
        return {
            "pi_num": self.pi_num,
            "status": self.status.value,
            "last_seen": self.last_seen,
            "age_s": age,
            "signal_dbm": self.signal_dbm,
            "mode": _get(self.state, "mode"),
            "frequency": _get(self.state, "frequency"),
            "sequence": _get(self.state, "sequence"),
            "step": _get(self.state, "step"),
            "auto_follow": _get(self.state, "auto_follow"),
            "sensors": _get(self.state, "sensors"),
        }


def _get(d: Optional[dict], key: str) -> Any:
    if d is None:
        return None
    return d.get(key)


class FleetStateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._pis: dict[int, PiState] = {}
        self._commands: list[QueuedCommand] = []
        self._cmd_counter = 0

    def ensure_pi(self, pi_num: int) -> None:
        with self._lock:
            if pi_num not in self._pis:
                self._pis[pi_num] = PiState(pi_num=pi_num)

    def update_state(self, pi_num: int, state: dict, signal_dbm: Optional[int] = None) -> None:
        self.ensure_pi(pi_num)
        with self._lock:
            pi = self._pis[pi_num]
            pi.status = PiStatus.ONLINE
            pi.last_seen = time.time()
            pi.state = state
            if signal_dbm is not None:
                pi.signal_dbm = signal_dbm

    def mark_offline(self, pi_num: int) -> None:
        self.ensure_pi(pi_num)
        with self._lock:
            self._pis[pi_num].status = PiStatus.OFFLINE

    def mark_unknown(self, pi_num: int) -> None:
        self.ensure_pi(pi_num)
        with self._lock:
            self._pis[pi_num].status = PiStatus.UNKNOWN

    def known_pi_nums(self) -> list[int]:
        with self._lock:
            return list(self._pis.keys())

    def get_all(self) -> list[dict]:
        with self._lock:
            return [pi.to_dict() for pi in self._pis.values()]

    def get_pi(self, pi_num: int) -> Optional[dict]:
        with self._lock:
            pi = self._pis.get(pi_num)
            return pi.to_dict() if pi else None

    def enqueue_command(self, pi_num: int, cmd: dict) -> str:
        with self._lock:
            self._cmd_counter += 1
            cmd_id = f"cmd-{self._cmd_counter}"
            qc = QueuedCommand(id=cmd_id, pi_num=pi_num, cmd=cmd)
            self._commands.append(qc)
            return cmd_id

    def drain_commands(self, pi_num: int) -> list[QueuedCommand]:
        with self._lock:
            pending = [c for c in self._commands
                       if c.pi_num == pi_num and c.status == CommandStatus.PENDING]
            for c in pending:
                c.status = CommandStatus.SENT
            return pending

    def mark_command_result(self, cmd_id: str, result: dict, confirmed: bool) -> None:
        with self._lock:
            for c in self._commands:
                if c.id == cmd_id:
                    c.status = CommandStatus.CONFIRMED if confirmed else CommandStatus.FAILED
                    c.result = result
                    c.completed_at = time.time()
                    break

    def get_commands(self, pi_num: Optional[int] = None, limit: int = 50) -> list[dict]:
        with self._lock:
            cmds = self._commands
            if pi_num is not None:
                cmds = [c for c in cmds if c.pi_num == pi_num]
            cmds = cmds[-limit:]
            return [
                {
                    "id": c.id,
                    "pi_num": c.pi_num,
                    "cmd": c.cmd,
                    "status": c.status.value,
                    "created_at": c.created_at,
                    "completed_at": c.completed_at,
                }
                for c in cmds
            ]

    def pis_with_pending_commands(self) -> set[int]:
        with self._lock:
            return {c.pi_num for c in self._commands
                    if c.status == CommandStatus.PENDING}

    def cleanup_old_commands(self, max_age_s: float = 3600) -> None:
        cutoff = time.time() - max_age_s
        with self._lock:
            self._commands = [
                c for c in self._commands
                if c.status == CommandStatus.PENDING or
                (c.completed_at is not None and c.completed_at > cutoff)
            ]
