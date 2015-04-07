import errno
import sys
import threading
from colorama import init
init()

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = "\033[1m"
    NONE = ''

    def disable(self):
        self.HEADER = ''
        self.OKBLUE = ''
        self.OKGREEN = ''
        self.WARNING = ''
        self.FAIL = ''
        self.ENDC = ''
        self.NONE = ''


print_lock = threading.Lock()


def print_locked(message, bcolor, f, print_fmt="%s%s%s\n"):
    with print_lock:
        while True:
            try:
                f.write(print_fmt % (bcolor, message, bcolors.ENDC))
                break
            except IOError as e:
                if e.errno != errno.EAGAIN:
                    raise e
        f.flush()


def info(message, f=sys.stderr):
    print_locked(message, bcolors.HEADER, f)


def white(message, f=sys.stderr):
    print_locked(message, bcolors.NONE, f)


def ok(message, f=sys.stderr):
    print_locked(message, bcolors.OKGREEN, f)


def warn(message, f=sys.stderr):
    print_locked(message, bcolors.WARNING, f)


def err(message, f=sys.stderr):
    print_locked(message, bcolors.FAIL, f, "%sError: %s%s\n")
