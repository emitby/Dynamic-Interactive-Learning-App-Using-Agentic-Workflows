"""Command-line entry point: ``python -m studysprint <command>``. Use the launchers (``run.ps1`` / ``run.sh``).

Commands:
    serve [--demo] [--port N]          run the web app on http://127.0.0.1:N (default 8000)
    build "subject" --grade G          build one lesson with the agents and print a summary
    guide                              take screenshots of the app (demo mode) and build docs/how-it-works.pdf

The launchers set environment variables that must exist *before* Python starts
(``PYTHONPYCACHEPREFIX``, ``PLAYWRIGHT_BROWSERS_PATH``), so every cache stays inside the
project without this code touching the environment (CLAUDE.md: no ``os``, no system
configuration access).
"""

import argparse
import sys

#: Loopback only. The app is a personal study tool and must not be reachable from the network.
HOST = "127.0.0.1"


def cmd_serve(args):
    """Run the web app with uvicorn until interrupted (Ctrl+C).

    Args:
        args: Parsed arguments with ``demo`` and ``port``.

    Returns:
        Exit code 0.
    """
    import uvicorn

    from studysprint.api import create_app
    print(f"Study Sprint on http://{HOST}:{args.port}  ({'demo mode' if args.demo else 'live agents'})")
    uvicorn.run(create_app(demo=args.demo), host=HOST, port=args.port, log_level="warning")
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
    s.set_defaults(fn=cmd_serve)
    b = sub.add_parser("build", help="build one lesson with the agents")
    b.add_argument("subject")
    b.add_argument("--grade", required=True, help="K, 1-12 or C (college)")
    b.set_defaults(fn=cmd_build)
    g = sub.add_parser("guide", help="build docs/how-it-works.pdf with screenshots")
    g.set_defaults(fn=cmd_guide)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
