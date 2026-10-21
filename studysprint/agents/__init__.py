"""The five lesson-building agents and the runtime that runs them.

Each agent module exposes the same small interface, which the harness relies on:
    * ``NAME``: display name used in the trace
    * ``build(ctx)``: returns a configured :class:`~studysprint.agents.runtime.Agent` whose
      tools write into the build context
    * ``task(ctx)``: the instruction for this agent, built from the context
"""
