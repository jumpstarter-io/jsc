import select
import json
import sys
from multiprocessing import Process, Event
import socket
from sshrpcutil import *
import sshjsonrpc
import msvcrt
try:
    import logger as log
except ImportError:
    from jsc import logger as log


def input_reader(port, event):
    try:
        fwd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        fwd.connect(("127.0.0.1", port))
        while not event.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getch()
                fwd.send(key)
    except BaseException as e:
        print(str(e))


class SshJsonRpcWin(sshjsonrpc.SshJsonRpc):
    def call(self, method, args):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for port in range(14000, 62000):
            try:
                server_socket.bind(("127.0.0.1", port))
                break
            except socket.error:
                continue
        server_socket.listen(5)
        ev = Event()
        input_thread = Process(target=input_reader, args=(port, ev))
        input_thread.start()
        (input_socket, _) = server_socket.accept()
        input_socket.setblocking(0)
        rpc_cmd = self.rpc(method, args)
        self._sendall(rpc_cmd)
        recv_buf = ""
        try:
            while True:
                rl, _, xl = select.select([self.ssh_channel, input_socket], [], [])
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
                                    sys.stdout.write(resp["stdout"])
                                    sys.stdout.flush()
                                elif "stderr" in resp:
                                    log.white(resp["stderr"], f=sys.stderr)
                                elif "result" in resp:
                                    if resp['error'] is not None:
                                        raise SshRpcCallError(resp['error']['message'])
                                    #print("ending",method)
                                    return resp["result"]
                    if self.ssh_channel.recv_stderr_ready():
                        log.white("{}".format(self.ssh_channel.recv_stderr(4096)))
                    if self.ssh_channel.exit_status_ready():
                        raise SshRpcError()
                if input_socket in rl:
                    new_stdin_data = input_socket.recv(1024)
                    self._sendall(self.stdin(new_stdin_data))
        except (KeyboardInterrupt, SshRpcError):
            self.ssh_channel.shutdown(2)
            self.ssh_channel = None
            raise KeyboardInterrupt()
        finally:
            ev.set()
            input_thread.terminate()
            input_thread.join()
            del input_socket
            del server_socket
