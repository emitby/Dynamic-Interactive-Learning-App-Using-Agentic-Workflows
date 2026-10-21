"""Safety guardrails for a study app whose users may be young children.

Three layers, all enforced in code (the agents also judge, but never alone):

1. :func:`validate_subject`: what a user may type. A length limit, a character
   allowlist, and rejection of prompt-injection phrasing. The subject is later passed to
   agents as quoted data (see :func:`quote_subject`), never as instructions.
2. :func:`topic_gate`: subjects that are never taught (hard block), or only from a
   minimum grade (age-gated). The Intake Agent may also decline a subject, but it can't
   approve one this gate blocked, because the gate runs first.
3. :func:`content_problems`: every string an agent writes that reaches the screen is
   checked for links, contact details, requests for personal information, profanity
   and hard-blocked topics.

Known limits: pattern lists catch known phrasings, not every possible one. The Intake
Agent's judgment and the Reviewer Agent are the second line, and the red-team evals in
``evals/redteam.py`` track which cases each layer handles.
"""

import json
import re

from studysprint.config import GRADE_LABELS, SUBJECT_MAX_CHARS, SUBJECT_MIN_CHARS, grade_number


class SafetyError(ValueError):
    """A user input or agent output that violates a safety rule. The message is safe to show the user."""


# --------------------------------------------------------------------------- subject input

# Letters (including accented ones), digits, spaces and the punctuation real subjects use:
# "U.S. history", "Algebra 2", "Cells & DNA", "World War II (1939-1945)", "C++".
_SUBJECT_ALLOWED_RE = re.compile(r"^[\w\s.,'&()\-/:+?!]+$", re.UNICODE)

# Prompt-injection phrasing: attempts to address the model instead of naming a subject.
_INJECTION_RE = re.compile(
    r"\b(ignore|disregard|forget)\b.{0,30}\b(instructions?|rules|above|previous|prompt)\b"
    r"|\bsystem prompt\b|\byou are (now|an?)\b|\bact as\b|\bpretend\b|\bjailbreak\b"
    r"|\bdeveloper mode\b|\bnew instructions?\b",
    re.IGNORECASE)


def validate_subject(raw):
    """Clean and validate the subject a user typed.

    Args:
        raw: The text from the subject box.

    Returns:
        The subject with surrounding whitespace removed and inner runs of whitespace collapsed.

    Raises:
        SafetyError: If it's too short or too long, uses characters outside the allowlist
            (for example ``<``, ``{`` or a backtick, which only matter for markup or code
            injection), or reads like an instruction to the AI.
    """
    subject = re.sub(r"\s+", " ", (raw or "")).strip()
    if not SUBJECT_MIN_CHARS <= len(subject) <= SUBJECT_MAX_CHARS:
        raise SafetyError(f"Please enter a subject between {SUBJECT_MIN_CHARS} and {SUBJECT_MAX_CHARS} characters.")
    if not _SUBJECT_ALLOWED_RE.match(subject):
        raise SafetyError("Please use only letters, numbers and simple punctuation in the subject.")
    if _INJECTION_RE.search(subject):
        raise SafetyError("Please enter just the name of a subject, like \"fractions\" or \"volcanoes\".")
    return subject


def quote_subject(subject):
    """Render a validated subject as quoted data for an agent prompt.

    JSON-encoding the string makes it unambiguous where the user's text starts and ends,
    and the surrounding prompt tells agents to treat it only as a subject name. This is
    defense in depth: :func:`validate_subject` has already rejected injection phrasing.

    Args:
        subject: The output of :func:`validate_subject`.

    Returns:
        For example ``'<subject>"volcanoes"</subject>'``.
    """
    return f"<subject>{json.dumps(subject)}</subject>"


# --------------------------------------------------------------------------- topic gate

