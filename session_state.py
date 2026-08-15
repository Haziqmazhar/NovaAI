"""
session_state.py — Nova's shared "world state" for one running session.

Holds the persisted conversation (so follow-up commands like "actually,
open notepad instead" work across turns) and a lightweight task registry
so the orchestrator can report what a sub-agent is doing or did.
In-memory only, scoped to one Nova process's lifetime — not saved to disk.
"""

import itertools


class SessionState:
    def __init__(self):
        self.messages = []
        self.tasks = {}
        self._task_ids = itertools.count(1)

    def start_task(self, description: str) -> str:
        task_id = f"task-{next(self._task_ids)}"
        self.tasks[task_id] = {
            "description": description,
            "status": "running",
            "result": None,
            "error": None,
        }
        return task_id

    def finish_task(self, task_id: str, result: str = None, error: str = None):
        task = self.tasks[task_id]
        task["status"] = "error" if error else "done"
        task["result"] = result
        task["error"] = error
