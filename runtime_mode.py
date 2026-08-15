import os

BASE = os.path.dirname(os.path.abspath(__file__))

# A marker file that exists ONLY on the machine that does the scraping.
# It is listed in .gitignore, so it is never committed and therefore never
# present in the copy Streamlit Cloud clones from GitHub. That asymmetry is
# the whole trick: local has it, deployed does not.
MARKER = os.path.join(BASE, ".local_dev")


def is_local():
    """True when running on the scraping machine, False when deployed."""
    return os.path.exists(MARKER)


def mode_label():
    return "local" if is_local() else "deployed (read-only)"