#!/usr/bin/python2
import SimpleHTTPServer
import SocketServer


class ServerHandler(SimpleHTTPServer.SimpleHTTPRequestHandler):

    def do_POST(self):
        self.send_response(200)
        self.wfile.write("OK")
        self.wfile.close()


def serve():
    httpd = SocketServer.TCPServer(("localhost", 8000), ServerHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
