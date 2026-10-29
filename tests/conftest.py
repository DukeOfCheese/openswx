import os

# tests read and rebuild files afresh: results cached on disk by earlier runs must not answer for them
os.environ.setdefault("OPENSWX_CACHE", "off")
