"""Executable entrypoint for the CG Signal desktop server.

The implementation and health contract live in :mod:`cg_signal.http`; this
file intentionally remains a thin executable.  ``--print-source-revision`` is
handled by that module for launchers that need the aggregate package hash.
"""

from cg_signal.http import main


if __name__ == "__main__":
    main()
