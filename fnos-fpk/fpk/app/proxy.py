#!/usr/bin/env python3
"""Relay fnOS app.sock to the existing music service without rewriting HTTP."""
import errno
import os
import signal
import socket
import stat
import threading

BACKEND = ("127.0.0.1", 8765)
CONNECT_TIMEOUT = 10
SOCK_PATH = os.path.join(os.environ.get("TRIM_APPDEST", os.path.dirname(os.path.abspath(__file__))), "app.sock")
_ERROR_BODY = b"Music Assistant: backend 127.0.0.1:8765 unreachable. Please start MediaMusicService.\n"
BAD_GATEWAY = (b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain; charset=utf-8\r\n"
               b"Connection: close\r\nContent-Length: " + str(len(_ERROR_BODY)).encode() + b"\r\n\r\n" + _ERROR_BODY)


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client):
    with client:
        try:
            backend = socket.create_connection(BACKEND, timeout=CONNECT_TIMEOUT)
        except OSError:
            try:
                client.sendall(BAD_GATEWAY)
            except OSError:
                pass
            return
        with backend:
            # The timeout is for connecting only. Persistent HTTP connections and
            # slow searches/audio streams may legitimately be idle for much longer.
            backend.settimeout(None)
            up = threading.Thread(target=pipe, args=(client, backend), daemon=True)
            up.start()
            pipe(backend, client)
            up.join()


def main():
    def terminate(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    if os.path.lexists(SOCK_PATH):
        if not stat.S_ISSOCK(os.lstat(SOCK_PATH).st_mode):
            raise RuntimeError(f"Refusing to replace a non-socket file: {SOCK_PATH}")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.connect(SOCK_PATH)
            except OSError as error:
                if error.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                    raise
                os.unlink(SOCK_PATH)
            else:
                raise RuntimeError(f"Another proxy is already listening on {SOCK_PATH}")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(SOCK_PATH)
        try:
            # The fnOS gateway runs as a different system user.
            os.chmod(SOCK_PATH, 0o666)
            server.listen(128)
            while True:
                client, _ = server.accept()
                threading.Thread(target=handle, args=(client,), daemon=True).start()
        finally:
            os.unlink(SOCK_PATH)


if __name__ == "__main__":
    main()
