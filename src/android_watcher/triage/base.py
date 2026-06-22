"""Triager protocol, TriageResult dataclass, and TRIAGERS registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from android_watcher.config import AIConfig
from android_watcher.models import Change
from android_watcher.registry import Registry


@dataclass
class TriageResult:
	changes: list[Change]
	tldr: str | None = None
	unavailable: str | None = None


@runtime_checkable
class Triager(Protocol):
	def triage(self, changes: list[Change], config: AIConfig) -> TriageResult: ...


TRIAGERS: Registry[Triager] = Registry("triager")
