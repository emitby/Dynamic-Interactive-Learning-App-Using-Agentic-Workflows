"""Allow ``python -m studysprint``. See :mod:`studysprint.cli`."""
import sys

from studysprint.cli import main

sys.exit(main())
