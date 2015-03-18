import select
import sys
import json
import os
import fcntl
import re
import subprocess
import datetime
import signal
import pty
import base64
import shutil
import giturlparse
import urllib2
import httplib
from docopt import docopt, DocoptExit
import shlex
from distutils.spawn import find_executable
import termios

try:
    from __init__ import __version__
except ImportError:
    from jsc import __version__


# Terminate if sshd dies
signal.signal(signal.SIGHUP, lambda x, y: os._exit(1))


def log(message):
    message = json.dumps({"id": None, "stdout": str(message)+ "\n" })
    sys.stdout.write("{message}\n".format(message=message))
    sys.stdout.flush()


def fail(message):
    message = json.dumps({"id": None, "stderr": str(message)})
    sys.stdout.write("{message}\n".format(message=message))
    sys.stdout.flush()
    os._exit(1)

# JSONRPC Defined error_codes
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_INTERNAL_ERROR = -32604

DO_REVERT_NOT_CLEAN = -31050
DO_REVERT_INVALID_ID = -31051

DO_SYNC_NO_RECIPE_INSTALLED = -31100
DO_SYNC_SERVER_FAILED = -31101
DO_SYNC_HTTP_ERROR = -31102

DO_DEPLOY_NOT_CLEAN = -31000
DO_DEPLOY_NO_NEWRECIPE = -31200

DO_ASSERT_IS_ASSEMBLY_ERROR = -31400

DO_BACKUP_NEW_IS_CLEAN = -31500

RC_RECIPE_RUNTIME_ERROR = -31300

CODE_DIR = "/app/code"
STATE_DIR = "/app/state"
JSC_DIR = os.path.join(CODE_DIR, ".jsc")
BACKUPS_DIR = os.path.join(JSC_DIR, "backups")
BACKUPS_SEQ_FILE_PATH = os.path.join(BACKUPS_DIR, "seq")
NEW_BACKUP_DIR = os.path.join(BACKUPS_DIR, "new-backup")
LOCK_FILE = os.path.join(JSC_DIR, "lock")
RECIPE_PATH = os.path.join(JSC_DIR, "recipe")
NEW_RECIPE_PATH = os.path.join(JSC_DIR, "new-recipe")
NEW_RECIPE_SRC = os.path.join(NEW_RECIPE_PATH, "src")
NEW_RECIPE_SCRIPT = os.path.join(NEW_RECIPE_SRC, "Jumpstart-Recipe")


################################################################################
############################### Utility functions ##############################
################################################################################

class AssemblyStateError(Exception):
    """Base class for exceptions in this module."""
    pass


def subproc(args, wd=None):
    pid, child_fd = pty.fork()
    if pid == 0:
        # Child process
        if wd is not None:
            os.chdir(wd)
        binfile = args[0]
        if not os.path.isfile(binfile):
            binfile = find_executable(binfile)
        os.execv(binfile, args)
    # set up a nonblocking
    nb_child = os.dup(child_fd)
    old = termios.tcgetattr(nb_child)
    new = old[:]
    new[3] &= ~termios.ICANON
    termios.tcsetattr(nb_child, termios.TCSADRAIN, new)
    fcntl.fcntl(nb_child, fcntl.F_SETFL, os.O_NONBLOCK)
    stdin_fd = sys.stdin.fileno()
    stdin_buffer = ""
    pollfd = select.poll()
    poll_err_mask = select.POLLPRI | select.POLLERR | select.POLLHUP
    pollfd.register(stdin_fd, select.POLLIN | poll_err_mask)
    pollfd.register(nb_child, select.POLLIN | poll_err_mask)
    while True:
        pl = dict(pollfd.poll())
        if stdin_fd in pl:
            mask = pl[stdin_fd]
            if mask & poll_err_mask != 0:
                terminate()
            # Check for notifications, crash on new commands.
            new_data = sys.stdin.read()
            stdin_buffer += new_data
            if "\n" in stdin_buffer:
                lines = stdin_buffer.split("\n")
                # Last line is not complete.
                stdin_buffer = "\n".join(lines[-1:])
                messages = lines[0:-1]
                for msg in messages:
                    msg_obj = json.loads(msg)
                    if "id" in msg_obj.keys() and msg_obj["id"] is None:
                        # Might not write all of it.
                        data = msg_obj[u"stdin"]
                        os.write(child_fd, data)
                    else:
                        raise Exception("Should have been a notification")
        if nb_child in pl:
            # Forward as notification
            try:
                mask = pl[nb_child]
                if mask & poll_err_mask != 0:
                    # Stdout closed, probably don't want to read anything now.
                    break
                data = os.read(nb_child, 1024)
                sys.stdout.write(json.dumps({"id": None, "stdout": data}) + "\n")
                sys.stdout.flush()
            except OSError:
                # The fd is probably not valid anymore because the subprocess exited.
                break
    os.waitpid(pid, 0)


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
    except IOError:
        return False, "file {file_path} not found".format(file_path=file_path)


