"""Path-routing proxy for the collaborator front (C18).

WHY THIS REPLACES tailscale serve's per-collaborator paths: tailscale will not
proxy to a non-localhost target -- established for Funnel (C15) and, measured
here, equally true of `serve`. A `/c/<name>` route pointed at a container's
address on collab-net answers 000 even though the host can curl that same
address successfully. So the per-collaborator routing has to happen in a
process that CAN reach collab-net, which is this one.

It reads each request's head, routes on that request's own path, then pipes
raw bytes both ways -- which is what makes WebSocket upgrade work unchanged,
and PSCode needs WebSockets. A parsing proxy would have to reimplement that;
this does not.

It did NOT always do that. Until 2026-08-29 it routed on the FIRST request
line of a connection and then piped blindly, which is correct only if every
connection carries one request. tailscaled does not work that way: it holds a
pool of keep-alive connections to this port, and the collaborator container
answers HTTP/1.1 with Connection: keep-alive (the broker answers HTTP/1.0 and
closes, so only container-routed connections survive to be reused). Every
later request on such a connection was piped to the container no matter what
it asked for. MEASURED: 8 parallel requests for the sign-in page, 7 answered
by the container's "Forbidden."; and the workbench rendered with no CSS and
tofu icons because its assets were answered by the wrong backend.

The fix is to make one request per connection the rule for plain HTTP -- the
head is rewritten to Connection: close, so the backend closes and this
process closes the client side, and the proxy cannot carry a second request
over a routing decision made for the first. An Upgrade request is exempt and
forwarded untouched, because a WebSocket connection legitimately IS one
request for its whole life.

  /c/<name>/...  -> that collaborator's container on collab-net
  anything else  -> the broker

The name->address map is read fresh from state.json per connection (it is
small, and 0644 by design -- see state.py). Reading it fresh rather than
caching is deliberate: a container gets a NEW address on every restart
(C17), and a cached map would route a live session into nothing.

Stdlib only. Binds 0.0.0.0 because the media host's loopback forwarder dials it across
the tailnet; the collab host has no funnel, so this is not a public surface.
"""
# P10 RELAXATIONS: relaxes rule 2 (bounded loops) twice, both deliberate and
# both bounded by something other than a counter -- the accept() loop is a
# daemon main loop, and each pump loop is bounded by socket EOF plus the
# connection timeout. A counter would truncate a live editing session.
import json
import os
import re
import socket
import sys
import threading

# 4539, NOT 4536: tailscaled keeps intercepting a port it has ever served, in
# userspace, even after `tailscale serve --https=4536 off` removes it from
# `serve status`. A plain-HTTP request from another node to that port gets
# "Client sent an HTTP request to an HTTPS server" and never reaches this
# process at all -- while the same request from the local node does. Moving to
# a port tailscale has never touched is the fix; arguing with its residue is
# not.
LISTEN_HOST, LISTEN_PORT = "0.0.0.0", 4539
BROKER = ("127.0.0.1", 4537)
STATE = "/var/lib/pscode-collab/state.json"
REPO_CONF = "/etc/pscode-collab/repo.conf"
CONTAINER_PORT = 3000
BUF = 65536
MAX_CONN = 64
MAX_HEAD = 32768                     # rule 2: bounded request-head read
MAX_READS = 64                       # rule 2: bounded recv count per head
_NAME_RE = re.compile(r"^/(?:collab/)?c/([a-z0-9][a-z0-9-]{0,63})(?:/|\s|$)")


def _public_prefix(path=None):
    """The public mount prefix, e.g. "/collab" ("" if unmounted).

    MEASURED (C19): tailscale funnel --set-path STRIPS its prefix before
    forwarding. C13 left that open and this end-to-end settled it -- the
    request arrives here as /c/<name>/..., while the browser's address bar and
    every URL Code-OSS generates carry /collab/c/<name>/... So the container is
    told the PUBLIC path via --server-base-path, and this router adds the
    prefix back on the way in. Without that the container 404s a path it was
    never configured to answer, which is exactly what it did.
    """
    # Resolved at CALL time, not as a default argument: a default binds at
    # definition, so the module global could never override it -- which made
    # this function silently untestable and would have hidden a config change.
    path = path or REPO_CONF
    assert path, "repo conf path required"
    assert isinstance(path, str), "repo conf path must be a string"
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(16):                # rule 2: bounded
                line = fh.readline()
                if not line:
                    break
                key, _, value = line.strip().partition("=")
                if key == "base_path":
                    return value.rstrip("/")
    except OSError:
        pass
    return ""


