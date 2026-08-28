"""Background batch executor: runs jobs serially in a worker thread, reports
events into a queue.Queue the UI polls. Cancellation via threading.Event."""
import queue
import threading
import traceback

from ..core import job
from ..core.ffbin import CancelledError, FFmpegError
from ..core.translate import TranslateError


class BatchRunner:
    def __init__(self, jobs, translator):
        """jobs: list[JobParams]; translator: core.translate.Translator."""
        self.jobs = jobs
        self.translator = translator
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.cancel.set()

    @property
    def running(self):
        return self.thread.is_alive()

    def _run(self):
        done = failed = 0
        for i, p in enumerate(self.jobs):
            if self.cancel.is_set():
                break
            self.events.put(("file_start", i, len(self.jobs), p.src.name))
            try:
                report = job.run_job(
                    p, translator=self.translator,
                    progress=lambda st, d, t, m: self.events.put(("progress", st, d, t, m)),
                    cancel=self.cancel)
                done += 1
                self.events.put(("file_done", i, p.src.name, report))
            except CancelledError:
                self.events.put(("file_cancelled", i, p.src.name, None))
                break
            except (FFmpegError, TranslateError, ValueError) as e:
                # expected pipeline failures carry a full message (ffmpeg stderr tail etc.)
                failed += 1
                self.events.put(("file_error", i, p.src.name, str(e)))
            except Exception:  # noqa: BLE001 - surface any stage failure in the UI
                failed += 1
                self.events.put(("file_error", i, p.src.name, traceback.format_exc(limit=6)))
        self.events.put(("batch_done", done, failed, None))
