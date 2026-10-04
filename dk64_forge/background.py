"""Serial CPU jobs; results reach widgets through queued Qt signals."""
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

class Events(QObject):
    result = Signal(int, object, str)
    progress = Signal(int, str)

class Job(QRunnable):
    def __init__(self, token, work, events):
        super().__init__()
        self.token, self.work, self.events = token, work, events
    def run(self):
        try:
            value = self.work(lambda text: self.events.progress.emit(self.token, text))
            self.events.result.emit(self.token, value, "")
        except Exception as exc:
            self.events.result.emit(self.token, None, str(exc))

class Loader(QObject):
    busy = Signal(bool)
    progress = Signal(str)
    failed = Signal(str)
    def __init__(self, parent):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.events = Events(self)
        self.events.result.connect(self._result)
        self.events.progress.connect(self._progress)
        self.token, self.done = 0, None
        self.pending = False
    def submit(self, work, done):
        self.token += 1
        self.pool.clear()
        self.done = done
        self.pending = True
        self.busy.emit(True)
        self.pool.start(Job(self.token, work, self.events))
    @Slot(int, str)
    def _progress(self, token, text):
        if token == self.token:
            self.progress.emit(text)
    @Slot(int, object, str)
    def _result(self, token, value, error):
        if token != self.token:
            return
        self.pending = False
        self.busy.emit(False)
        if error:
            self.failed.emit(error)
        elif self.done is not None:
            self.done(value)

    def cancel(self):
        self.token += 1
        self.pool.clear()
        self.pending = False
        self.done = None
        self.busy.emit(False)
