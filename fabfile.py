from fabric.api import *
import subprocess
import os
import requests
import distutils.version as dist_version
from jsc import __version__

CURRENT_DIR = os.getcwd()
FABFILE_DIR = os.path.dirname(__file__)
JSC_SRC_ROOT = os.path.join(FABFILE_DIR, "jsc")
REMOTE_DIR = "/var/www/repo"
REMOTE_SERVER_TEMP_FILE = "server_tmp"
PYPI_JSON = "https://pypi.python.org/pypi/jsc/json"


print(FABFILE_DIR)


def released_version():
    r = requests.get(PYPI_JSON)
    version = r.json()['info']['version']
    return version


def check_version(rel_ver, this_ver):
    if dist_version.StrictVersion(rel_ver) >= dist_version.StrictVersion(this_ver):
        print("release version [{this}] is newer than what you're trying to deploy")
        return False
    return True


@task
def ul_server():
    released_ver = released_version()
    if not check_version(released_ver, __version__):
        return
    subprocess.check_call("pyinstaller -F -n server server.py", shell=True, cwd=JSC_SRC_ROOT)
    subprocess.check_call("scp -P23 dist/server root@repo.jumpstarter.io:/var/www/jsc/server-{version}".format(version=__version__), shell=True, cwd=JSC_SRC_ROOT)


@task
def release(pypi="pypitest"):
    released_ver = released_version()
    if not check_version(released_ver, __version__):
        return
    subprocess.check_call("python2 setup.py sdist register -r {pypi}".format(pypi=pypi), shell=True, cwd=FABFILE_DIR)
    subprocess.check_call("python2 setup.py sdist upload -r {pypi}".format(pypi=pypi), shell=True, cwd=FABFILE_DIR)
