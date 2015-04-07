import paramiko
import threading
import json
import os
import os.path
import inspect
import server_updater
import select
from sshrpcutil import *

try:
    from __init__ import __version__
except ImportError:
    from jsc import __version__

try:
    import logger as log
except ImportError:
    from jsc import logger as log


class SshJsonRpc():
    rpc_id = 0

    def __init__(self, username, password=None, key_filename=None, host=DEFAULT_SSH_HOST, port=DEFAULT_SSH_PORT):
        pkey = os.path.expanduser(key_filename) if key_filename is not None else None
        self.send_lock = threading.Lock()
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.load_system_host_keys()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kw = {"username": username,
                      "compress": True,
                      "look_for_keys": True}
        if password is not None:
            connect_kw["password"] = password
            connect_kw["look_for_keys"] = False
        if key_filename is not None:
            connect_kw["key_filename"] = key_filename
            connect_kw["look_for_keys"] = False
        try:
            self.ssh_client.connect(host, port, **connect_kw)
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
        try:
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
        except paramiko.ssh_exception.SSHException:
            log.white("Connection lost, make sure the assembly is running, then reconnect.")
            os._exit(1)

    def _open_channel(self):
        try:
            self.ssh_channel = self.ssh_transport.open_session()
            self.ssh_channel.setblocking(0)
            self.ssh_channel.exec_command('/tmp/server')
            self.stdout_file = self.ssh_channel.makefile("r", 0)
        except paramiko.ssh_exception.SSHException:
            log.white("Connection lost, make sure the assembly is running, then reconnect.")
            os._exit(1)


    def _sendall(self, rpc):
        if self.ssh_channel is None or self.ssh_channel.exit_status_ready():
            self._open_channel()
        with self.send_lock:
            self.ssh_channel.sendall("{rpc}\n".format(rpc=rpc))

    def call(self, method, args):
        raise NotImplementedError()

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

    def stdin(self, data):
        return json.dumps({"id":None,
                           "stdin": data})

    def __getattr__(self, item):
        def wrapper(args=None):
            return self.call(item, args)
        return wrapper
