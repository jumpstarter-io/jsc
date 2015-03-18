import select
import json
import sys
from sshrpcutil import *
import sshjsonrpc

try:
    import logger as log
except ImportError:
    from jsc import logger as log


class SshJsonRpcWin(sshjsonrpc.SshJsonRpc):
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
                            # Last line is either not complete or empty string.
                            # ("x\nnot compl".split("\n") => ['x', 'not compl'] or "x\n".split("\n") => ['x', ''])
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
