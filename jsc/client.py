"""
Jumpstarter Console

Usage:
  jsc [--no-update] [options] SSH_USERNAME
  jsc [--no-update] api PATH ACCOUNT_ID

Arguments:
  SSH_USERNAME              Your SSH username
  PATH                      API Endpoint path
  ACCOUNT_ID                Your account id

Options:
  -h --help                 show this help message and exit
  -V --version              print version and quit
  -H --host=HOST            Hostname of ssh endpoint. [Default: ssh.jumpstarter.io]
  -p --port=N               Port of ssh endpoint. [Default: 22]
  -P --password             Prompt for password/passphrase
  -i --pkey=PKEY            SSH key file path
  -c --non-interactive=CMD  Execute single command
  --no-update               Do not check for updates of jsc
"""
import argparse
import base64
import choice
import cmd
from docopt import docopt, DocoptExit
import distutils.version as dist_version
import json
import os
import os.path
import platform
import shlex
import subprocess
import sys
import time
import getpass
import re
import requests
import fnmatch
import recipe
import giturlparse
import glob
from sshrpcutil import *

POSIX = os.name == "posix"
WINDOWS = os.name == "nt"

if POSIX:
    from sshjsonrpcposix import SshJsonRpcPosix as SshJsonRpc
elif WINDOWS:
    from sshjsonrpcwin import SshJsonRpcWin as SshJsonRpc
else:
    raise OSError("unknown OS")

try:
    import logger as log
except ImportError:
    from jsc import logger as log
try:
    import urllib2 as url
except ImportError:
    import urllib.request as url
try:
    from __init__ import __version__
except ImportError:
    from jsc import __version__



PYPI_JSON = "https://pypi.python.org/pypi/jsc/json"

MINUTE_S = 60
MIN_TIME_BETWEEN_UPDATES = MINUTE_S * 10

API_ENDPOINT = "https://jumpstarter.io/api/v0"

CODE_DIR = "/app/code"
STATE_DIR = "/app/state"
JSC_DIR = CODE_DIR + "/.jsc"
NEW_RECIPE_PATH = JSC_DIR + "/new-recipe"
RECIPE_PATH = JSC_DIR + "/recipe"
NEW_RECIPE_SRC = NEW_RECIPE_PATH + "/src"
NEW_RECIPE_SCRIPT = NEW_RECIPE_SRC + "/Jumpstart-Recipe"

LOCAL_JSC_PATH = os.path.expanduser("~/.jsc")
API_KEY_FILE = os.path.join(LOCAL_JSC_PATH, "api_key")


def get_filter(jscignore):
    root = os.path.dirname(jscignore)
    excluded_paths = [os.path.basename(jscignore), ".svn", ".git"]
    jscignore_lines = []
    if os.path.exists(jscignore):
        with open(jscignore) as fh:
            jscignore_lines = [x.rstrip() for x in fh.readlines() if not x.startswith("#") and len(x.rstrip()) > 0]
    excluded_paths = list(set(excluded_paths) | set(jscignore_lines))
    exclude_regxs = [re.compile(fnmatch.translate(os.path.join(root, x))) for x in excluded_paths]

    def should_skip(fn):
        for reobj in exclude_regxs:
            if reobj.match(fn) is not None:
                return True
        return False
    return should_skip