def install(src, dst):
    src = adjust_remote_pwd(src)
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
    except IOError as e:
        return False, str(e)
    return True, None


def env_json():
    with open("/app/env.json") as f:
        container_env = json.loads(f.read())
        return container_env


def sync_dir(directory):
    fd = os.open(directory, os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)


def disk_usage_stats_pretty(dir_path):
    stat = os.statvfs(dir_path)
    total_size = stat.f_bsize * stat.f_blocks
    used_size = total_size - (stat.f_bsize * stat.f_bavail)
    percent_used = "{:.1%}".format(float(used_size) / float(total_size))
    return sizeof_fmt(total_size), sizeof_fmt(used_size), percent_used


def is_code_dir_clean():
    for node in os.listdir(CODE_DIR):
        if node not in ("lost+found", ".jsc", ".pacman", ".config"):
            return False
    return True


def backup_new():
    if is_code_dir_clean():
        return None, {"code": DO_BACKUP_NEW_IS_CLEAN, "message": "Your code dir [{code_dir}] is clean, there's nothing to backup.".format(code_dir=CODE_DIR)}
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
    jsc_recipe_dir = os.path.join(JSC_DIR, "recipe")
    if not os.path.isdir(pacman_dir):
        pacman_dir = ""
    subprocess.check_call("tar --use-compress-program=lzop --exclude='{code_dir}/.pacman/cache' --exclude='{code_dir}/.pacman/db/sync' --exclude='lost+found' -cf {new_backup_file} {code_dir}/* {pacman_dir} {jsc_recipe_dir}".format(new_backup_file=new_backup_file, code_dir=CODE_DIR, pacman_dir=pacman_dir, jsc_recipe_dir=jsc_recipe_dir), shell=True)
    lzop_info = subprocess.check_output("lzop --info {new_backup_file}".format(new_backup_file=new_backup_file).split(" ")).decode("utf-8")
    for entry in lzop_info.split(" "):
        # the first digit we find is the uncompressed size
        if entry.isdigit():
            with open(size_file, "w+") as f:
                f.truncate(0)
                f.write(entry)
                f.flush()
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


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, "Yi", suffix)


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


def clean(datasets):
    for dataset in datasets:
        if dataset == "state":
            log("cleaning {dataset}".format(dataset=dataset))
            for node in os.listdir(STATE_DIR):
                if node != "lost+found":
                    node_path = os.path.join(STATE_DIR, node)
                    subprocess.check_call("rm -rf {node_path}".format(node_path=node_path), shell=True)
            log("{dataset} was cleaned".format(dataset=dataset))
        elif dataset == "code":
            log("cleaning {dataset}".format(dataset=dataset))
            pacman_db_dir = os.path.join(STATE_DIR, ".pacman", "db")
            subprocess.check_call("rm -rf {pacman_db_dir}".format(pacman_db_dir=pacman_db_dir), shell=True)
            for node in os.listdir(CODE_DIR):
                if node not in ("lost+found", ".jsc", ".pacman", ".config"):
                    node_path = os.path.join(CODE_DIR, node)
                    subprocess.check_call("rm -rf {node_path}".format(node_path=node_path), shell=True)
            recipe_reset()
            do_init({})
            do_sync({})
            log("{dataset} was cleaned".format(dataset=dataset))


