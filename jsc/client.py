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
import paramiko
import getpass
import threading
import select
from gevent.fileobject import FileObject
import re
import fnmatch
import recipe
import server_updater
import inspect
import giturlparse

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


DEFAULT_SSH_HOST = "ssh.jumpstarter.io"
DEFAULT_SSH_PORT = 22

PYPI_JSON = "https://pypi.python.org/pypi/jsc/json"

MINUTE_S = 60
MIN_TIME_BETWEEN_UPDATES = MINUTE_S * 10

CODE_DIR = "/app/code"
STATE_DIR = "/app/state"
JSC_DIR = CODE_DIR + "/.jsc"
NEW_RECIPE_PATH = JSC_DIR + "/new-recipe"
RECIPE_PATH = JSC_DIR + "/recipe"
NEW_RECIPE_SRC = NEW_RECIPE_PATH + "/src"
NEW_RECIPE_SCRIPT = NEW_RECIPE_SRC + "/Jumpstart-Recipe"


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

        Deployes a user writter recipe.

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
        elif sys.platform.startswith("win32"):
            cmd_open = "start"
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


class SshRpcCallError(BaseException):
    pass


class SshRpcError(BaseException):
    pass


class SshRpcKeyEncrypted(SshRpcError):
    pass


class SshRpcKeyNoAuthMethod(SshRpcError):
    pass


class SshRpcKeyAuthFailed(SshRpcError):
    pass


class SshJsonRpc():
    rpc_id = 0

    def __init__(self, username, password=None, key_filename=None, host=DEFAULT_SSH_HOST, port=DEFAULT_SSH_PORT):
        pkey = os.path.expanduser(key_filename) if key_filename is not None else None
        self.send_lock = threading.Lock()
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.load_system_host_keys()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kw = {"username": username,
                      "compress":True,
                      "look_for_keys": True}
        if password is not None:
            connect_kw["password"] = password
            connect_kw["look_for_keys"] = False
        if key_filename is not None:
            connect_kw["key_filename"] = key_filename
            connect_kw["look_for_keys"] = False
        try:
            self.ssh_client.connect(host, port, **connect_kw )
        except paramiko.ssh_exception.PasswordRequiredException as e:
            if e.message == "Private key file is encrypted":
                raise SshRpcKeyEncrypted()
            raise SshRpcError()
        except paramiko.ssh_exception.SSHException as e:
            if e.message == "No authentication methods available":
                raise SshRpcKeyNoAuthMethod()
            if e.message == "Authentication failed.":
                raise SshRpcKeyAuthFailed()
            raise SshRpcError()
        self.ssh_transport = self.ssh_client.get_transport()
        self.ssh_transport.set_keepalive(30)
        self.ssh_channel = None
        self._server_update()

    def _server_update(self):
        channel = self.ssh_transport.open_session()
        channel.setblocking(0)
        # TODO: call server binary
        src = (repr(inspect.getsource(server_updater))+"\n").encode()
        channel.exec_command("env JSC_CLIENT_VERSION={version} python2 -c \"import sys;exec(eval(sys.stdin.readline()))\"".format(version=__version__))
        channel.sendall(src)
        while True:
            if channel.exit_status_ready():
                break
            rl, wl, xl = select.select([channel], [], [])
            for _ in rl:
                while channel.recv_stderr_ready():
                    log.white(channel.recv_stderr(4096).decode())
                while channel.recv_ready():
                    log.white(channel.recv(4096).decode())

    def _open_channel(self):
        self.ssh_channel = self.ssh_transport.open_session()
        self.ssh_channel.setblocking(0)
        # TODO: call server binary
        self.ssh_channel.exec_command('/tmp/server')
        self.stdout_file = self.ssh_channel.makefile("r", 0)

    def stdin_g(self):
        stdinFile = FileObject(sys.stdin)
        for line in stdinFile:
            self._notify(stdin=line)

    def _sendall(self, rpc):
        if self.ssh_channel is None or self.ssh_channel.exit_status_ready():
            self._open_channel()
        with self.send_lock:
            self.ssh_channel.sendall("{rpc}\n".format(rpc=rpc))

    def call(self, method, args):
        rpc_cmd = self.rpc(method, args)
        self._sendall(rpc_cmd)
        recv_buf = ""
        try:
            while True:
                rl, _, xl = select.select([self.ssh_channel], [], [])
                for _ in rl:
                    if self.ssh_channel.recv_ready():
                        new_data = self.ssh_channel.recv(4096)
                        recv_buf += new_data
                        if "\n" in recv_buf:
                            lines = recv_buf.split("\n")
                            # Last line is either not complete or empty string. ("x\nnot compl".split("\n") => ['x', 'not compl'] or "x\n".split("\n") => ['x', ''])
                            # so we put it back in recv_buf for next iteration
                            recv_buf = lines.pop()
                            for line in lines:
                                resp = json.loads(line)
                                if "stdout" in resp:
                                    log.white(resp["stdout"].strip(), f=sys.stdout)
                                elif "stderr" in resp:
                                    log.white(resp["stderr"], f=sys.stderr)
                                elif "result" in resp:
                                    if resp['error'] is not None:
                                        raise SshRpcCallError(resp['error']['message'])
                                    return resp["result"]
                    if self.ssh_channel.recv_stderr_ready():
                        log.white("{}".format(self.ssh_channel.recv_stderr(4096)))
                    if self.ssh_channel.exit_status_ready():
                        raise SshRpcError()
        except (KeyboardInterrupt, SshRpcError):
            # stdin_g.kill()
            self.ssh_channel.shutdown(2)
            self.ssh_channel = None
            raise KeyboardInterrupt()

    def _notify(self, **kwargs):
        if len(kwargs) > 0:
            notify_dict = {"id": None}
            notify_dict.update(kwargs)
            rpc_json = json.dumps(notify_dict)
            self._sendall(rpc_json)

    def rpc(self, method, params):
        rpc_json = json.dumps({"id": SshJsonRpc.rpc_id,
                               "method": method,
                               "params": params})
        SshJsonRpc.rpc_id += 1
        return rpc_json

    def __getattr__(self, item):
        def wrapper(args=None):
            return self.call(item, args)
        return wrapper


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


