import sys

from taglish_listener.cli import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0] != "eval":
        argv = ["eval", *argv]
    main(argv)