def recipe_reset():
    if os.path.exists(RECIPE_PATH):
        shutil.rmtree(RECIPE_PATH)
    # For consecutive deploys to work we need to clear every time.
    if os.path.exists(NEW_RECIPE_PATH):
        shutil.rmtree(NEW_RECIPE_PATH)
    os.mkdir(NEW_RECIPE_PATH)
    os.mkdir(NEW_RECIPE_SRC)


def read_new_recipe():
    if os.path.exists(NEW_RECIPE_SCRIPT):
        with open(NEW_RECIPE_SCRIPT) as f:
            return f.read()


def write_file(args):
    path, content = args
    with open(path, "w") as f:
        f.write(content)


def now3339():
    return datetime.datetime.now().replace(microsecond=0).isoformat("T")


def mvtree(args):
    src, dst = args
    shutil.move(src, dst)


def rmtree(src):
    shutil.rmtree(src)


def package(args):
    output = {}
    proc = subprocess.Popen(["jumpstart", "--noconfirm", "-Sy"] + args, stdout=subprocess.PIPE)
    while True:
        line = proc.stdout.readline().decode("utf-8")
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
                # The version can and often do contain aditional "-"
                pkg_version = "-".join(pkg_parts[1:]).strip()
                if pkg_name not in args:
                    return False, "No such package"
                output[pkg_name] = pkg_version
    return True, output


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


def git_latest_tag_guess(src):
    # Heuristic approach to finding the latest release for a project.
    if subprocess.check_call("cd %s; git fetch -t -q"%src, shell=True) != 0:
        return None, None
    try:
        ref_lines = subprocess.check_output("cd %s; git show-ref --tags"%src, shell=True).split("\n")
    except subprocess.CalledProcessError:
        return None, None
    tag_ref_lines = [line.split(" ")[1] for line in ref_lines if line != ""]
    version_tag_ref_lines = [(line.strip("refs/tags/"), line)
                             for line in tag_ref_lines
                             if re.match("^refs/tags/[0-9\.]+$", line) is not None]
    version_tag_ref_lines.sort(key=lambda (tag, ref): map(int, tag.split('.')))
    if len(version_tag_ref_lines) > 0:
        return version_tag_ref_lines[-1]
    return None, None


def git_checkout_tag(src, tag):
    log("checking out [%s] for [%s]"%(tag, src))
    return subprocess.check_call("cd %s; git checkout %s -q"%(src, tag), shell=True)


def sync_software_list(url, session_key, software_list):
    try:
        request = urllib2.Request(url, data=software_list.encode())
        request.add_header("Authorization", "Session-Key {session_key}".format(session_key=session_key))
        request.add_header("Content-Type", "application/json".format(session_key=session_key))
        response = urllib2.urlopen(request)
        if response.getcode() != httplib.OK:
            return None, {"code": DO_SYNC_SERVER_FAILED, "message": "software list not accepted"}
        is_software_list_synced_file = os.path.join(JSC_DIR, "recipe", "is-software-list-synced")
        if os.path.isfile(is_software_list_synced_file):
            with open(is_software_list_synced_file, "w") as f:
                f.truncate(0)
                f.write("1")
                f.flush()
        return None, None
    except urllib2.URLError as e:
        log(str(e))
        return None, {"code": DO_SYNC_HTTP_ERROR, "message": "an error occured communicating with the server"}


################################################################################
############################### Recipe functions ###############################
################################################################################