def parse_args_main(args):
    parser = argparse.ArgumentParser(description='Jumpstarter Console')
    parser.add_argument('-V', '--version', action='version', version='{version}'.format(version=__version__))
    parser.add_argument('-H', '--host', action='store', default=DEFAULT_SSH_HOST, help="Hostname of ssh endpoint. Default: ssh.jumpstarter.io")
    parser.add_argument('-p', '--port', action='store', default=DEFAULT_SSH_PORT, help="Port of ssh endpoint. Default: 22")
    parser.add_argument('-P', '--password', action='store_true', default=False, help="Prompt password input")
    parser.add_argument('-i', '--pkey', action='store', default=None, help="SSH Key file path")
    parser.add_argument('--no-update', action='store_true', help="Do not check for updates of jsc")
    parser.add_argument('ssh_username', help="SSH username")
    return parser.parse_args(args)


def print_status(assembly_id, status, env, verbose=False):
    email = env["ident"]["user"]["email"]
    name = env["ident"]["user"]["name"]
    if status['deploy_time'] is None:
        status['deploy_time'] = "<never>"
    log.white(
        "\n".join(["Jsc v{version} attached to assembly [{assembly_id}] by [{name} {email}]".format(version=__version__, assembly_id=assembly_id, name=name, email=email),
                   "{dir}: {used} used of {total} ({percent_used} % used)".format(**status['code_usage']),
                   "    deployed recipe: {recipe_name}".format(**status),
                   "        at {deploy_time}".format(**status),
                   "    total backups: {total_backups}".format(**status),
                   "{dir}: {used} used of {total} ({percent_used} % used)".format(**status['state_usage'])]))
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


def main(args=None):
    try:
        parsed = parse_args_main(args)
        if not parsed.no_update:
            update_self()
        # WARNING: port does is not supported by remoto atm
        ssh_username = parsed.ssh_username
        ssh_conn_str = "{id}@{host}".format(id=ssh_username, host=parsed.host, port=parsed.port)
        # Init
        if parsed.password:
            password = getpass.getpass()
        else:
            password = None
        pkey = parsed.pkey
        rpc = None
        while rpc is None:
            try:
                rpc = SshJsonRpc(ssh_username, password, parsed.pkey, host=parsed.host, port=int(parsed.port))
            except SshRpcKeyEncrypted:
                password = getpass.getpass("Password for pubkey:")
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
        # Print status on login
        print_status(parsed.ssh_username, rpc.do_status(), rpc.do_env())
        # Start console prompt
        console = Console(ssh_username, rpc, ssh_conn_str)
        console.cmdloop_with_keyboard_interrupt("Welcome to jsc!")
    except KeyboardInterrupt:
        os._exit(1)
    os._exit(0)


if __name__ == '__main__':
    main()
