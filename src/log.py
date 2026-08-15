import logging
import logging.handlers

from .config import Config


class LogFile:
    def __init__(self, name=None):
        self.logger = logging.getLogger(name)
        self.buffer = ""
        # StringIO.StringIO()

    def write(self, msg, level=logging.INFO):
        self.buffer += msg
        lines = self.buffer.splitlines()
        # print>>sys.stderr,lines
        if self.buffer.count('\n') == len(lines):
            self.buffer = ""
        else:
            # Last line was not \n terminated
            self.buffer = lines.pop()
        for line in lines:
            self.logger.log(level, line)

    def flush(self):
        for handler in self.logger.handlers:
            handler.flush()


class FilterEmptyLines(logging.Filter):
    def filter(self, record):
        return len(record.msg) != 0


# t-k: classes that handle logging from multiple processes
#     (supporting rotating log file)

class QueueHandler(logging.Handler):
    """
    This handler sends events to a queue. Typically, it would be used together
    with a multiprocessing Queue to centralise logging to file in one process
    (in a multi-process application), so as to avoid file write contention
    between processes.

    This code is new in Python 3.2, but this class can be copy pasted into
    user code for use with earlier Python versions.
    """

    def __init__(self, queue):
        """
        Initialise an instance, using the passed queue.
        """
        logging.Handler.__init__(self)
        self.queue = queue

    def enqueue(self, record):
        """
        Enqueue a record.

        The base implementation uses put_nowait. You may want to override
        this method if you want to use blocking, timeouts or custom queue
        implementations.
        """
        self.queue.put_nowait(record)

    def prepare(self, record):
        """
        Prepares a record for queuing. The object returned by this method is
        enqueued.

        The base implementation formats the record to merge the message
        and arguments, and removes unpickleable items from the record
        in-place.

        You might want to override this method if you want to convert
        the record to a dict or JSON string, or send a modified copy
        of the record while leaving the original intact.
        """
        # The format operation gets traceback text into record.exc_text
        # (if there's exception data), and also puts the message into
        # record.message. We can then use this to replace the original
        # msg + args, as these might be unpickleable. We also zap the
        # exc_info attribute, as it's no longer needed and, if not None,
        # will typically not be pickleable.
        self.format(record)
        record.msg = record.message
        record.args = None
        record.exc_info = None
        return record

    def emit(self, record):
        """
        Emit a record.

        Writes the LogRecord to the queue, preparing it for pickling first.
        """
        try:
            self.enqueue(self.prepare(record))
        except (KeyboardInterrupt, SystemExit):
            raise
        except:  # noqa: E722
            self.handleError(record)


def listener_configurer():
    config = Config()
    if not config.CLI_OPTIONS.daemon:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, filename='/dev/null')
    if config.LOG_NAME:
        root = logging.getLogger()
        h = logging.handlers.RotatingFileHandler(filename=config.LOG_NAME, maxBytes=config.LOG_MAXBYTES, backupCount=config.LOG_BACKUPCOUNT)
        f = logging.Formatter(fmt='%(asctime)s %(name)-12s %(levelname)-8s %(message)s', datefmt='%m-%d-%y %H:%M:%S')
        fil = FilterEmptyLines()
        h.setFormatter(f)
        h.addFilter(fil)
        root.addHandler(h)


# This is the listener process top-level loop: wait for logging events
# (LogRecords)on the queue and handle them, quit when you get a None for a 
# LogRecord.
def listener_process(queue, configurer):
    configurer()
    while True:
        try:
            record = queue.get()
            if record is None:  # We send this as a sentinel to tell the listener to quit.
                break
            logger = logging.getLogger(record.name)
            logger.handle(record)  # No level or filter logic applied - just do it!
        except (KeyboardInterrupt, SystemExit):
            # raise
            pass  # handled by signal and atexit
        except:  # noqa: E722
            import sys
            import traceback
            print('Whoops! Problem:', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)


def worker_configurer(queue):
    h = QueueHandler(queue)  # Just the one handler needed
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)