def rpc_put_recipe(rpc, src, dst=NEW_RECIPE_SRC, chunk_size=2**16, should_skip=lambda x: False):
    def put_file(fn_path_src, fn_path_dst):
        f_stat = os.stat(fn_path_src)
        bytes_left = f_stat.st_size
        log.white("putting local:{src} -> remote:{dst}".format(src=fn_path_src, dst=dst))
        with open(fn_path_src, "rb") as fo:
            while True:
                b64_content = base64.standard_b64encode(fo.read(chunk_size))
                rpc.do_file_append({"path": fn_path_dst, "content": b64_content})
                bytes_left -= chunk_size
                if bytes_left <= 0:
                    break

    if os.path.isfile(src) or os.path.islink(src):
        put_file(src, dst + "/Jumpstart-Recipe")
    elif os.path.isdir(src):
        for fn in os.listdir(src):
            fn_path_src = os.path.join(src, fn)
            if should_skip(fn_path_src):
                continue
            fn_path_dst = dst + "/" + fn
            if os.path.islink(fn_path_src):
                link_target = os.readlink(fn_path_src)
                rpc.do_symlink({"path": fn_path_dst, "target": link_target})
            elif os.path.isfile(fn_path_src):
                put_file(fn_path_src, fn_path_dst)
            elif os.path.isdir(fn_path_src):
                rpc.do_mkdir({"path": fn_path_dst})
                rpc_put_recipe(rpc, fn_path_src, fn_path_dst, chunk_size, should_skip)
    else:
        log.white("could not find recipe dir or file")


def docopt_cmd(func):
    """
    This decorator is used to simplify the try/except block and pass the result
    of the docopt parsing to the called action.
    """
    def fn(self, arg):
        try:
            opt = docopt(fn.__doc__, shlex.split(arg))
        except DocoptExit as e:
            # The DocoptExit is thrown when the args do not match.
            # We print a message to the user and the usage block.
            log.white('Invalid Command!')
            log.white(e)
            return
        except SystemExit:
            # The SystemExit exception prints the usage for --help
            # We do not need to do the print here.
            return
        return func(self, {k.lstrip("<").rstrip(">"): opt[k] for k in opt})
    fn.__name__ = func.__name__
    fn.__doc__ = func.__doc__
    fn.__dict__.update(func.__dict__)
    return fn


def fail(message):
    log.white(message)
    os._exit(1)


def stop(message):
    log.white(message)
    os._exit(0)


