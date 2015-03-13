import unittest
import os.path
import os
import shutil
import json
import platform
import time
import subprocess
import pwd

import jsc.client
import jsc.server


CODE_DIR = jsc.server.CODE_DIR
STATE_DIR = jsc.server.STATE_DIR
JSC_DIR = jsc.server.JSC_DIR
BACKUPS_DIR = jsc.server.BACKUPS_DIR
BACKUPS_SEQ_FILE_PATH = jsc.server.BACKUPS_SEQ_FILE_PATH
NEW_BACKUP_DIR = jsc.server.NEW_BACKUP_DIR
LOCK_FILE = jsc.server.LOCK_FILE
RECIPE_PATH = jsc.server.RECIPE_PATH
NEW_RECIPE_PATH = jsc.server.NEW_RECIPE_PATH
NEW_RECIPE_SRC = jsc.server.NEW_RECIPE_SRC
NEW_RECIPE_SCRIPT = jsc.server.NEW_RECIPE_SCRIPT

ENV_JSON = "/app/env.json"


def touch_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except IOError:
            pass


def touch_file(file_path):
    with open(file_path, "wb+") as f:
        f.write("")


def add_garbage():
    touch_dir(os.path.join(CODE_DIR, "garb"))
    touch_dir(os.path.join(CODE_DIR, ".pacman", "db"))
    touch_file(os.path.join(CODE_DIR, ".pacman", "garb"))


class TestClient(unittest.TestCase):
    def setUp(self):
        cuser = pwd.getpwuid(os.getuid()).pw_name
        subprocess.check_call("sudo mkdir -m 777 -p {}".format(CODE_DIR), shell=True)
        subprocess.check_call("sudo mkdir -m 777 -p {}".format(STATE_DIR), shell=True)
        subprocess.check_call("sudo chown -R {cuser}:{cuser} /app".format(cuser=cuser), shell=True)
        self._rpc = jsc.client.SshJsonRpc(cuser, key_filename=os.path.expanduser("~/.ssh/id_rsa"), host="localhost")
        self._rpc.do_init()

    def tearDown(self):
        subprocess.check_call("sudo rm -rf {}".format("/app"), shell=True)

    def do_clean_all(self):
        self._rpc.do_clean({
            "--all": True,
            "--code": False,
            "--state": False
        })

    def add_env(self, env_file):
        shutil.copy2(os.path.join("travis", env_file), ENV_JSON)

    def test_do_clean(self):
        def is_code_dir_clean():
            for node in os.listdir(CODE_DIR):
                if node not in ("lost+found", ".jsc", ".pacman", ".config"):
                    return False
            return True

        add_garbage()
        self._rpc.do_clean({
            "--all": False,
            "--code": False,
            "--state": False
        })
        assert is_code_dir_clean()
        add_garbage()
        self._rpc.do_clean({
            "--all": True,
            "--code": False,
            "--state": False
        })
        assert is_code_dir_clean()
        add_garbage()
        self._rpc.do_clean({
            "--all": False,
            "--code": True,
            "--state": False
        })
        assert is_code_dir_clean()
        self._rpc.do_clean({
            "--all": False,
            "--code": False,
            "--state": True
        })

    def test_do_assert_is_assembly(self):
        self.add_env("env_app.json")
        try:
            self._rpc.do_assert_is_assembly()
            # shouldn't reach this
            assert False
        except jsc.client.SshRpcCallError:
            pass
        self.add_env("env_assembly.json")
        assert self._rpc.do_assert_is_assembly()

    def test_do_backup(self):
        print(os.listdir(CODE_DIR))
        add_garbage()
        assert isinstance(self._rpc.do_backup({
            "new": False,
            "du": False,
            "ls": False,
            "rm": False
        }), list)
        assert isinstance(self._rpc.do_backup({
            "new": False,
            "du": True,
            "ls": False,
            "rm": False
        }), list)
        assert self._rpc.do_backup({
            "new": True,
            "du": False,
            "ls": False,
            "rm": False
        }) is None
        assert isinstance(self._rpc.do_backup({
            "new": False,
            "du": False,
            "ls": False,
            "rm": False
        }), list)
        assert isinstance(self._rpc.do_backup({
            "new": False,
            "du": True,
            "ls": False,
            "rm": False
        }), list)
        for b_id in self._rpc.do_backup({
            "new": False,
            "du": False,
            "ls": True,
            "rm": False
        }):
            self._rpc.do_backup({
                "new": False,
                "du": False,
                "ls": False,
                "rm": True,
                "id": b_id
            })

    def test_do_deploy_reset_check(self):
        assert self._rpc.do_deploy_reset_check() is None
        add_garbage()
        try:
            print(self._rpc.do_deploy_reset_check())
            # shouldn't reach this
            assert False
        except jsc.client.SshRpcCallError as e:
            pass

    def test_do_check_init(self):
        for d in (BACKUPS_DIR, JSC_DIR, BACKUPS_SEQ_FILE_PATH):
            if os.path.isfile(d):
                os.remove(d)
            else:
                shutil.rmtree(d)
            assert self._rpc.do_check_init()['needs_init'] is True
            self._rpc.do_init()
        assert self._rpc.do_check_init()['needs_init'] is False

    def test_do_env(self):
        # all about it returning an env
        self.add_env("env_app.json")
        assert isinstance(self._rpc.do_env(), dict)

    def test_do_lock_session(self):
        lock_content_json = {
            "hostname": platform.node(),
            "unix_epoch": int(time.time()),
        }
        lock_content = json.dumps(lock_content_json)
        assert self._rpc.do_lock_session(lock_content) is None
        try:
            self._rpc.do_lock_session(lock_content)
            # shouldn't reach this
            assert False
        except jsc.client.SshRpcCallError:
            pass

    def test_do_status(self):
        # test for crashes
        self._rpc.do_status()

    def test_do_symlink(self):
        self._rpc.do_symlink({"path": "/app/code/sym_tmp", "target": "/tmp"})

    def test_do_mkdir(self):
        self._rpc.do_mkdir({"path": "/app/code/new_dir"})

    def test_do_file_append(self):
        self._rpc.do_file_append({"path": "/app/code/new_file", "content": "content1"})
        self._rpc.do_file_append({"path": "/app/code/new_file", "content": "content2"})
