"""One desktop navigator at a time, shared by quota reads and top-up clicks."""
import threading

LOCK = threading.Lock()
