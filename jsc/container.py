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
BACKUPS_DIR = os.path.join(JSC_DIR, "backups")
BACKUPS_SEQ_FILE_PATH = os.path.join(BACKUPS_DIR, "seq")
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
    for node in (JSC_DIR, BACKUPS_DIR):
        if not os.path.exists(node):
            return False, None
    if not os.path.exists(BACKUPS_SEQ_FILE_PATH):
        return False, None
    return True, None


@pythonify_args
def init():
    for node in (JSC_DIR, BACKUPS_DIR):
        if not os.path.exists(node):
            touch_dir(node)
    if not os.path.exists(BACKUPS_SEQ_FILE_PATH):
        with open(BACKUPS_SEQ_FILE_PATH, 'w+') as f:
            f.write("1")
    if os.path.exists(NEW_RECIPE_PATH):
        shutil.rmtree(NEW_RECIPE_PATH)


@pythonify_args
def sync_dir(directory):
    fd = os.open(directory, os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)


@pythonify_args
def status_short(local_version, verbose):
    env = env_json()
    container_id = env["ident"]["container"]["id"]
    email = env["ident"]["user"]["email"]
    name = env["ident"]["user"]["name"]
    info = {"container_id": container_id, "email": email, "name": name, "version": local_version}
    log_ok("jsc v{version} attached to assembly [{container_id}] by [{name} <{email}>]".format(**info))
    if verbose:
        try:
            with open(os.path.join(RECIPE_PATH, "software-list")) as f:
                software = json.loads(f.read())
                package_lines = "\n".join(["\t{pkg}: [{ver}]".format(pkg=pkg, ver=software["package"][pkg]["version"]) for pkg in software["package"]])
                gd_lines = "\n".join(["\t{path}: [{ref}] [{src}] [{commit}]".format(path=path,
                                                                                    ref=software["gd"][path]["ref"],
                                                                                    src=software["gd"][path]["repo"],
                                                                                    commit=software["gd"][path]["commit"][0:8])
                                      for path in software["gd"]])
                log_ok("\nDeployed packages:\n{pkg}\n\nGit deployed software:\n{gd}".format(pkg=package_lines, gd=gd_lines))
        except FileNotFoundError:
            pass
    return True


@pythonify_args
def is_code_dir_clean():
    for node in os.listdir(CODE_DIR):
        if node not in ("lost+found", ".jsc", ".pacman", ".config"):
            return False
    return True


@pythonify_args
def backup_new():
    log("Backup starting")
    recipe_dir = os.path.join(JSC_DIR, "recipe")
    new_backup_dir = os.path.join(BACKUPS_DIR, "new-backup")
    size_file = os.path.join(new_backup_dir, "size")
    touch_dir(new_backup_dir)
    with open(BACKUPS_SEQ_FILE_PATH) as f:
        seq_num = int(f.read())
    with open(BACKUPS_SEQ_FILE_PATH, "w") as f:
        f.truncate(0)
        f.write(str(seq_num + 1))
        f.flush()
    log("Compressing")
    new_backup_file = os.path.join(new_backup_dir, "data.tar.lzo")
    pacman_dir = os.path.join(CODE_DIR, ".pacman")
    if not os.path.isdir(pacman_dir):
        pacman_dir = ""
    subprocess.check_call("tar --use-compress-program=lzop --exclude='{code_dir}/.pacman/cache' --exclude='{code_dir}/.pacman/db/sync' --exclude='lost+found' -cf {new_backup_file} {code_dir}/* {pacman_dir}".format(new_backup_file=new_backup_file, code_dir=CODE_DIR, pacman_dir=pacman_dir), shell=True)
    lzop_info = subprocess.check_output("lzop --info {new_backup_file}".format(new_backup_file=new_backup_file).split(" ")).decode("utf-8")
    for entry in lzop_info.split(" "):
        # the first digit we find is the uncompressed size
        if entry.isdigit():
            log("Uncompressed size {}".format(lzop_info))
            with open(size_file, "w+") as f:
                f.truncate(0)
                f.write(entry)
                f.flush
            break
    log("Saving recipe")
    if os.path.isdir(recipe_dir):
        subprocess.check_call("cp -r {recipe_dir} {new_backup_dir}".format(recipe_dir=recipe_dir, new_backup_dir=new_backup_dir), shell=True)
    sync_dir(CODE_DIR)
    created_time = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SUTC")
    backup_target_dir = os.path.join(BACKUPS_DIR, "{seq_num}@{created_time}".format(seq_num=seq_num, created_time=created_time))
    log("Finnishing up")
    subprocess.check_call("mv {new_backup_dir} {backup_target_dir}".format(new_backup_dir=new_backup_dir, backup_target_dir=backup_target_dir), shell=True)


re_backup_name = re.compile(r"(\d)+@(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})([\w\-\+]+)")


@pythonify_args
def backup_ls():
    backup_list = []
    for node in os.listdir(BACKUPS_DIR):
        match = re.match(re_backup_name, node)
        if match is not None:
            m_id = match.group(1)
            m_date = match.group(2)
            m_time = match.group(3)
            m_tz = match.group(4)
            backup_dir = os.path.join(BACKUPS_DIR, node)
            jumpstart_recipe_path = os.path.join(backup_dir, "recipe", "src", "Jumpstart-Recipe")
            if os.path.isfile(jumpstart_recipe_path):
                with open(jumpstart_recipe_path) as f:
                    for line in f.readlines():
                        if line.startswith("name"):
                            m_recipe_name = line.split(" ")[1].strip()
                            break
            else:
                m_recipe_name = "<broken/unknown>"
            backup_list.append("{id}: {date} {time} {tz}, {recipe_name}".format(id=m_id, date=m_date, time=m_time, tz=m_tz, recipe_name=m_recipe_name))
    return backup_list


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


