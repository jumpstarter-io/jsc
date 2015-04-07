import select
import os
import json
import os.path
import sys
import fcntl
import termios
from sshrpcutil import *
import sshjsonrpc

try:
    import logger as log
except ImportError:
    from jsc import logger as log


class SshJsonRpcPosix(sshjsonrpc.SshJsonRpc):
    def call(self, method, args):
        rpc_cmd = self.rpc(method, args)
        self._sendall(rpc_cmd)
        recv_buf = ""
        stdin_fd = os.dup(sys.stdin.fileno())
        flags = fcntl.fcntl(stdin_fd, fcntl.F_GETFL, 0)
        flags |= os.O_NONBLOCK
        fcntl.fcntl(stdin_fd, fcntl.F_SETFL, flags)
        tty = os.fdopen(stdin_fd, "r", 0)
        try:
            # Some code stolen from getpass.py
            # getpass Authors: Piers Lauder (original)
            #                  Guido van Rossum (Windows support and cleanup)
            #                  Gregory P. Smith (tty support & GetPassWarning)b
            old = termios.tcgetattr(stdin_fd)     # a copy to save
            new = termios.tcgetattr(stdin_fd)
            new[3] &= ~termios.ECHO  # 3 == 'lflags'
            new[3] &= ~termios.ICANON  # 3 == 'lflags'
            tcsetattr_flags = termios.TCSADRAIN
            termios.tcsetattr(stdin_fd, tcsetattr_flags, new)
            while True:
                rl, _, xl = select.select([self.ssh_channel, stdin_fd], [], [])
                if self.ssh_channel in rl:
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
                                    log.white(resp["stdout"], f=sys.stdout)
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
                if stdin_fd in rl:
                    new_stdin_data = tty.read()
                    self._sendall(self.stdin(new_stdin_data))
        except (KeyboardInterrupt, SshRpcError):
            # stdin_g.kill()
            self.ssh_channel.shutdown(2)
            self.ssh_channel = None
            raise KeyboardInterrupt()
        finally:
            termios.tcsetattr(stdin_fd, tcsetattr_flags, old)
            tty.flush()  # issue7208
            flags &= ~os.O_NONBLOCK
            fcntl.fcntl(stdin_fd, fcntl.F_SETFL, flags)