class RecipeRuntimeError(BaseException):
    pass


def recipe_fn(func):
    def fn(args):
        str_args = [elm.encode() if type(elm) is not str else elm for elm in args['args']]
        opt = docopt(func.__doc__, str_args)
        parsed_args = {k.lstrip("<").rstrip(">"): opt[k] for k in opt}
        try:
            return func(args['state'], parsed_args), None
        except RecipeRuntimeError as e:
            return None, {"code": RC_RECIPE_RUNTIME_ERROR, "message": str(e)}
    return fn


@recipe_fn
def rc_name(state, args):
    """
    Usage:
      name <name>
    """
    state["name"] = args["name"]
    return state


@recipe_fn
def rc_package(state, args):
    """
    Usage:
      package <pkg>...
    """
    success, installed_packages = package(args["pkg"])
    if not success:
        raise RecipeRuntimeError("package failed")
    if "package" not in state["software_list"]:
        state["software_list"]["package"] = {}
    for pkg in installed_packages.keys():
        state["software_list"]["package"][pkg] = {}
        state["software_list"]["package"][pkg]["version"] = installed_packages[pkg]
    return state


@recipe_fn
def rc_gd(state, args):
    """
    Usage:
      gd [--pkey=<pkey>] [--branch=<branch>] [--depth=<n>] <repo> <dst>
    """
    is_dev = state["is_dev"]
    repo = args["repo"]
    dst = args["dst"]
    depth = args["--depth"] if is_dev else 1
    branch = args["--branch"]
    pkey = args["--pkey"]
    success, git_res = git_clone(repo, dst, depth, branch, pkey)
    commit, ref = git_res
    if branch is None:
        # Try to find a tag to checkout.
        tag, n_ref = git_latest_tag_guess(dst)
        if tag is not None:
            if git_checkout_tag(dst, tag) != 0:
                raise RecipeRuntimeError("gd failed")
        if n_ref is not None:
            ref = n_ref
    if success:
        if "gd" not in state["software_list"]:
            state["software_list"]["gd"] = {}
        state["software_list"]["gd"][args["dst"]] = {
            "ref": ref,
            "commit": commit,
            "repo": args["repo"]
        }
        if not is_dev:
            rmtree(os.path.join(args["dst"], ".git"))
        return state
    raise RecipeRuntimeError("gd failed")


def rc_run(args):
    """
    Usage:
      run <cmd>...
    """
    state = args["state"]
    log("running command: {}".format(args["args"][0]))
    cmd = shlex.split(args["args"][0])
    subproc(cmd, NEW_RECIPE_SRC)
    return state, None


@recipe_fn
def rc_install(state, args):
    """
    Usage:
      install <src> <dst>
    """
    success, msg = install(args["src"], args["dst"])
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@recipe_fn
def rc_append(state, args):
    """
    Usage:
      append <file> <text>
    """
    success, msg = file_put(args["file"], args["text"])
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@recipe_fn
def rc_put(state, args):
    """
    Usage:
      put <file> <text>
    """
    success, msg = file_put(args["file"], args["text"], truncate=True)
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@recipe_fn
def rc_replace(state, args):
    """
    Usage:
      replace <file> <find> <replace>
    """
    success, msg = file_content_replace(args["file"], args["find"], args["replace"])
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@recipe_fn
def rc_insert(state, args):
    """
    Usage:
      insert <file> <find> <insert>
    """
    success, msg = file_content_replace(args["file"], args["find"], args["find"]+args["insert"], accurances=1)
    if not success:
        raise RecipeRuntimeError(msg)
    return state


@recipe_fn
def rc_rinsert(state, args):
    """
    Usage:
      rinsert <file> <find> <insert>
    """
    success, msg = file_content_replace(args["file"], args["find"], args["insert"]+args["find"], accurances=1, reverse=True)
    if not success:
        raise RecipeRuntimeError(msg)
    return state


################################################################################
############################### Regular commands ###############################
################################################################################

