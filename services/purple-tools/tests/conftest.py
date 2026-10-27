import os

# The tests never reach the real reporter, search or Mentat on this PC and tailnet: every service URL points at a closed port unless a test sets it.
os.environ["PURPLE_POPUP_URL"] = "http://127.0.0.1:9"
os.environ["PURPLE_SEARCH_URL"] = "http://127.0.0.1:9"
os.environ["PURPLE_MENTAT_URL"] = "http://127.0.0.1:9"
