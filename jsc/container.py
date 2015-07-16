import datetime
import fcntl
import os
import os.path
import json
import re
import select
import subprocess
import sys
import threading
import shutil

try:
    import urllib2 as url
    from urllib2 import HTTPError

except ImportError:
    import urllib.request as url
    from urllib.error import HTTPError

try:
    import httplib
except ImportError:
    import http.client as httplib

"""
This module is indended to be executed on remote side.
Although it's designed to run in python3, it still needs support for python2 since it's imported as a module locally
"""


CODE_DIR = "/app/code"
STATE_DIR = "/app/state"
JSC_DIR = os.path.join(CODE_DIR, ".jsc")
LOCK_FILE = os.path.join(JSC_DIR, "lock")
RECIPE_PATH = os.path.join(JSC_DIR, "recipe")
NEW_RECIPE_PATH = os.path.join(JSC_DIR, "new-recipe")
NEW_RECIPE_SRC = os.path.join(NEW_RECIPE_PATH, "src")
NEW_RECIPE_SCRIPT = os.path.join(NEW_RECIPE_SRC, "Jumpstart-Recipe")


RECIPE_PATH = os.path.join(JSC_DIR, "recipe")

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = "\033[1m"

    def disable(self):
        self.HEADER = ''
        self.OKBLUE = ''
        self.OKGREEN = ''
        self.WARNING = ''
        self.FAIL = ''
        self.ENDC = ''


print_lock = threading.Lock()


def print_locked(message, bcolor, printstr="%s%s%s\n"):
    with print_lock:
        sys.stderr.write(printstr % (bcolor, message, bcolors.ENDC))
        sys.stderr.flush()


def log(message):
    print_locked(message, bcolors.HEADER)


def log_ok(message):
    print_locked(message, bcolors.OKGREEN)


def log_warn(message):
    print_locked(message, bcolors.WARNING)


def fail(message):
    print_locked(message, bcolors.FAIL, "%sError: %s%s\n")
    os._exit(1)


def pythonify_args(func):
    """
    Used as a decorator to convert arguments from remoto into pythonesque equivalents
    """
    def fn(*args, **kwargs):
        if len(args) > 0 and args[0] == "remoto":
            arg_list = args[1]
            return func(*arg_list[0], **arg_list[1])
        else:
            return func(*args, **kwargs)
    return fn


class AssemblyStateError(Exception):
    """Base class for exceptions in this module."""
    pass


def touch_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except FileExistsError:
            pass


def adjust_remote_pwd(file_path):
    if not file_path.startswith("/"):
        return os.path.expanduser(os.path.join(NEW_RECIPE_SRC, file_path))
    return file_path


@pythonify_args
def file_put(file_path, content, truncate=False):
    file_path = adjust_remote_pwd(file_path)
    if not os.path.isdir(os.path.dirname(file_path)):
        return False, "Path does not exist"
    open_flag = "a+"
    if truncate is not False:
        open_flag = "w+"
    with open(file_path, open_flag) as f:
        if truncate:
            f.truncate(0)
        f.write(content)
    return True, None


@pythonify_args
def file_content_replace(file_path, find, replace, accurances=None, reverse=False):
    file_path = adjust_remote_pwd(file_path)
    try:
        with open(file_path, "r") as f:
            content = f.read()
        with open(file_path, "w") as f:
            f.truncate(0)
            if accurances is None:
                accurances = len(content)
            if not reverse:
                f.write(content.replace(find, replace, accurances))
            else:
                f.write(content[::-1].replace(find[::-1], replace[::-1], accurances)[::-1])
        return True, None
    except (FileNotFoundError, IOError):
        return False, "file {file_path} not found".format(file_path=file_path)


@pythonify_args
def install(src, dst):
    src = adjust_remote_pwd(src)
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
    except (FileNotFoundError, IOError) as e:
        return False, str(e)
    return True, None


@pythonify_args
def rc_run(cmd):
    wd = os.getcwd()
    os.chdir(NEW_RECIPE_SRC)
    success = True
    try:
        output = subprocess.check_output(cmd, shell=True).decode("utf-8")
    except subprocess.CalledProcessError as e:
        output = str(e)
    os.chdir(wd)
    return success, output


@pythonify_args
def env_json():
    with open("/app/env.json") as f:
        container_env = json.loads(f.read())
        return container_env


@pythonify_args
def assert_is_assembly():
    if not env_json()['ident']['container']['is_assembly']:
        return False, "You tried to connect to a non-assembly container"
    return True, None