class Console(cmd.Cmd):
    def __init__(self, ssh_username, rpc, ssh_conn_str):
        cmd.Cmd.__init__(self)
        self._ssh_username = ssh_username
        self.prompt = "{ssh_username}> ".format(ssh_username=ssh_username)
        self._rpc = rpc
        self._ssh_conn_str = ssh_conn_str
        self._hist = []      # No history yet
        self._locals = {}      # Initialize execution namespace for user
        self._globals = {}

    def cmdloop_with_keyboard_interrupt(self, intro=""):
        print(intro)
        while True:
            try:
                self.cmdloop(intro="")
                break
            except KeyboardInterrupt:
                print("")

    @docopt_cmd
    def do_backup(self, args):
        """
        Usage:
          backup [ls]
          backup new
          backup du
          backup rm <id>

        Managing backups.

        Arguments:
          ls        List backups.
          new       Create a new backup.
          du        Like ls, but also shows size and disk space usage.
          rm        Removes backup with supplied id.
        """
        resp = self._rpc.do_backup(args)
        if resp is not None:
            for line in resp:
                log.white(line)

    @docopt_cmd
    def do_clean(self, args):
        """
        Usage:
          clean [--code|--state|--all]

        Cleans all user added directories and files in the given dataset.

        Options:
          --code            Cleans /app/code (default).
          --state           Cleans /app/state.
          --all             Cleans both /app/code and /app/state.
        """
        self._rpc.do_clean(args)

    @docopt_cmd
    def do_clone(self, args):
        """
        Usage:
          clone <id> [--pkey=<pkey>] [--code|--state]

          NOT IMPLEMENTED
        """
        # Low priotiry. Do later.
        pass

    @docopt_cmd
    def do_deploy(self, args):
        """
        Usage:
          deploy [--dev] <path>

        Deploys a recipe.

        Arguments:
          <path>        A path to the local recipe or a git repo which contains a recipe.

        Options:
          --dev         Uses git clone instead of git archive to keep .git.
                        This should NOT be done on an assembly that are going to be released.
        """
        try:
            self._rpc.do_deploy_reset_check()
            path = args['path']
            if not giturlparse.validate(path):
                if not path.startswith("/"):
                    path = os.path.join(os.getcwd(), path)
                rpc_put_recipe(self._rpc, path, should_skip=get_filter(os.path.join(path, ".jscignore")))
            rec = self._rpc.do_deploy_read_new_recipe({"path": path})
            state = recipe.run(self._rpc, rec, args['--dev'])
            args.update({"state": state})
            self._rpc.do_deploy_finalize(args)
        except SshRpcCallError as e:
            log.white(str(e))

    def complete_deploy(self, text, line, begidx, endidx):
        try:
            argv = shlex.split(line)
            cwd = os.getcwd()
            if len(argv) == 1 or (len(argv) == 2 and argv[1] == '--dev'):
                ret = os.listdir(cwd)
            else:
                parsed = docopt(self.do_deploy.__doc__, argv[1:])
                path = parsed['<path>']
                ret = []
                if path == '.':
                    ret.append('.')
                    ret.append('..')
                elif path == '..':
                    ret.append('..')
                elif os.path.isdir(path):
                    os.chdir(path)
                elif os.path.isdir(os.path.dirname(path)):
                    os.chdir(os.path.dirname(path))
                resolved = glob.glob(os.path.basename(path)+'*')
                if len(resolved) == 1 and resolved[0] == path:
                    pass
                else:
                    ret += resolved
                os.chdir(cwd)
            return map(lambda d: d + "/" if os.path.isdir(d) else d, ret)
        except Exception as e:
            # The DocoptExit is thrown when the args do not match.
            # We print a message to the user and the usage block.
            log.white('Invalid Command!')
            log.white(e)
            return []

    @docopt_cmd
    def do_env(self, args):
        """
        Usage:
          env

        Dumps /app/env.json to console.
        """
        env_json = self._rpc.do_env()
        log.white(json.dumps(env_json, sort_keys=True, indent=4, separators=(',', ': ')))

    @docopt_cmd
    def do_revert(self, args):
        """
        Usage:
          revert <id>

        Reverts a backup. This will destroy any changes you've made since backup.

        Arguments:
          <id>        Id of the backup to restore.
        """
        self._rpc.do_revert(args)
        log.white("revert of backup done!")

    @docopt_cmd
    def do_run(self, args):
        """
        Usage:
          run

        Runs /app/code/init.
        """
        self._rpc.do_run({})

    @docopt_cmd
    def do_ssh(self, args):
        """
        Usage:
          ssh

        Opens a new terminal and starts an SSH shell to this assembly.
        """
        if sys.platform.startswith("linux"):
            cmd_open = "xdg-open"
        elif sys.platform.startswith("darwin"):
            cmd_open = "open"
        else:
            log.white("Cannot start ssh shell since your platform [{platform}] is not supported.".format(platform=platform))
            return
        try:
            subprocess.check_call([cmd_open, "ssh://{uri}".format(uri=self._ssh_conn_str)])
        except subprocess.CalledProcessError:
            pass

    @docopt_cmd
    def do_sync(self, args):
        """
        Usage:
          sync

        Syncs software list so it becomes visable in the developer panel.
        """
        self._rpc.do_sync()
        log.white("sync done!")

    @docopt_cmd
    def do_status(self, args):
        """
        Usage:
          status [-v]

        Shows status about the container.
        Like disk usage, recipe deployed, backups and more.

        Options:
          -v         Be more verbose and show what jumpstart-packages are installed and
                     which git repos are checked out.
        """
        print_status(self._ssh_username, self._rpc.do_status(), self._rpc.do_env(), verbose=args['-v'])

    def do_hist(self, args):
        """Print a list of commands that have been entered"""
        log.white(self._hist)

    def do_exit(self, args):
        """Exits from the console"""
        return -1

    def do_quit(self, args):
        return self.do_exit(args)

    def do_EOF(self, args):
        """Exit on system end of file character"""
        return self.do_exit(args)

    def preloop(self):
        """Initialization before prompting user for commands.
           Despite the claims in the Cmd documentaion, Cmd.preloop() is not a stub.
        """
        cmd.Cmd.preloop(self)   # sets up command completion

    def postloop(self):
        """Take care of any unfinished business.
           Despite the claims in the Cmd documentaion, Cmd.postloop() is not a stub.
        """
        cmd.Cmd.postloop(self)   # Clean up command completion
        log.white("Goodbye!")

    def precmd(self, line):
        """ This method is called after the line has been input but before
            it has been interpreted. If you want to modifdy the input line
            before execution (for example, variable substitution) do it here.
        """
        if line not in ("EOF", "exit", "quit"):
            self._hist += [line.strip()]
        return line

    def postcmd(self, stop, line):
        """If you want to stop the console, return something that evaluates to true.
           If you want to do some post command processing, do it here.
        """
        return stop

    def emptyline(self):
        """Do nothing on empty input line"""
        pass

    def default(self, line):
        """Called on an input line when the command prefix is not recognized.
           In that case we execute the line as Python code.
        """
        log.white("unknown command: [{line}]".format(line=line))


