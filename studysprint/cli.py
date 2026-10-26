"""Command-line entry point: ``python -m studysprint <command>``. Use the launchers (``run.ps1`` / ``run.sh``).

Commands:
    serve [--demo] [--port N]          run the web app on http://127.0.0.1:N (default 8000)
    serve --public [--demo] [--password PW] --port N
                                       hosting (Render): listen on all interfaces; live agents
                                       need a password and get a daily lesson cap
    build "subject" --grade G          build one lesson with the agents and print a summary
    guide                              take screenshots of the app (demo mode) and build docs/how-it-works.pdf

The launchers set environment variables that must exist *before* Python starts
(``PYTHONPYCACHEPREFIX``, ``PLAYWRIGHT_BROWSERS_PATH``), so every cache stays inside the
project without this code touching the environment (CLAUDE.md: no ``os``, no system
configuration access).
"""

import argparse
import sys

#: Default: loopback only. Run locally, the app is a personal study tool and must not be
#: reachable from the network.
HOST = "127.0.0.1"

#: ``serve --public`` only: all interfaces, so a host such as Render can route traffic in.
PUBLIC_HOST = "0.0.0.0"


def check_serve_args(args):
    """Refuse a public server that would let anyone spend API credits.

    A public live server must have a password of at least
    :data:`~studysprint.config.MIN_PASSWORD_CHARS` characters. Render passes the password
    as ``--password "$STUDYSPRINT_PASSWORD"``, so an unset variable arrives as ``""`` and is
    refused here: a missing secret fails the deploy instead of publishing an open site.

    Args:
        args: Parsed ``serve`` arguments with ``public``, ``demo`` and ``password``.

    Returns:
        An error message, or ``None`` if the arguments are safe.
    """
    from studysprint.config import MIN_PASSWORD_CHARS
    if args.public and not args.demo and len(args.password or "") < MIN_PASSWORD_CHARS:
        return (f"serve --public with live agents needs --password of at least {MIN_PASSWORD_CHARS} "
                "characters (set STUDYSPRINT_PASSWORD on the host).")
    return None


def cmd_serve(args):
    """Run the web app with uvicorn until interrupted (Ctrl+C).

    Locally it listens on :data:`HOST`. With ``--public`` it listens on
    :data:`PUBLIC_HOST`, and live agents also get the daily lesson cap
    (:data:`~studysprint.config.DAILY_LESSON_LIMIT`). The password is never printed.

    Args:
        args: Parsed arguments with ``demo``, ``port``, ``public`` and ``password``
            (already checked by :func:`check_serve_args`).

    Returns:
        Exit code 0.
    """
    import uvicorn

    from studysprint.api import create_app
    from studysprint.config import DAILY_LESSON_LIMIT
    host = PUBLIC_HOST if args.public else HOST
    daily_limit = DAILY_LESSON_LIMIT if args.public and not args.demo else None
    print(f"Study Sprint on http://{host}:{args.port}  ({'demo mode' if args.demo else 'live agents'}"
          f"{', password required' if args.password else ''}"
          f"{f', {daily_limit} lessons a day' if daily_limit else ''})")
    app = create_app(demo=args.demo, password=args.password or None, daily_limit=daily_limit)
    uvicorn.run(app, host=host, port=args.port, log_level="warning")
    return 0


def cmd_build(args):
    """Build one lesson with the live agents and print where it was saved.

    Args:
        args: Parsed arguments with ``subject`` and ``grade``.

    Returns:
        0 on success, 1 if the build failed or was declined.
    """
    from studysprint.harness import Declined, build_lesson, new_context
    ctx = new_context(args.subject, args.grade, progress=lambda s: print(f"  … {s}"))
    print(f"Building '{ctx.subject}' for {ctx.grade_label} -> {ctx.run_dir}")
    try:
        lesson = build_lesson(ctx)
    except Declined as exc:
        print(f"Declined: {exc}")
        return 1
    except Exception as exc:
        print(f"Failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"Topic: {lesson.topic}")
    for c in lesson.concepts:
        print(f"  • {c.title}")
    print(f"Cost ${ctx.trace.cost_usd():.3f}. Saved {ctx.run_dir / 'lesson.json'}")
    return 0


def cmd_guide(args):
    """Screenshot every screen of the demo app, then build the PDF guide.

    Args:
        args: Parsed arguments (unused).

    Returns:
        Exit code 0.
    """
    from studysprint.guide import build_guide
    print(build_guide())
    return 0


def main(argv=None):
    """Parse arguments and dispatch to a command.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        The command's exit code.
    """
    ap = argparse.ArgumentParser(prog="studysprint", description="Study Sprint: multi-agent study app")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--demo", action="store_true", help="serve bundled sample lessons (no API key needed)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--public", action="store_true", help="listen on all interfaces (for hosting such as Render)")
    s.add_argument("--password", help="require this password to use the site (HTTP Basic Auth)")
    s.set_defaults(fn=cmd_serve)
    b = sub.add_parser("build", help="build one lesson with the agents")
    b.add_argument("subject")
    b.add_argument("--grade", required=True, help="K, 1-12 or C (college)")
    b.set_defaults(fn=cmd_build)
    g = sub.add_parser("guide", help="build docs/how-it-works.pdf with screenshots")
    g.set_defaults(fn=cmd_guide)
    args = ap.parse_args(argv)
    if args.fn is cmd_serve and (problem := check_serve_args(args)):
        ap.error(problem)       # exits with status 2 before the server starts
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
