"""Priority agent coordinator with one external-side-effect executor.

Agents may decide independently, but all operations run serially through this
coordinator so KakaoTalk and Nenova can never be controlled concurrently.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from core.moyi_control import OperationPaused


@dataclass
class Agent:
    name: str
    priority: int
    interval: float
    operation: Callable[[], object]
    next_at: float = 0.0
    runs: int = 0
    failures: int = 0


class AgentCoordinator:
    """Run due agents by priority using exactly one execution lock."""

    def __init__(self, log_path: Path, clock=time.monotonic,
                 wall_clock=time.time, on_error=None):
        self.log_path = Path(log_path)
        self.clock = clock
        self.wall_clock = wall_clock
        self.on_error = on_error or (lambda agent, exc: None)
        self.agents: list[Agent] = []
        self._executor = threading.Lock()

    def add(self, name: str, priority: int, interval: float, operation: Callable[[], object]):
        if any(agent.name == name for agent in self.agents):
            raise ValueError(f'duplicate agent: {name}')
        agent = Agent(name, priority, interval, operation)
        self.agents.append(agent)
        return agent

    def due(self, now=None):
        now = self.clock() if now is None else now
        return sorted((a for a in self.agents if now >= a.next_at),
                      key=lambda a: (a.priority, a.next_at, a.name))

    def run_due(self):
        outcomes = []
        for agent in self.due():
            queued_at = self.clock()
            due_at = agent.next_at
            with self._executor:
                started = self.clock()
                self._append({'at': self.wall_clock(), 'agent': agent.name,
                              'priority': agent.priority, 'outcome': 'started'})
                outcome, error_type, value = 'ok', '', None
                try:
                    value = agent.operation()
                except OperationPaused:
                    outcome = 'paused'
                except Exception as exc:
                    outcome, error_type = 'error', type(exc).__name__
                    agent.failures += 1
                    self.on_error(agent, exc)
                finished = self.clock()
            agent.runs += 1
            agent.next_at = finished + agent.interval
            record = {'at': self.wall_clock(), 'agent': agent.name,
                      'priority': agent.priority, 'outcome': outcome,
                      'queue_ms': round((started - queued_at) * 1000, 3),
                      'overdue_ms': round(max(0, started - due_at) * 1000, 3) if due_at else 0,
                      'duration_ms': round((finished - started) * 1000, 3),
                      'error_type': error_type}
            self._append(record)
            outcomes.append((agent.name, outcome, value))
            if outcome == 'paused':
                break
        return outcomes

    def _append(self, record):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
