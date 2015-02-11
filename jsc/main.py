import atexit
import argparse
import choice
import cmd
import distutils.version as dist_version
from docopt import docopt, DocoptExit
import json
import logging
import multiprocessing
import os
import os.path
import platform
import remoto
import remoto.process
import execnet
import shlex
import signal
import subprocess
import sys
import time
try:
    import recipe
    import logger as log
    import container
except ImportError:
    from jsc import recipe, logger as log, container
try:
    import urllib2 as url
except ImportError:
    import urllib.request as url


import pkg_resources  # part of setuptools
__version__ = pkg_resources.require("jsc")[0].version


DEFAULT_SSH_HOST = "ssh.jumpstarter.io"
DEFAULT_SSH_PORT = "22"

PYPI_JSON = "https://pypi.python.org/pypi/jsc/json"

MINUTE_S = 60
MIN_TIME_BETWEEN_UPDATES = MINUTE_S * 10


CODE_DIR = "/app/code"
STATE_DIR = "/app/state"
JSC_DIR = os.path.join(CODE_DIR, ".jsc")
NEW_RECIPE_PATH = os.path.join(JSC_DIR, "new-recipe")
RECIPE_PATH = os.path.join(JSC_DIR, "recipe")
NEW_RECIPE_SRC = os.path.join(NEW_RECIPE_PATH, "src")
NEW_RECIPE_SCRIPT = os.path.join(NEW_RECIPE_SRC, "Jumpstart-Recipe")


def fail(message):
    log.err(message)
    os._exit(1)


def stop(message):
    log.warn(message)
    os._exit(0)


def rsync(conn, src, dst):
    class JSRSync(execnet.RSync):
        def _report_send_file(self, gateway, modified_rel_path):
            log.info("syncing file: %s" % modified_rel_path)
    sync = JSRSync(src)
    sync.add_target(conn.gateway, dst)
    sync.send()


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
            log.err('Invalid Command!')
            log.err(e)
            return
        except SystemExit:
            # The SystemExit exception prints the usage for --help
            # We do not need to do the print here.
            return
        return func(self, opt)
    fn.__name__ = func.__name__
    fn.__doc__ = func.__doc__
    fn.__dict__.update(func.__dict__)
    return fn


