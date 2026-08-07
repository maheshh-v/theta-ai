"""
Theta's optional research capability: plan → gather → extract → compose → verify.

Exposed to the agent as a single `research` tool for tasks that need facts before
they can act. Import from the submodules explicitly:

    from research.pipeline import research, plan_questions, ResearchError
    from research.briefs import Brief, store
    from research.render import to_markdown

Nothing is re-exported here: `research.research` as both a package attribute and
a function invites exactly the shadowing confusion avoided in `web` and
`automation`.
"""
