"""
Runs and Playbooks — the memory of what Theta did, and how to do it again.

A **Run** is one execution: the goal, every action, a screenshot per step, and
the outcome. It is the audit trail.

A **Playbook** is a successful run distilled into a repeatable automation: the
same actions, addressed by durable selectors instead of throwaway element
numbers, with the values you typed lifted out as inputs. Replaying one costs no
model calls at all — the model is only consulted when a step no longer resolves,
and the repair is written back.

Import from the submodules explicitly:

    from automation.runs import Run, runs
    from automation.playbooks import Playbook, playbooks, from_run
    from automation.replay import replay

Nothing is re-exported here on purpose: the store singletons are named after
their modules (`runs`, `playbooks`), and re-exporting them at package level
would shadow the modules themselves.
"""
