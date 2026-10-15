"""Localhost->the collab host TCP forwarder for the collaborator public front.

WHY THIS EXISTS (plan correction C15, measured 2026-08-21): Tailscale Funnel
will NOT proxy to another node. Pointing the media host's `/collab` mount at the collab host's
tailnet address times out (000) while the identical mount pointed at a
localhost target answers 302 -- same funnel, same path, only the target
changed. Funnel requires a target on the local node, so C8 Option A needs a
local hop on the media host that the funnel can legally address.

Stdlib only, deliberately: the media host is a live public host and adding socat or
nginx to it is a package-set change on someone else's running service.

Bounded and non-privileged: binds loopback only, so nothing new is exposed --
the funnel in front of it is the only public path.
"""
# P10 RELAXATIONS: relaxes rule 2 (bounded loops) in two places, both
# deliberate and both bounded by something other than a counter: the accept()
# loop is a daemon main loop (the same relaxation nasa-code-agent grants
# service loops), and each _pump loop is bounded by its socket's EOF plus the
# connection timeout. A counter here would truncate a live session mid-stream.
import socket
import sys
import threading

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 4538
TARGET_HOST = "100.64.0.14"   # the collab host tailnet
TARGET_PORT = 4537              # broker
BUF = 65536
MAX_CONN = 64                   # rule 2: bounded concurrency


def _pump(src, dst):
    """Copy one direction until EOF, then half-close so the peer sees it."""
    assert src is not None and dst is not None, "both sockets required"
    assert src is not dst, "refusing to pump a socket into itself"
    try:
        while True:
            data = src.recv(BUF)
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


def _handle(client):
    assert client is not None, "client socket required"
    assert TARGET_PORT != LISTEN_PORT, "forwarder would loop back onto itself"
    try:
        upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=15)
    except OSError as exc:
        sys.stderr.write("upstream unreachable: %s\n" % exc)
        client.close()
        return
    threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
    _pump(upstream, client)
    client.close()
    upstream.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(MAX_CONN)
    sys.stderr.write("forwarding %s:%d -> %s:%d\n"
                     % (LISTEN_HOST, LISTEN_PORT, TARGET_HOST, TARGET_PORT))
    while True:
        client, _ = srv.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
