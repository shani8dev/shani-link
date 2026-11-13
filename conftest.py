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
# nor their data or cache: the call history, the address book and the phone's
# cached contacts live there, and a test that logs a call must never log it in
# somebody's real history
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="shani-link-test-data-")
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="shani-link-test-cache-")
# nor its state: the screen stream writes its own log there, and a test that
# starts (or fakes) a stream must not leave one in the person's state folder
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="shani-link-test-state-")