def do_assert_is_assembly(args):
    if not env_json()["ident"]["container"]["is_assembly"]:
        return False, {"code": DO_ASSERT_IS_ASSEMBLY_ERROR, "message": "You tried to connect to a non-assembly container"}
    return True, None


def do_backup(args):
    if args["new"]:
        backup_new()
        return None, None
    elif args["du"]:
        return backup_du(), None
    elif args["rm"]:
        backup_id = args["id"]
        backup_rm(backup_id)
        return None, None
    else:
        return backup_ls(), None


def do_clean(args):
    if args["--all"]:
        datasets = ["state", "code"]
    else:
        datasets = [item[2:] for item in args if args[item] is True]
    if len(datasets) == 0:
        datasets = ["code"]
    clean(datasets)
    return None, None


def do_check_init(args):
    for node in (JSC_DIR, BACKUPS_DIR):
        if not os.path.exists(node):
            return {"needs_init": True}, None
    if not os.path.exists(BACKUPS_SEQ_FILE_PATH):
        return {"needs_init": True}, None
    return {"needs_init": False}, None


def do_clone(args):
    # Low priotiry. Do later.
    return None, None


def do_deploy_reset_check(args):
    if not is_code_dir_clean():
        return None, {"code": DO_DEPLOY_NOT_CLEAN, "message": "Deploying a recipe requires your code base to be empty. Use the command clean and try again."}
    # From spec:
    # 1. Deleting .jsc/recipe and initializing .jsc/new-recipe. This folder
    #    should be deleted in init if it is found as it signifies a
    #    non-completed deploy.
    recipe_reset()
    return None, None


def do_deploy_read_new_recipe(args):
    # 2. The full recipe is cloned into .jsc/new-recipe/src if the path is a git repo.
    # The .git folder should not be included.
    path = args["path"]
    path_parts = path.split(":")
    if path_parts[0] == "github":
        # github repo id format.
        repo_url = "git://github/" + path_parts[0:].join(":") + ".git"
    else:
        repo_url = path
    if giturlparse.validate(repo_url):
        success, msg = git_clone(repo_url, "src", 1, None, None)
        if not success:
            return None, {"code": DO_DEPLOY_NO_NEWRECIPE, "message": "There is no recipe script to execute"}
        rmtree(os.path.join(NEW_RECIPE_SRC, ".git"))
        # 3. A disk sync is performed on /app/code.
        sync_dir("/")
    # 4. Syncing the jumpstart repo so it"s up to date.
    # Not needed, jumpstart -Sy is a better solution
    # 5. The recipe (.jsc/new-recipe/src/Jumpstart-Recipe) is executed by
    #    interpretation in jsc.
    recipe_script = read_new_recipe()
    if recipe_script is None:
        return None, {"code": DO_DEPLOY_NO_NEWRECIPE, "message": "There is no recipe script to execute"}
    return recipe_script, None


def do_deploy_finalize(args):
    is_dev_flag = "1" if args["--dev"] else "0"
    state = args["state"]
    rec_o = read_new_recipe()
    if rec_o is None:
        return None, {"code": DO_DEPLOY_NO_NEWRECIPE, "message": "There is no recipe script to execute"}
    # 6. A disk sync is performed on /app/code.
    sync_dir(CODE_DIR)
    # 7. The software list is exported as JSON to .jsc/new-recipe/software-list.
    software_list = json.dumps(state["software_list"])
    write_file([os.path.join(NEW_RECIPE_PATH, "software-list"), software_list])
    # 8. The file .jsc/new-recipe/is-dev is created with the content 1 when
    #    --dev is specified, otherwise 0.
    write_file([os.path.join(NEW_RECIPE_PATH, "is-dev"), is_dev_flag])
    # 9. The file .jsc/new-recipe/is-software-list-synced is created with
    #    the content 0.
    is_software_list_path = os.path.join(NEW_RECIPE_PATH, "is-software-list-synced")
    write_file([is_software_list_path, "0"])
    # 10. The file .jsc/new-recipe/deploy-time is created with the current
    #     time in rfc 3339 format with zero precision.
    remote_time = now3339()
    write_file([os.path.join(NEW_RECIPE_PATH, "deploy-time"), remote_time])
    # 11. A disk sync is performed on /app/code.
    sync_dir(CODE_DIR)
    # 12. Moving .jsc/new-recipe to .jsc/recipe.
    mvtree([NEW_RECIPE_PATH, RECIPE_PATH])
    # 13. A disk sync is performed on /app/code.
    sync_dir(CODE_DIR)
    # 14. A software list sync is performed.
    result, err = do_sync(None)
    if err is not None:
        log("softwarelist sync failed, please try to sync manually")
    # 15. Informing the user that the deploy was succesful.
    return None, None