def _rewrite(path):
    """Re-add the public prefix the funnel stripped, if it is absent."""
    assert path.startswith("/"), "path must be absolute"
    prefix = _public_prefix()
    if prefix and not path.startswith(prefix + "/") and path != prefix:
        return prefix + path
    return path


def _target_for(path):
    """Resolve one request path to a backend address."""
    assert path.startswith("/"), "path must be absolute"
    assert BROKER[1] != LISTEN_PORT, "router would loop back onto itself"
    m = _NAME_RE.match(path)
    if not m:
        return BROKER
    name = m.group(1)
    try:
        with open(STATE, encoding="utf-8") as fh:
            for rec in json.load(fh):
                if rec.get("name") == name and rec.get("ip"):
                    return (rec["ip"], CONTAINER_PORT)
    except (OSError, ValueError):
        pass    # unreadable state is not a reason to drop the broker path
    return BROKER


def _pump(src, dst):
    assert src is not dst, "refusing to pump a socket into itself"
    assert src is not None and dst is not None, "both sockets required"
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


def _read_head(client):
    """Read one request head. Returns (head, spill) -- spill is body bytes
    that arrived in the same recv and must be forwarded after the head."""
    assert client is not None, "client socket required"
    assert MAX_HEAD > 0, "request-head bound must be positive"
    buf = b""
    for _ in range(MAX_READS):              # rule 2: bounded
        idx = buf.find(b"\r\n\r\n")
        if idx != -1:
            return buf[:idx + 4], buf[idx + 4:]
        if len(buf) >= MAX_HEAD:
            break
        try:
            chunk = client.recv(BUF)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    return buf, b""


def _is_upgrade(head):
    """True for a request that asks to stop being HTTP (WebSocket).

    Such a connection legitimately carries one request for its whole life, so
    it is exempt from the one-request-per-connection rule and is forwarded
    byte-for-byte.
    """
    assert isinstance(head, bytes), "head must be bytes"
    assert not head or b" " in head.split(b"\r\n")[0], \
        "head must open with a request line; header scan starts after it"
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"upgrade:") and line.split(b":", 1)[1].strip():
            return True
    return False


def _force_close(head):
    """Rewrite a request head so the backend closes after one response.

    This is what makes routing per-request: with no reusable connection, the
    proxy cannot carry a later request over a decision made for an earlier
    one. Any client-supplied Connection/Keep-Alive header is dropped first --
    appending without removing would leave two conflicting Connection headers
    and let the backend pick the wrong one.
    """
    assert isinstance(head, bytes), "head must be bytes"
    assert head.endswith(b"\r\n\r\n"), "head must be terminated"
    lines = head[:-4].split(b"\r\n")
    kept = [lines[0]]
    for line in lines[1:]:
        low = line.lower()
        if low.startswith(b"connection:") or low.startswith(b"keep-alive:"):
            continue
        kept.append(line)
    kept.append(b"Connection: close")
    return b"\r\n".join(kept) + b"\r\n\r\n"


def _handle(client):
    assert client is not None, "client socket required"
    head, spill = _read_head(client)
    assert isinstance(head, bytes) and isinstance(spill, bytes), \
        "_read_head must return the head and any body bytes it over-read"
    if not head:
        client.close()
        return
    try:
        path = head.split(b" ")[1].decode("latin-1")
    except (IndexError, UnicodeDecodeError):
        path = "/"
    try:
        upstream = socket.create_connection(_target_for(path), timeout=15)
    except OSError as exc:
        sys.stderr.write("upstream unreachable for %s: %s\n" % (path, exc))
        client.close()
        return
    # Replay the request line, re-adding the prefix the funnel stripped (C19).
    try:
        verb, orig, rest = head.split(b" ", 2)
        head = b" ".join([verb, _rewrite(orig.decode("latin-1")).encode("latin-1"), rest])
    except (ValueError, UnicodeDecodeError):
        pass    # unparseable request line: forward it untouched, let the backend judge
    if not _is_upgrade(head) and head.endswith(b"\r\n\r\n"):
        head = _force_close(head)
    upstream.sendall(head + spill)
    threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
    _pump(upstream, client)
    client.close()
    upstream.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(MAX_CONN)
    sys.stderr.write("collab-router on %s:%d\n" % (LISTEN_HOST, LISTEN_PORT))
    while True:
        client, _ = srv.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
