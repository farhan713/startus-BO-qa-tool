import logging
import sys

_FMT = "%(asctime)s [%(levelname)8s] %(name)s :: %(message)s"
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FMT, datefmt="%H:%M:%S"))
    root = logging.getLogger("stratus_qa")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(f"stratus_qa.{name}")
