import unittest
import os.path
import os
import shutil
import json
import platform
import time
import subprocess
import pwd
import pyparsing
import signal

import fake_sync_endpoint

import jsc.client
import jsc.server
import jsc.recipe
import jsc.rparser as rp


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
    touch_dir(os.path.join(CODE_DIR, ".jsc", "recipe"))


def cmp_lists(l1, l2):
    return not set(l1) ^ set(l2)


class FakeEndpointConn():
    def __init__(self):
        self.fake_sync_endpoint = None

    def __del__(self):
        self.stop()

    def start(self):
        if self.fake_sync_endpoint is None:
            fake_sync_endpoint_bin = os.path.join(os.path.dirname(__file__), "fake_sync_endpoint.py")
            self.fake_sync_endpoint = subprocess.Popen("{fake_sync_endpoint_bin}".format(fake_sync_endpoint_bin=fake_sync_endpoint_bin), shell=True)
            while True:
                try:
                    ret_code = self.fake_sync_endpoint.poll()
                    if ret_code is None:
                        self.send("")
                        break
                    else:
                        raise RuntimeError("starting fake endpoint failed")
                except subprocess.CalledProcessError:
                    pass

    def stop(self):
        if self.fake_sync_endpoint is not None:
            self.fake_sync_endpoint.terminate()
            self.fake_sync_endpoint.wait()
            del self.fake_sync_endpoint
            self.fake_sync_endpoint = None

    def send(self, data):
        subprocess.check_output("curl -s --data '{data}' http://localhost:8125 > /dev/null".format(data=data), shell=True)


class TestClient(unittest.TestCase):
    fake_ep = FakeEndpointConn()

    def setUp(self):
        cuser = pwd.getpwuid(os.getuid()).pw_name
        subprocess.check_call("sudo mkdir -m 777 -p {}".format(CODE_DIR), shell=True)
        subprocess.check_call("sudo mkdir -m 777 -p {}".format(STATE_DIR), shell=True)
        subprocess.check_call("sudo chown -R {cuser}:{cuser} /app".format(cuser=cuser), shell=True)
        self._rpc = jsc.client.SshJsonRpc(cuser, key_filename=os.path.expanduser("~/.ssh/id_rsa"), host="localhost")
        self._rpc.do_init()
        self.fake_ep.start()

    def tearDown(self):
        subprocess.check_call("sudo rm -rf {}".format("/app"), shell=True)
        self._rpc = None
        self.fake_ep.stop()

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
        self.add_env("env_assembly.json")

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

    def test_do_sync(self):
        self.add_env("env_assembly.json")
        self._rpc.do_sync()

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
        self.add_env("env_assembly.json")
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

    # recipe functions

    def test_rc_name(self):
        state = jsc.recipe.run(self._rpc, "name test", False)
        assert "name" in state
        assert state["name"] == "test"

    def test_rc_package(self):
        state = jsc.recipe.run(self._rpc, "package nodejs", False)
        assert "software_list" in state
        assert "package" in state['software_list']
        assert "nodejs" in state['software_list']['package']
        state = jsc.recipe.run(self._rpc, "package nginx php5", False)
        assert "nginx" in state['software_list']['package']
        assert "php5" in state['software_list']['package']

    def test_rc_install(self):
        # single file
        f_src = "{code_dir}/jsc_test_install_src".format(code_dir=CODE_DIR)
        f_dst = "{state_dir}/jsc_test_install_dst".format(state_dir=STATE_DIR)
        touch_file(f_src)
        jsc.recipe.run(self._rpc, "install {src} {dst}".format(src=f_src, dst=f_dst), False)
        assert os.path.isfile(f_dst)
        # directory with files
        d_src = "{code_dir}/jsc_test_install_src_dir".format(code_dir=CODE_DIR)
        d_dst = "{state_dir}/jsc_test_install_dst_dir".format(state_dir=STATE_DIR)
        touch_dir(d_src)
        for x in range(2):
            touch_file(os.path.join(d_src, str(x)))
            touch_dir(os.path.join(d_src, str(x) + "_dir"))
        jsc.recipe.run(self._rpc, "install {src} {dst}".format(src=d_src, dst=d_dst), False)
        assert os.path.isdir(d_dst)

    def test_rc_append(self):
        original_content = "original content"
        f_append = "{code_dir}/f_append".format(code_dir=CODE_DIR)
        jsc.recipe.run(self._rpc, "append {f} '{c}'".format(f=f_append, c=original_content), False)
        with open(f_append) as f:
            assert f.read() == original_content
        added_content = "\nadded_content"
        jsc.recipe.run(self._rpc, "append {f} '{c}'".format(f=f_append, c=added_content), False)
        with open(f_append) as f:
            assert f.read() == original_content + added_content

    def test_rc_put(self):
        original_content = "original content"
        f_put = "{code_dir}/f_put".format(code_dir=CODE_DIR)
        jsc.recipe.run(self._rpc, "put {f} '{c}'".format(f=f_put, c=original_content), False)
        with open(f_put) as f:
            assert f.read() == original_content
        new_content = "new_content"
        jsc.recipe.run(self._rpc, "put {f} '{c}'".format(f=f_put, c=new_content), False)
        with open(f_put) as f:
            assert f.read() == new_content

    def test_rc_replace(self):
        original_content = "original content original"
        replace_part = "original"
        f_replace = "{code_dir}/f_replace".format(code_dir=CODE_DIR)
        jsc.recipe.run(self._rpc, "put {f} '{c}'".format(f=f_replace, c=original_content), False)
        new_content = "new"
        jsc.recipe.run(self._rpc, "replace {f} '{r}' '{c}'".format(f=f_replace, r=replace_part, c=new_content), False)
        with open(f_replace) as f:
            assert f.read() == original_content.replace(replace_part, new_content)

    def test_rc_insert(self):
        original_content = "original content original"
        needle = "original"
        f_replace = "{code_dir}/f_replace".format(code_dir=CODE_DIR)
        jsc.recipe.run(self._rpc, "put {f} '{c}'".format(f=f_replace, c=original_content), False)
        new_content = "new"
        jsc.recipe.run(self._rpc, "insert {f} '{r}' '{c}'".format(f=f_replace, r=needle, c=new_content), False)
        with open(f_replace) as f:
            assert f.read() == original_content.replace(needle, needle+new_content, 1)

    def test_rc_rinsert(self):
        original_content = "original content original"
        needle = "original"
        f_replace = "{code_dir}/f_replace".format(code_dir=CODE_DIR)
        jsc.recipe.run(self._rpc, "put {f} '{c}'".format(f=f_replace, c=original_content), False)
        new_content = "new"
        jsc.recipe.run(self._rpc, "rinsert {f} '{r}' '{c}'".format(f=f_replace, r=needle, c=new_content), False)
        with open(f_replace) as f:
            assert f.read() == original_content[::-1].replace(needle[::-1], (new_content+needle)[::-1], 1)[::-1]