@pythonify_args
def backup_du():
    # STEP1: total disk size
    stat_code_dir = os.statvfs(CODE_DIR)
    code_dir_total_b = stat_code_dir.f_frsize * stat_code_dir.f_blocks
    backup_list = []
    for node in os.listdir(BACKUPS_DIR):
        match = re.match(re_backup_name, node)
        if match is not None:
            m_id = match.group(1)
            m_date = match.group(2)
            m_time = match.group(3)
            m_tz = match.group(4)
            backup_dir = os.path.join(BACKUPS_DIR, node)
            jumpstart_recipe_path = os.path.join(backup_dir, "recipe", "src", "Jumpstart-Recipe")
            if os.path.isfile(jumpstart_recipe_path):
                with open(jumpstart_recipe_path) as f:
                    for line in f.readlines():
                        if line.startswith("name"):
                            m_recipe_name = line.split(" ")[1].strip()
                            break
            else:
                m_recipe_name = "<broken/unknown>"
            backup_file = os.path.join(backup_dir, "data.tar.lzo")
            backup_file_stat = os.stat(backup_file)
            percent_of_total = "{:.1%}".format(backup_file_stat.st_size / code_dir_total_b)
            backup_file_size = sizeof_fmt(backup_file_stat.st_size)
            with open(os.path.join(backup_dir, "size")) as f:
                backup_file_raw_size = sizeof_fmt(int(f.read()))
            backup_list.append("{id}: {date} {time} {tz}, {recipe_name}, {backup_file_size} ({percent_of_total} of disk) ({backup_file_raw_size} raw)"
                               .format(id=m_id, date=m_date, time=m_time, tz=m_tz, recipe_name=m_recipe_name, backup_file_size=backup_file_size, percent_of_total=percent_of_total, backup_file_raw_size=backup_file_raw_size))
    return backup_list


@pythonify_args
def backup_rm(backup_id):
    for node in os.listdir(BACKUPS_DIR):
        match = re.match(re_backup_name, node)
        if match is not None:
            m_id = match.group(1)
            if m_id == backup_id:
                backup_dir = os.path.join(BACKUPS_DIR, node)
                deleted_backup_dir = os.path.join(BACKUPS_DIR, "deleted-backup")
                subprocess.check_call("mv {backup_dir} {deleted_backup_dir}".format(backup_dir=backup_dir, deleted_backup_dir=deleted_backup_dir), shell=True)
                sync_dir(CODE_DIR)
                subprocess.check_call("rm -rf {deleted_backup_dir}".format(deleted_backup_dir=deleted_backup_dir), shell=True)
                break


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
                if node not in ("lost+found", ".jsc", ".pacman"):
                    node_path = os.path.join(CODE_DIR, node)
                    subprocess.check_call("rm -rf {node_path}".format(node_path=node_path), shell=True)
            log("{dataset} was cleaned".format(dataset=dataset))


@pythonify_args
def revert(backup_id):
    if not is_code_dir_clean():
        return False, "{code_dir} is not clean".format(code_dir=CODE_DIR)
    for backup_dir in os.listdir(BACKUPS_DIR):
        match = re.match(re_backup_name, backup_dir)
        if match is not None:
            m_id = match.group(1)
            if m_id == backup_id:
                backup_dir_path = os.path.join(BACKUPS_DIR, backup_dir)
                backup_archive = os.path.join(backup_dir_path, "data.tar.lzo")
                subprocess.check_call("tar -xf {backup_archive} -C /".format(backup_archive=backup_archive), shell=True)
                recipe_file_path = os.path.join(backup_dir_path, "recipe")
                if os.path.isfile(recipe_file_path):
                    subprocess.call("mv {recipe_file_path {jsc_dir}/new-recipe".format(recipe_file_path=recipe_file_path, jsc_dir=JSC_DIR), shell=True)
    return True, None


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
def sync():
    recipe_path = os.path.join(JSC_DIR, "recipe")
    if not os.path.isdir(recipe_path):
        return True, None
    is_synced_file_path = os.path.join(recipe_path, "is-software-list-synced")
    with open(is_synced_file_path) as f:
        if int(f.read().strip()) != 0:
            return True, None
    env = env_json()
    if "software_list_sync_url" in env['ident']['container']:
        with open("{recipe_path}/software-list".format(recipe_path=RECIPE_PATH)) as f:
            software_list_content = f.read()
        try:
            request = url.Request(env["ident"]["container"]["software_list_sync_url"], data=software_list_content.encode())
            request.add_header("Authorization", "Session-Key {session_key}".format(session_key=env["ident"]["container"]["session_key"]))
            response = url.urlopen(request)
            if response.status != httplib.OK:
                return False, response.reason
            with open(is_synced_file_path, "w") as f:
                f.truncate(0)
                f.write("1")
                f.flush()
        except urllib.error.HTTPError as e:
            return False, "{}".format(e)
    return True, None


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
    git_ret = subprocess.check_call(clone_cmd, shell=True)
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