@pythonify_args
def assert_assembly_is_empty():
    for node in os.listdir(CODE_DIR):
        if node != 'lost+found':
            return False, "{code_dir} is not empty, cannot continue".format(code_dir=CODE_DIR)
    return True, None


@pythonify_args
def lock_session(lock_content):
    fd = os.open(LOCK_FILE, os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(LOCK_FILE, "w") as f:
            f.truncate(0)
            f.write(lock_content)
            f.flush()
        return True, None
    except IOError:
        with open(LOCK_FILE) as f:
            lock_content = json.loads(f.read())
        return False, "File lock is already acquired by [{who}] since [{when}]".format(who=lock_content['hostname'], when=lock_content['unix_epoch'])


@pythonify_args
def check_init():
    if not os.path.exists(JSC_DIR):
            return False, None
    return True, None


@pythonify_args
def init():
    if not os.path.exists(JSC_DIR):
            touch_dir(JSC_DIR)
    if os.path.exists(NEW_RECIPE_PATH):
        shutil.rmtree(NEW_RECIPE_PATH)


@pythonify_args
def sync_dir(directory):
    fd = os.open(directory, os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)


def disk_usage_stats_pretty(dir_path):
    stat = os.statvfs(dir_path)
    total_size = stat.f_bsize * stat.f_blocks
    used_size = total_size - (stat.f_bsize * stat.f_bavail)
    percent_used = "{:.1%}".format(used_size / total_size)
    return sizeof_fmt(total_size), sizeof_fmt(used_size), percent_used


@pythonify_args
def status_short(local_version, verbose):
    env = env_json()
    container_id = env["ident"]["container"]["id"]
    email = env["ident"]["user"]["email"]
    name = env["ident"]["user"]["name"]
    info = {"container_id": container_id, "email": email, "name": name, "version": local_version}
    log_ok("jsc v{version} attached to assembly [{container_id}] by [{name} <{email}>]".format(**info))
    code_total_size, code_used_size, code_percent_used = disk_usage_stats_pretty(CODE_DIR)
    log_ok("{code_dir}: {used} used of {total} ({percent_used} free)".format(code_dir=CODE_DIR,
                                                                             used=code_used_size,
                                                                             total=code_total_size,
                                                                             percent_used=code_percent_used))
    recipe_file = os.path.join(RECIPE_PATH, "src", "Jumpstart-Recipe")
    recipe_name = "<broken/unknown>"
    recipe_deploy_time = None
    if os.path.exists(RECIPE_PATH) and os.path.exists(recipe_file):
        with open(recipe_file) as f:
            for line in f.readlines():
                clean_line = line.strip()
                if clean_line.startswith("name"):
                    recipe_name = clean_line[len("name")+1:]
        with open(os.path.join(RECIPE_PATH, "deploy-time")) as f:
            recipe_deploy_time = f.read().replace("T", " ")
    log_ok("    deployed recipe: {recipe_name}".format(recipe_name=recipe_name))
    if recipe_deploy_time is not None:
        log_ok("        at {recipe_deploy_time}".format(recipe_deploy_time=recipe_deploy_time))

    state_total_size, state_used_size, state_percent_used = disk_usage_stats_pretty(STATE_DIR)
    log_ok("{code_dir}: {used} used of {total} ({percent_used} free)".format(code_dir=STATE_DIR,
                                                                             used=state_used_size,
                                                                             total=state_total_size,
                                                                             percent_used=state_percent_used))


@pythonify_args
def is_code_dir_clean():
    for node in os.listdir(CODE_DIR):
        if node not in ("lost+found", ".jsc", ".pacman", ".config"):
            return False
    return True


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


@pythonify_args
def clean(datasets):
    for dataset in datasets:
        if dataset == 'state':
            log("cleaning {dataset}".format(dataset=dataset))
            for node in os.listdir(STATE_DIR):
                if node != "lost+found":
                    node_path = os.path.join(STATE_DIR, node)
                    subprocess.check_call("rm -rf {node_path}".format(node_path=node_path), shell=True)
            log("{dataset} was cleaned".format(dataset=dataset))
        elif dataset == 'code':
            log("cleaning {dataset}".format(dataset=dataset))
            pacman_db_dir = os.path.join(STATE_DIR, ".pacman", "db")
            subprocess.check_call("rm -rf {pacman_db_dir}".format(pacman_db_dir=pacman_db_dir), shell=True)
            for node in os.listdir(CODE_DIR):
                if node not in ("lost+found", ".jsc", ".pacman", ".config"):
                    node_path = os.path.join(CODE_DIR, node)
                    subprocess.check_call("rm -rf {node_path}".format(node_path=node_path), shell=True)
            init()
            log("{dataset} was cleaned".format(dataset=dataset))


@pythonify_args
def run():
    poller = select.epoll()
    try:
        subproc = subprocess.Popen(["{code_dir}/init".format(code_dir=CODE_DIR)], stdout=subprocess.PIPE)
    except FileNotFoundError:
        return False, "No init in {code_dir}".format(code_dir=CODE_DIR)
    poller.register(subproc.stdout, select.EPOLLHUP | select.EPOLLERR | select.EPOLLIN)
    while True:
        for fd, flags in poller.poll():
            if flags == select.EPOLLIN:
                sys.stdout.write(os.read(fd))
            elif flags == select.EPOLLHUP or flags == select.EPOLLERR:
                poller.unregister(fd)
                break
        break
    return True,



@pythonify_args
def recipe_reset():
    if os.path.exists(RECIPE_PATH):
        shutil.rmtree(RECIPE_PATH)
    # For consecutive deploys to work we need to clear every time.
    if os.path.exists(NEW_RECIPE_PATH):
        shutil.rmtree(NEW_RECIPE_PATH)
    os.mkdir(NEW_RECIPE_PATH)
    os.mkdir(NEW_RECIPE_SRC)


@pythonify_args
def read_new_recipe():
    if os.path.exists(NEW_RECIPE_SCRIPT):
        with open(NEW_RECIPE_SCRIPT) as f:
            return f.read()


@pythonify_args
def write_file(args):
    path, content = args
    with open(path, 'w') as f:
        f.write(content)


@pythonify_args
def now3339():
    return datetime.datetime.now().replace(microsecond=0).isoformat('T')


@pythonify_args
def mvtree(args):
    src, dst = args
    shutil.move(src, dst)


@pythonify_args
def rmtree(src):
    shutil.rmtree(src)


@pythonify_args
def package(args):
    output = {}
    proc = subprocess.Popen(["jumpstart", "--noconfirm", "-Sy"] + args, stdout=subprocess.PIPE)
    while True:
        line = proc.stdout.readline().decode('utf-8')
        if line == "":
            break
        words = line.split(" ")
        if words[0] == "Packages":
            # First we expect the number of packages, in a paren like (23)
            n_pkg = int(words[1].strip("()"))
            for i in range(0, n_pkg):
                # For each package we expect 2 list elements
                pkg_full = words[2 + 2 * i]
                # The package version is concated to the package name with a - between.
                pkg_parts = pkg_full.split("-")
                pkg_name = pkg_parts[0]
                # The version can and often do contain aditional '-'
                pkg_version = "-".join(pkg_parts[1:]).strip()
                if pkg_name not in args:
                    return False, "No such package"
                output[pkg_name] = pkg_version
    return True, output


@pythonify_args
def git_clone(src, dst, depth, branch, pkey):
    dst = os.path.join(JSC_DIR, "new-recipe", dst)
    depth_str = "--depth %s" % depth if depth is not None else ""
    branch_str = "--branch %s" % branch if branch is not None else ""
    git_cmd = "git clone {depth} {branch} {src} {dst}".format(depth=depth_str, branch=branch_str, src=src, dst=dst)
    if pkey is None:
        clone_cmd = git_cmd
    else:
        clone_cmd = "ssh-agent (ssh-add {pkey} && {git_cmd})".format(pkey=pkey, git_cmd=git_cmd)
    try:
        git_ret = subprocess.check_call(clone_cmd, shell=True)
    except subprocess.CalledProcessError as e:
        return False, str(e)
    if git_ret != 0:
        return False, "git clone failed"
    with open(os.path.join(dst, ".git", "HEAD")) as head_f:
        head_ref = head_f.readline().strip()
    head_ref_parts = head_ref.split(" ")
    if len(head_ref_parts) > 1:
        # We have a non-commit ref
        if head_ref_parts[0] != "ref:":
            return False, "Invalid ref spec in %s" % src
        ref = head_ref_parts[1].strip()
        # Path to ref spec, so we can read out commit
        ref_path = os.path.join(dst, ".git", ref)
        with open(ref_path) as ref_f:
            commit = ref_f.read().strip()
        # We know all we need to know
        return True, (commit, ref)
    # Commit was in .git/HEAD
    return True, (head_ref, None)


# remoto magic, needed to execute these functions remotely
if __name__ == '__channelexec__':
    try:
        for item in channel:  # noqa
            channel.send(eval(item))  # noqa
    except KeyboardInterrupt:
        pass
