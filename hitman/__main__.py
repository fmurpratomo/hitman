"""Allow `python3 -m hitman`.

Lets the app run straight from a checkout with nothing installed but its
dependencies — no editable install, no build backend, no console script.
"""

from hitman.cli import main

if __name__ == "__main__":
    main()
