"""Repo-root pytest configuration: the package from this checkout, no bytecode
written into it, and every test's config in a temporary home (the landline
keeps its setup under ~/.config/shani-link)."""
import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.dirname(__file__))

# never the person's own config: a test outside the router fixture that touched
# the landline's files would otherwise read (and carry over) their real line
import tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="shani-link-test-config-")
