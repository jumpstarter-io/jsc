import sys
import threading


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    WHITE = '\033[97m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = "\033[1m"

    def disable(self):
        self.HEADER = ''
        self.OKBLUE = ''
        self.OKGREEN = ''
        self.WARNING = ''
        self.WHITE = ''
        self.FAIL = ''
        self.ENDC = ''


print_lock = threading.Lock()


def print_locked(message, bcolor, printstr="%s%s%s\n"):
    with print_lock:
        sys.stderr.write(printstr % (bcolor, message, bcolors.ENDC))
        sys.stderr.flush()


def info(message):
    print_locked(message, bcolors.HEADER)


def white(message):
    print_locked(message, bcolors.WHITE)


def ok(message):
    print_locked(message, bcolors.OKGREEN)


def warn(message):
    print_locked(message, bcolors.WARNING)


def err(message):
    print_locked(message, bcolors.FAIL, "%sError: %s%s\n")