class Console(cmd.Cmd):
    def __init__(self, ssh_username, remoto_exec, ssh_conn_str):
        cmd.Cmd.__init__(self)
        self.prompt = "{ssh_username}> ".format(ssh_username=ssh_username)
        self._remoto_exec = remoto_exec
        self._ssh_conn_str = ssh_conn_str

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
        if args['new']:
            self._remoto_exec.backup_new()
            log.ok("Backup done!")
        elif args['du']:
            for backup in self._remoto_exec.backup_du():
                log.info(backup)
        elif args['rm']:
            backup_id = args['<id>']
            self._remoto_exec.backup_rm(backup_id)
            log.info("Removal of backup [{backup_id}] done!".format(backup_id=backup_id))
        else:
            for backup in self._remoto_exec.backup_ls():
                log.info(backup)

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
        if args['--all']:
            datasets = ['state', 'code']
        else:
            datasets = [item[2:] for item in args if args[item] is True]
        if len(datasets) == 0:
            datasets = ['code']
        self._remoto_exec.clean(datasets)

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
        if not self._remoto_exec.is_code_dir_clean():
            log.err("deploy requires clean /app/code")
            return
        path = args["<path>"]
        is_dev = "1" if args['--dev'] else "0"
        # From spec:
        # 1. Deleting .jsc/recipe and initializing .jsc/new-recipe. This folder
        #    should be deleted in init if it is found as it signifies a
        #    non-completed deploy.
        self._remoto_exec.recipe_reset()
        # 2. The full recipe is copied over into .jsc/new-recipe/src. If the
        #    source is a git repo the .git folder should not be included.
        # Depending on what path is we need to do different things
        # 1 Path to a local file it is a recipe, copy and execute.
        # 2 Path to a local dir it contains a recipe and resource for the recipe.
        #   Copy over all and execute.
        # 3 URL to a git repository, clone in and execute recipe.
        # 4 "GitHub ID" Clone and execute.
        if os.path.isfile(path):
            self._remoto_exec.rsync(path, NEW_RECIPE_SCRIPT)
        elif os.path.isdir(path):
            self._remoto_exec.rsync(path, NEW_RECIPE_SRC)
        else:
            path_parts = path.split(":")
            if path_parts[0] == "github":
                # github repo id format.
                repo_url = "git://github/" + path_parts[0:].join(":") + ".git"
            else:
                repo_url = path
            self._remoto_exec.git_clone(repo_url, "src", 1, None, None)
            self._remoto_exec.rmtree(os.path.join(NEW_RECIPE_SRC, ".git"))
        # 3. A disk sync is performed on /app/code.
        self._remoto_exec.sync_dir('/')
        # 4. Syncing the jumpstart repo so it's up to date.
        # Not needed, jumpstart -Sy is a better solution
        # 5. The recipe (.jsc/new-recipe/src/Jumpstart-Recipe) is executed by
        #    interpretation in jsc.
        recipe_script = self._remoto_exec.read_new_recipe()
        rec_o = recipe.run(self._remoto_exec, recipe_script, is_dev)
        if rec_o is None:
            log.err("Deploy unsuccessful")
            return
        # 6. A disk sync is performed on /app/code.
        self._remoto_exec.sync_dir(CODE_DIR)
        # 7. The software list is exported as JSON to .jsc/new-recipe/software-list.
        software_list = json.dumps(rec_o["software_list"], sort_keys=True, indent=4, separators=(',', ': '))
        self._remoto_exec.write_file([os.path.join(NEW_RECIPE_PATH, "software-list"), software_list])
        # 8. The file .jsc/new-recipe/is-dev is created with the content 1 when
        #    --dev is specified, otherwise 0.
        self._remoto_exec.write_file([os.path.join(NEW_RECIPE_PATH, "is-dev"), is_dev])
        # 9. The file .jsc/new-recipe/is-software-list-synced is created with
        #    the content 0.
        is_software_list_path = os.path.join(NEW_RECIPE_PATH, "is-software-list-synced")
        self._remoto_exec.write_file([is_software_list_path, "0"])
        # 10. The file .jsc/new-recipe/deploy-time is created with the current
        #     time in rfc 3339 format with zero precision.
        remote_time = self._remoto_exec.now3339()
        self._remoto_exec.write_file([os.path.join(NEW_RECIPE_PATH, "deploy-time"), remote_time])
        # 11. A disk sync is performed on /app/code.
        self._remoto_exec.sync_dir(CODE_DIR)
        # 12. Moving .jsc/new-recipe to .jsc/recipe.
        self._remoto_exec.mvtree([NEW_RECIPE_PATH, RECIPE_PATH])
        # 13. A disk sync is performed on /app/code.
        self._remoto_exec.sync_dir(CODE_DIR)
        # 14. A software list sync is performed.
        success, msg = self._remoto_exec.sync()
        if not success:
            log.warn("Sync failed because of: [{msg}]. Try running clean manually.".format(msg=msg))
        # 15. Informing the user that the deploy was succesful.
        log.info("Deploy successful!")

    @docopt_cmd
    def do_env(self, args):
        """
        Usage:
          env

        Dumps /app/env.json to console.
        """
        env_json = self._remoto_exec.env_json()
        log.info(json.dumps(env_json, sort_keys=True, indent=4, separators=(',', ': ')))

    @docopt_cmd
    def do_revert(self, args):
        """
        Usage:
          revert <id>

        Reverts a backup. This will destroy any changes you've made since backup.

        Arguments:
          <id>        Id of the backup to restore.
        """
        backup_id = args['<id>']
        success, msg = self._remoto_exec.revert(backup_id)
        if not success:
            log.err(msg)
            return
        log.info("revert of backup [{backup_id}] done!".format(backup_id=backup_id))

    @docopt_cmd
    def do_run(self, args):
        """
        Usage:
          run

        Runs /app/code/init.
        """
        try:
            success, msg = self._remoto_exec.run()
            if not success:
                log.err(msg)
        except KeyboardInterrupt:
            # give the prompt a fresh new line instead of ^C
            print("")

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
            log.err("Cannot start ssh shell since your platform [{platform}] is not supported.".format(platform=platform))
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
        success, msg = self._remoto_exec.sync()
        if not success:
            log.warn("Sync failed because of: [{msg}]. Please contact support if problem remains.".format(msg=msg))
        else:
            log.ok("Sync successful!")

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
        verbose = args["-v"]
        self._remoto_exec.status_short(__version__, verbose)

    def do_hist(self, args):
        """Print a list of commands that have been entered"""
        log.info(self._hist)

    def do_exit(self, args):
        """Exits from the console"""
        log.info(args)
        return -1

    def do_quit(self, args):
        self.do_exit("")

    def do_EOF(self, args):
        """Exit on system end of file character"""
        return self.do_exit(args)

    def preloop(self):
        """Initialization before prompting user for commands.
           Despite the claims in the Cmd documentaion, Cmd.preloop() is not a stub.
        """
        cmd.Cmd.preloop(self)   ## sets up command completion
        self._hist    = []      ## No history yet
        self._locals  = {}      ## Initialize execution namespace for user
        self._globals = {}

    def postloop(self):
        """Take care of any unfinished business.
           Despite the claims in the Cmd documentaion, Cmd.postloop() is not a stub.
        """
        cmd.Cmd.postloop(self)   ## Clean up command completion
        log.info("Goodbye!")

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
        log.info("unknown command: [{line}]".format(line=line))


