#!/usr/bin/python2
import SimpleHTTPServer
import SocketServer
import signal
import sys


class ServerHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):

    def do_POST(self):
        self.send_response(200)
        self.wfile.write("OK")
        self.wfile.close()


class TCPServer(SocketServer.TCPServer):
    allow_reuse_address = True


httpd = None


def serve():
    global httpd
    httpd = TCPServer(("localhost", 8125), ServerHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        httpd.shutdown()


def sig_handler(signum, frame):
    print("got signal SIGTERM")
    httpd.shutdown()
    sys.exit(0)

signal.signal(signal.SIGTERM, sig_handler)
