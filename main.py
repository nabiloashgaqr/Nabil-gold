"""Local convenience entry point.

For production on GitHub Actions use the scripts under scripts/ and workflows
under .github/workflows. This file simply runs one analysis cycle locally.
"""

from scripts.run_analysis import main


if __name__ == "__main__":
    main()
