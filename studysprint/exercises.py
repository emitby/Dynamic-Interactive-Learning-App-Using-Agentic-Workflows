"""Movement breaks: which exercise to do, and the end-of-quiz summary.

The choice is made by code with :func:`secrets.choice`, never by an agent. The user asked
for a *random* pick from exactly three exercises, and ``secrets`` gives an unbiased choice
without seeding or ``os`` (``random`` would also work, but ``secrets`` keeps one source of
randomness for the whole app, including session tokens).
"""

import secrets
from collections import Counter

from studysprint.config import EXERCISES


def pick_exercise():
    """Pick one exercise uniformly at random.

    Returns:
        An exercise ID, one of the keys of :data:`studysprint.config.EXERCISES`.
    """
    return secrets.choice(sorted(EXERCISES))


def summarize(completed_ids):
    """Summarize completed exercises for the results screen.

    Args:
        completed_ids: Exercise IDs in the order they were completed. Duplicates are
            expected, since a slow reader may get several breaks.

    Returns:
        A list of ``{"id", "label", "times"}`` dicts in first-completion order, for example
        ``[{"id": "pushups", "label": "10 push-ups", "times": 2}]``. Empty if no break
        was needed.
    """
    counts = Counter(completed_ids)
    order = list(dict.fromkeys(completed_ids))   # first-seen order, without duplicates
    return [{"id": e, "label": EXERCISES[e], "times": counts[e]} for e in order]