# Never taught at any grade. These are requests for harmful instructions or adult
# content, not academic subjects.
HARD_BLOCK = {
    "sexual or adult content": r"\b(porn\w*|sex(ual|y)? (positions?|acts?)|nude\w*|nsfw|erotic\w*|hentai|onlyfans)\b",
    "weapon or explosive making": r"\b(make|build|making|building|how to)\b.{0,20}\b(bombs?|explosives?|guns?|weapons?|napalm|molotov)\b",
    "self-harm": r"\b(self[- ]harm|how to (kill|hurt) (myself|yourself)|suicide methods?|cutting myself)\b",
    # Requires an instruction form ("how to steal", "hack into"), so "stealth aircraft"
    # or a history lesson saying "armies stole grain" isn't blocked.
    "illegal activity how-to": r"\bhow to (steal|shoplift|pick a lock|hotwire)\b|\bhack(ing)? into\b"
                               r"|\b(make|cook|buy|sell) (meth|drugs|cocaine|heroin)\b",
    "graphic violence": r"\b(gore|torture (methods?|techniques?)|snuff)\b",
    "hate content": r"\b(why .{0,20} (are|is) inferior|racial superiority|white power)\b",
}

# Legitimate school topics that are taught only from a certain grade (US health and
# social-studies norms). Below that grade the gate declines kindly and suggests asking
# a teacher or parent.
AGE_GATED = {
    r"\b(drugs?|alcohol|tobacco|vaping|addiction|opioids?)\b": 5,
    r"\b(puberty|reproduction|reproductive system|sex education)\b": 5,
    r"\b(suicide prevention|suicide)\b": 9,
    r"\b(holocaust|genocide)\b": 6,
}

_HARD = {name: re.compile(p, re.IGNORECASE) for name, p in HARD_BLOCK.items()}
_AGED = [(re.compile(p, re.IGNORECASE), g) for p, g in AGE_GATED.items()]


def topic_gate(subject, grade):
    """Decide whether a subject may be taught at a grade, before any agent sees it.

    Args:
        subject: A validated subject string.
        grade: A grade code from :data:`studysprint.config.GRADES`.

    Returns:
        ``None`` if the subject may proceed to the Intake Agent; otherwise a kind,
        user-facing explanation of why it can't be taught here.
    """
    for pattern in _HARD.values():
        if pattern.search(subject):
            return ("That topic isn't something Study Sprint can teach. "
                    "Try a school subject like science, math, history or reading.")
    n = grade_number(grade)
    for pattern, min_grade in _AGED:
        if pattern.search(subject) and n < min_grade:
            return (f"That topic is usually taught from Grade {min_grade}. "
                    f"For {GRADE_LABELS[grade]}, try asking a teacher or a grown-up about it, "
                    "or pick another subject.")
    return None


# --------------------------------------------------------------------------- agent output

_URL_RE = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|org|net|io|gov|edu|ly|co)\b)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE_RE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
# Lesson text must never ask the learner to share anything about themselves.
_PII_REQUEST_RE = re.compile(
    r"\b(your|ur) (full name|last name|home address|address|phone( number)?|email|school name|"
    r"password|birthday|age|location|photo|picture of you)\b|\bwhere do you live\b|\bsend (me|us) (a|your)\b",
    re.IGNORECASE)
_PROFANITY_RE = re.compile(r"\b(damn|hell|crap|shit\w*|fuck\w*|bitch\w*|ass(hole)?|bastard|stupid|idiot|dumb)\b",
                           re.IGNORECASE)
_HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def content_problems(text):
    """List every safety problem in one piece of agent-written text.

    Checked:
        * links and web addresses (the lesson must be self-contained, so a child is
          never sent off-site)
        * email addresses and phone numbers
        * requests for the learner's personal information
        * profanity and put-downs. "Stupid", "dumb" and "idiot" are included because
          they're unkind in a learning context, even when they aren't profane.
        * HTML tags. The front end renders text with ``textContent``, so tags would
          show up as literal markup.
        * hard-blocked topics (see :data:`HARD_BLOCK`)

    Args:
        text: One string an agent wrote.

    Returns:
        Human-readable problems, each naming the offending text, written so an agent
        can fix them. An empty list means the text is clean.
    """
    problems = []
    checks = [
        (_URL_RE, "Remove links or web addresses"),
        (_EMAIL_RE, "Remove email addresses"),
        (_PHONE_RE, "Remove phone numbers"),
        (_PII_REQUEST_RE, "Never ask the learner for personal information"),
        (_PROFANITY_RE, "Use kind, classroom-appropriate words"),
        (_HTML_RE, "Use plain text, not HTML tags"),
    ]
    for pattern, fix in checks:
        m = pattern.search(text)
        if m:
            problems.append(f"{fix} (found {m.group(0)!r}).")
    for name, pattern in _HARD.items():
        if pattern.search(text):
            problems.append(f"Content touches a blocked topic ({name}).")
    return problems