def touch_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except IOError:
            pass


def update_self():
    # TODO: implement
    state_dir = os.path.expanduser("~/.jsc")
    touch_dir(state_dir)
    last_update_file = os.path.join(state_dir, "last_update")
    last_update_time = 0
    if os.path.exists(last_update_file):
        with open(last_update_file) as f:
            last_update_time = int(f.read().strip())
    epoch_time = int(time.time())
    if (epoch_time - last_update_time) > MIN_TIME_BETWEEN_UPDATES:
        log.white("Checking for updates of jsc")
        try:
            response = url.urlopen(PYPI_JSON)
            package_json = json.loads(response.read().decode())
            if dist_version.StrictVersion(package_json['info']['version']) > dist_version.StrictVersion(__version__):
                stop("There's a new version available, update with '# pip install -U jsc'")
            else:
                log.white("You're running the latest version of jsc")
            with open(last_update_file, "w") as f:
                f.truncate(0)
                f.write(str(epoch_time))
        except (url.URLError, ValueError):
            log.white("Could not check for updates, try '# pip install -U jsc'")


def print_status(assembly_id, status, env, verbose=False):
    email = env["ident"]["user"]["email"]
    name = env["ident"]["user"]["name"]
    if status['deploy_time'] is None:
        status['deploy_time'] = "<never>"
    log.white(
        u"\n".join([u"Jsc v{version} attached to assembly [{assembly_id}] by [{name} {email}]".format(version=__version__, assembly_id=assembly_id, name=name, email=email),
                   u"{dir}: {used} used of {total} ({percent_used} used)".format(**status['code_usage']),
                   u"    deployed recipe: {recipe_name}".format(**status),
                   u"        at {deploy_time}".format(**status),
                   u"    total backups: {total_backups}".format(**status),
                   u"{dir}: {used} used of {total} ({percent_used} used)".format(**status['state_usage'])]))
    if verbose:
        package_lines = []
        if "software" in status:
            if "package" in status["software"]:
                for pkg in status['software']["package"]:
                    pkg_kw = {"pkg": pkg,
                              "ver": status['software']["package"][pkg]["version"]}
                    package_lines.append("\t{pkg}: [{ver}]".format(**pkg_kw))
        gd_lines = []
        if "software" in status:
            if "gd" in status["software"]:
                for path in status['software']["gd"]:
                    gd_kw = {"ref": status['software']["gd"][path]["ref"],
                             "src": status['software']["gd"][path]["repo"],
                             "commit": status['software']["gd"][path]["commit"][0:8],
                             "path": path}
                    gd_lines.append("\t{path}: [{src}] [{ref}] [{commit}]".format(**gd_kw))
        all_lines = ["Deploiyed packages:"]
        all_lines += package_lines
        all_lines += ["Git deployed software:"]
        all_lines += gd_lines
        log.white("\n".join(all_lines))


