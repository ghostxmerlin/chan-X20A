"""The tool framework's error and result types (moved out of tools.py unchanged on 2026-11-19, refactor: tool_time
(rules) raised ToolError by importing the framework; tools.py re-exports both names)."""
CODES = ("bad_args", "not_available", "switch_off", "paused", "budget", "robot_offline", "timeout", "refused",
         "failed")


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code if code in CODES else "failed"
        self.message = str(message)[:200]


class Paid:
    """What a paid tool returns: its data and its cost {amount, cur} (the call's run-log row carries the cost)."""
    def __init__(self, data, cost):
        self.data, self.cost = data, cost