class TestRecipeParser(unittest.TestCase):
    def setUp(self):
        self.recipe = {
            'name recipename': ['name', 'recipename'],
            'name "recipe name"': ['name', 'recipe name'],
            'run ls -lah ./dir/path': ['run', 'ls -lah ./dir/path'],
            'run echo "echo"': ['run', 'echo "echo"'],
            'put /file "content with spaces in quotes"': ['put', '/file', 'content with spaces in quotes'],
            'put /file content': ['put', '/file', 'content'],
            'put /file unquoted_trailing_lonely_"': ['put', '/file', 'unquoted_trailing_lonely_"'],
            'put /file "escaped\\""': ['put', '/file', 'escaped"'],
            'replace /full/file/path "escaped\\"" "replace string"': ['replace', '/full/file/path', 'escaped"', 'replace string'],
            'replace relative_file "escaped\\"" "replace string"': ['replace', 'relative_file', 'escaped"', 'replace string'],
            'replace relative_file/path "escaped\\"" "replace string"': ['replace', 'relative_file/path', 'escaped"', 'replace string'],
            'replace relative_file/path "escaped\\"" "replace \nstring with newline"': ['replace', 'relative_file/path', 'escaped"', 'replace \nstring with newline'],
            'gd --depth=asdf --pkey=pkey --branch=name git@github.com/jumpstarter-io/jsc path/tocheck\ out/ya/\\nyah':
                ['gd', '--depth=asdf', '--pkey=pkey', '--branch=name', 'git@github.com/jumpstarter-io/jsc', 'path/tocheck out/ya/\nyah'],
            'gd git@github.com/jumpstarter-io/jsc path':
                ['gd', 'git@github.com/jumpstarter-io/jsc', 'path'],
            'gd --depth=asdf git@github.com/jumpstarter-io/jsc /path/tocheck\ out/ya/\\nyah':
                ['gd', '--depth=asdf', 'git@github.com/jumpstarter-io/jsc', '/path/tocheck out/ya/\nyah'],
            'install source/dir_\\nwith_escnewline dst # with comment end': ['install', 'source/dir_\nwith_escnewline', 'dst'],
            'install src dst': ['install', 'src', 'dst'],
        }

        self.should_fail = [
            'install src',
            'run',
            'append "sdfasdf\nasdfsdf"',
            'nonexisting "foo" foobar',
            'gd -depth path'

        ]

    def test_sl(self):
        for cmd in self.recipe.keys():
            result = rp.parse(cmd)
            if len(result) == 0:
                # comments does not return results
                return
            assert len(result) == 1
            assert cmp_lists(result.pop(), self.recipe[cmd])

    def test_ml(self):
        commands = []
        results = []
        for cmd in self.recipe.keys():
            commands.append(cmd)
            results.append(self.recipe[cmd])
        results.reverse()
        parse_results = rp.parse("\n".join(commands))
        for result in parse_results:
            assert cmp_lists(result, results.pop())

    def test_ml_failing(self):
        rec = "\n".join(self.should_fail)
        try:
            print("parsed_ml_failing: {}".format(rp.parse(rec)))
            # is this is reach, it parsed something...
            assert False
        except pyparsing.ParseException:
            pass
