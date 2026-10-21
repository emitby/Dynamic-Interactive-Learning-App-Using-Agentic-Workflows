"""Prompt text shared by every agent, so the audience and safety rules are stated identically."""

from studysprint.config import band_for, readability_range

AUDIENCE_RULES = """Audience and safety rules (enforced by code; your submission is rejected if broken):
- The learner may be a young child. Be warm, accurate and encouraging. Never scary, graphic or unkind.
- Plain text only: no links, web addresses, emails, phone numbers or HTML.
- Never ask the learner for personal information (name, age, school, location, photos).
- Everything must be self-contained: the learner can't look anything up.
- Facts must be correct and uncontroversial at this level. If a simplification is needed, keep it true."""


def level_guidance(grade, grade_label):
    """Describe the writing level for a grade, using the same numbers the validators enforce.

    Args:
        grade: Grade code.
        grade_label: Human-readable grade.

    Returns:
        A short paragraph for a system prompt or task.
    """
    band = band_for(grade)
    low, high = readability_range(grade)
    floor = f" and at least grade {low:g}" if low > float("-inf") else ""
    return (f"Writing level for {grade_label} ({band.name}): Flesch-Kincaid grade at most {high:g}{floor}; "
            f"average sentence length under {band.max_sentence_words:g} words; "
            f"each concept explanation {band.words_min}-{band.words_max} words.")
