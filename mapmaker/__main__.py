"""python -m mapmaker [file.mapmaker] [--selftest]"""
import sys


def main():
    from .gui.app import run
    sys.exit(run(sys.argv))


if __name__ == "__main__":
    main()