def do_api(path, account_id):
    if os.path.isfile(API_KEY_FILE):
        with open(API_KEY_FILE) as f:
            key = f.read().strip()
        r = requests.get(API_ENDPOINT + path, auth=(account_id, key))
        if r.status_code == 200:
            log.ok("Success!")
            return 0
        else:
            if r.status_code == 401:
                log.err("Invalid account_id and/or api_key given")
            elif r.status_code == 403:
                log.err("You have no permission to use the that resource")
            elif r.status_code == 404:
                log.err("The endpoint could not be found")
            return 1
    else:
        log.err("Could not find API key file [{key_file}]".format(key_file=API_KEY_FILE))
        return 1


def main(args=None):
    try:
        arguments = docopt(__doc__, version=__version__)
        if not arguments['--no-update']:
            update_self()
        # WARNING: port does is not supported by remoto atm
        ssh_username = arguments['SSH_USERNAME']
        if arguments['api']:
            sys.exit(do_api(arguments['PATH'], arguments['ACCOUNT_ID']))
        host = arguments['--host']
        port = arguments['--port']
        ssh_conn_str = "{id}@{host}".format(id=ssh_username, host=host, port=port)
        # Init
        if arguments['--password']:
            password = getpass.getpass()
        else:
            password = None
        pkey = arguments['--pkey']
        rpc = None
        while rpc is None:
            try:
                rpc = SshJsonRpc(ssh_username, password, pkey, host=host, port=int(port))
                password = None
            except SshRpcKeyEncrypted:
                if pkey is not None:
                    password = getpass.getpass("Password for pubkey:")
                else:
                    fail("Encrypted keys require the -i and -P flags.")
            except SshRpcKeyNoAuthMethod:
                password = getpass.getpass("No keys, log in with password:")
                pkey = None
            except SshRpcKeyAuthFailed:
                fail("Authentication failed!")
        try:
            is_assembly = rpc.do_assert_is_assembly()
            if not is_assembly:
                fail("Container is not an assembly")
        except SshJsonRpc as e:
            fail(e)
        res = rpc.do_check_init()
        if res['needs_init']:
            confirm = choice.Binary('Assembly is not initialized, would you like to do it now?', False).ask()
            if not confirm:
                stop("You choose not to init the assembly, exiting...")
        rpc.do_init()
        lock_content_json = {
            "hostname": platform.node(),
            "unix_epoch": int(time.time()),
        }
        lock_content = json.dumps(lock_content_json)
        rpc.do_lock_session(lock_content)
        rpc.do_sync()
        console = Console(ssh_username, rpc, ssh_conn_str)
        if arguments['--non-interactive'] is None:
            # Print status on login
            print_status(ssh_username, rpc.do_status(), rpc.do_env())
            # Start console prompt
            console.cmdloop_with_keyboard_interrupt("Welcome to jsc!")
        else:
            def parse_noninteractive_cmds(line):
                cmd_line = line.replace(";", " ; ")
                cmd_arr = shlex.split(cmd_line)
                cmds = []
                while True:
                    if not len(cmd_arr):
                        break
                    cmd = cmd_arr[0]
                    cmd_arr = cmd_arr[1:]
                    params = []
                    while len(cmd_arr) and cmd_arr[0] != ";":
                        params.append(cmd_arr[0])
                        cmd_arr = cmd_arr[1:]
                    cmd_arr = cmd_arr[1:]
                    cmds.append({"cmd": cmd, "params": " ".join(params)})
                return cmds
            for cmd in parse_noninteractive_cmds(arguments['--non-interactive']):
                try:
                    f = getattr(console, "do_{cmd}".format(cmd=cmd["cmd"]))
                    f(cmd["params"])
                except AttributeError:
                    fail("Invalid command [{cmd}]".format(cmd="{} {}".format(cmd["cmd"], cmd["params"])))
    except KeyboardInterrupt:
        os._exit(1)
    os._exit(0)


if __name__ == '__main__':
    main()