def do_env(args):
    return env_json(), None


def do_init(args):
    for node in (JSC_DIR, BACKUPS_DIR):
        if not os.path.exists(node):
            touch_dir(node)
    if not os.path.exists(BACKUPS_SEQ_FILE_PATH):
        with open(BACKUPS_SEQ_FILE_PATH, "w+") as f:
            f.write("1")
    if os.path.exists(NEW_RECIPE_PATH):
        shutil.rmtree(NEW_RECIPE_PATH)
    if os.path.exists(NEW_BACKUP_DIR):
        shutil.rmtree(NEW_BACKUP_DIR)
    return None, None


def do_lock_session(lock_content):
    fd = os.open(LOCK_FILE, os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(LOCK_FILE, "w") as f:
            f.truncate(0)
            f.write(lock_content)
            f.flush()
        return None, None
    except IOError:
        with open(LOCK_FILE) as f:
            lock_content = json.loads(f.read())
        return None, {"code": "", "message": "File lock is already acquired by [{who}] since [{when}]".format(who=lock_content["hostname"], when=lock_content["unix_epoch"])}


def do_revert(args):
    backup_id = args["id"]
    if not is_code_dir_clean():
        return None, {"code": DO_REVERT_NOT_CLEAN, "message": "{code_dir} is not clean".format(code_dir=CODE_DIR)}
    reverted = False
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
                reverted = True
                break
    if reverted:
        is_software_list_synced_file = os.path.join(JSC_DIR, "recipe", "is-software-list-synced")
        if os.path.isfile(is_software_list_synced_file):
            with open(is_software_list_synced_file, "w") as f:
                f.truncate(0)
                f.write("0")
                f.flush()
        do_sync({})
        return None, None
    return None, {"code": DO_REVERT_INVALID_ID, "message": "backup id does not exist"}


def do_run(args):
    args = ["{code_dir}/init".format(code_dir=CODE_DIR)]
    subproc(args)
    return None, None


def do_sync(args):
    env = env_json()
    if "software_list_sync_url" in env["ident"]["container"]:
        url = env["ident"]["container"]["software_list_sync_url"]
        session_key = env["ident"]["container"]["session_key"]
        recipe_path = os.path.join(JSC_DIR, "recipe")
        if not os.path.isdir(recipe_path):
            software_list = json.dumps({"gd": {}, "package": {}})
            return sync_software_list(url, session_key, software_list)
        else:
            is_synced_file_path = os.path.join(recipe_path, "is-software-list-synced")
            with open(is_synced_file_path) as f:
                fc = f.read().strip()
                if int(fc) != 0:
                    return None, None
            with open("{recipe_path}/software-list".format(recipe_path=RECIPE_PATH)) as f:
                software_list = f.read().strip()
                return sync_software_list(url, session_key, software_list)
    return None, None


def do_status(args):
    output = {}
    code_total_size, code_used_size, code_percent_used = disk_usage_stats_pretty(CODE_DIR)
    output["code_usage"] = {"dir": CODE_DIR,
                            "used": code_used_size,
                            "total": code_total_size,
                            "percent_used": code_percent_used}
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
    output["recipe_name"] = recipe_name
    output["deploy_time"] = recipe_deploy_time
    backups_count = len(backup_ls())
    output["total_backups"] = backups_count
    state_total_size, state_used_size, state_percent_used = disk_usage_stats_pretty(STATE_DIR)
    output["state_usage"] = {"dir": STATE_DIR,
                             "used": state_used_size,
                             "total": state_total_size,
                             "percent_used": state_percent_used}
    try:
        with open(os.path.join(RECIPE_PATH, "software-list")) as f:
            software = json.loads(f.read())
            output["software"] = software
    except IOError:
        pass
    return output, None


def do_file_append(args):
    path = args["path"]
    content = base64.standard_b64decode(args["content"])
    with open(path, "ab+") as f:
        f.write(content)
    return None, None


def do_symlink(args):
    path = args["path"]
    target = args["target"]
    os.symlink(target, path)
    return None, None


def do_mkdir(args):
    path = args["path"]
    os.mkdir(path)
    return None, None


################################################################################
############################### Server main loop ###############################
################################################################################

def send_msg(msg):
    msg_str = json.dumps(msg) + "\n"
    sys.stdout.write(msg_str)
    sys.stdout.flush()


def execute(method, params, rpc_id):
    method_prefix = method[0:3]
    if method_prefix in ["do_", "rc_"] and method in globals().keys():
        f = globals()[method]
        result, error = f(params)
        send_msg({"id": rpc_id,
                  "result": result,
                  "error": error})
    else:
        send_msg({"id": rpc_id,
                  "result": None,
                  "error": {
                      "code": JSONRPC_METHOD_NOT_FOUND,
                      "message": "method not found"}})


def main(in_ch):
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(__version__)
        return
    channels = [in_ch]
    inbuf = ""
    while True:
        rl, _, xl = select.select(channels, [], channels)
        if len(xl) > 0:
            if in_ch in xl:
                # Stdin is closed
                exit(0)
        elif len(rl) > 0:
            new_data = in_ch.read()
            if len(new_data) == 0:
                # stdin is closed
                exit(0)
            inbuf += new_data
            if "\n" in inbuf:
                lines = inbuf.split("\n")
                # Last line is not complete.
                inbuf = "\n".join(lines[-1:])
                commands = lines[0:-1]
                for cmd_str in commands:
                    cmd_obj = json.loads(cmd_str)
                    if type(cmd_obj) != dict:
                        raise TypeError("Invalid json-rpc")
                    if "method" in cmd_obj:
                        execute(cmd_obj["method"], cmd_obj["params"], cmd_obj["id"])
                    else:
                        # This is probably a notification recieved for the
                        # previous command, could contain sensitive information.
                        pass


def test():
    # Set up at test script
    commands = [("do_status", {}),
                ("do_status", {}),
                ("do_backup", {"new": False, "du": False, "rm": False}),
#                ("do_backup", {"new": True, "du": False, "rm": False}),
                ("do_backup", {"new": False, "du": True, "rm": False}),
                ("do_env", {}),
                ("do_sync", {}),
                ("do_status", {"-v": False}),
                ("do_status", {"-v": True}),
                ("do_clean", {"--all": True}),
                ("do_deploy_reset_check", {}),
                ("do_file_append", {"path": NEW_RECIPE_SCRIPT,
                                    "content": base64.b64encode("TEST RECIPECONTENT\nASD")}),
                ("rc_put", {"state": {}, "args":{"file":"/app/code/test", "text":"test"}})
    ]

    with open("/tmp/mockinput", "w") as f:
        f.writelines([json.dumps({"method": c[0], "params": c[1], "id":34})+"\n" for c in commands])

    sys.stdin = open("/tmp/mockinput",'r')
    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    main(sys.stdin)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        test()
    else:
        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
        main(sys.stdin)
