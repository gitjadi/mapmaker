"""Run functions on a background thread and get the result back on the GUI thread.

Results travel through one relay QObject that lives on the GUI thread, so the
callbacks always run there (Qt queues signals across threads)."""
from __future__ import annotations

import itertools
import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger("mapmaker")


class _Relay(QObject):
    finished = Signal(int, bool, object)   # job id, ok, result-or-traceback

    def __init__(self):
        super().__init__()
        self.callbacks: dict[int, tuple] = {}
        self.finished.connect(self._dispatch)

    @Slot(int, bool, object)
    def _dispatch(self, jid, ok, payload):
        on_done, on_error = self.callbacks.pop(jid, (None, None))
        cb = on_done if ok else on_error
        if cb is not None:
            cb(payload)


_relay = None
_ids = itertools.count(1)


class _Job(QRunnable):
    def __init__(self, jid, fn, args, kwargs, relay):
        super().__init__()
        self.jid, self.fn, self.args, self.kwargs, self.relay = jid, fn, args, kwargs, relay

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception:
            tb = traceback.format_exc()
            log.error("Background task failed:\n%s", tb)
            self.relay.finished.emit(self.jid, False, tb)
        else:
            self.relay.finished.emit(self.jid, True, result)


def run_async(fn, *args, on_done=None, on_error=None, **kwargs):
    """Call fn(*args, **kwargs) on the thread pool. on_done(result) / on_error(traceback)
    are called on the GUI thread. Must itself be called from the GUI thread."""
    global _relay
    if _relay is None:
        _relay = _Relay()
    jid = next(_ids)
    _relay.callbacks[jid] = (on_done, on_error)
    QThreadPool.globalInstance().start(_Job(jid, fn, args, kwargs, _relay))
    return jid
