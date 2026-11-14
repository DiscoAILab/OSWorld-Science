"""LLM as a judge: a model reviews a deliverable that code cannot grade.

Some tasks end with a check that reports `pending` (see evaluators/score.py):
their grader wants an independent review of a free-text report, a judgement
on a trajectory, or similar. `osci judge` fills that gap with a model from the
`judges:` section of configs/models.yaml. The machinery here is generic (model
resolution, a client that asks for JSON and keeps a transcript, the loop over
pending cells and the rescoring); what the judge is shown and what it must
answer is task-specific and ships with the task's data as a module named in the
task JSON's `judge` block, the same way `module_metric` loads a task author's
grader.
"""
from .client import JudgeClient, JudgeError, JudgeTruncated
from .runner import judge_cell, pending_cells

__all__ = ["JudgeClient", "JudgeError", "JudgeTruncated", "judge_cell", "pending_cells"]