def touch_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except FileExistsError:
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
        log.info("Checking for updates of jsc")
        try:
            response = url.urlopen(PYPI_JSON)
            package_json = json.loads(response.read().decode())
            if dist_version.StrictVersion(package_json['info']['version']) > dist_version.StrictVersion(__version__):
                stop("There's a new version available, update with '# pip install -U jsc'")
            else:
                log.info("You're running the latest version of jsc")
            with open(last_update_file, "w") as f:
                f.truncate(0)
                f.write(str(epoch_time))
        except (url.URLError, ValueError):
            log.warn("Could not check for updates, try '# pip install -U jsc'")


def parse_args_main(args):
    parser = argparse.ArgumentParser(description='Jumpstarter Console')
    parser.add_argument('-V', '--version', action='version', version='{version}'.format(version=__version__))
    parser.add_argument('-H', '--host', action='store', default=DEFAULT_SSH_HOST, help="Hostname of ssh endpoint. Default: ssh.jumpstarter.io")
    parser.add_argument('-p', '--port', action='store', default=DEFAULT_SSH_PORT, help="Port of ssh endpoint. Default: 22")
    parser.add_argument('--no-update', action='store_true', help="Do not check for updates of jsc")
    parser.add_argument('ssh_username', help="SSH username")
    return parser.parse_args(args)


class RemotoProcessCom():
    def __init__(self, q_in, q_out):
        self._q_in = q_in
        self._q_out = q_out

    def _caller(self, func_identifier):
        def func(*args, **kwargs):
            self._q_in.put((func_identifier, args, kwargs))
            return self._q_out.get()
        return func

    def __getattr__(self, item):
        if hasattr(container, item):
            return self._caller(item)
        raise AttributeError("'{class_name}' object has no attribute '{item}'".format(class_name=self.__class__.__name__, item=item))

    def rsync(self, src, dst):
        self._q_in.put(('rsync', [src, dst], {}))
        return self._q_out.get()


def remoto_process(ssh_conn_str, q_in, q_out):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        conn = remoto.Connection(ssh_conn_str, logger=logging.getLogger(ssh_conn_str), threads=0)
    except KeyboardInterrupt:
        q_out.put((False, "connection canceled by user"))
        return
    except execnet.gateway_bootstrap.HostNotFound:
        q_out.put((False, "could not connect"))
        return
    q_out.put((True, None))
    conn.import_module(container)
    while True:
        func_identifier, args, kwargs = q_in.get()
        if func_identifier == "rsync":
            q_out.put(rsync(conn, *args, **kwargs))
            continue
        func = getattr(conn.remote_module, func_identifier)
        q_out.put(func("remoto", [args, kwargs]))


def kill_process(process):
    process.terminate()


def process_signal(process):
    def signal_handler(signum, frame):
        if signum == signal.SIGINT:
            kill_process(process)
            os._exit(1)
    return signal_handler


def main(args=None):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        parsed = parse_args_main(args)
        if not parsed.no_update:
            update_self()
        # WARNING: port does is not supported by remoto atm
        ssh_username = parsed.ssh_username
        ssh_conn_str = "{id}@{host}".format(id=ssh_username, host=parsed.host, port=parsed.port)
        # Init
        remoto_q_in = multiprocessing.Queue()
        remoto_q_out = multiprocessing.Queue()
        remoto_t = multiprocessing.Process(target=remoto_process, args=(ssh_conn_str, remoto_q_in, remoto_q_out))
        remoto_t.start()
        signal.signal(signal.SIGINT, process_signal(remoto_t))
        # kill the remoto process if main process dies
        atexit.register(kill_process, remoto_t)
        try:
            success, msg = remoto_q_out.get()
        except KeyboardInterrupt:
            success, msg = remoto_q_out.get()
        if not success:
            fail(msg)
        remoto_exec = RemotoProcessCom(remoto_q_in, remoto_q_out)
        is_assembly, msg = remoto_exec.assert_is_assembly()
        if not is_assembly:
            fail(msg)
        success, _ = remoto_exec.check_init()
        if not success:
            confirm = choice.Binary('Assembly is not initialized, would you like to do it now?', False).ask()
            if not confirm:
                stop("You choose not to init the assembly, exiting...")
        remoto_exec.init()
        lock_content_json = {
            "hostname": platform.node(),
            "unix_epoch": int(time.time()),
        }
        lock_content = json.dumps(lock_content_json)
        success, msg = remoto_exec.lock_session(lock_content)
        if not success:
            fail(msg)
        # TODO: clean incomplete backup stuff
        success, msg = remoto_exec.sync()
        if not success:
            log.warn("Sync failed because of: [{msg}]. Try running clean manually.".format(msg=msg))
        if not success:
            log.err(msg)
        # Print status on login
        remoto_exec.status_short(__version__, False)
        # Start console prompt
        console = Console(ssh_username, remoto_exec, ssh_conn_str)
        console.cmdloop("Welcome to jsc!")
    except KeyboardInterrupt:
        pass
    kill_process(remoto_t)
    os._exit(1)


if __name__ == '__main__':
    main()
    pass
