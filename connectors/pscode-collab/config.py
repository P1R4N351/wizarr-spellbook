"""Tunables for the pscode-collab provisioning shim. Single source of truth.

Sizing is derived from the collab host measured headroom on 2026-08-21:
available 9804 MB, swap 511/512 used (0 free), 8 cores, 18 containers.
See the plan's correction C2 for the executed arithmetic.
"""
import os

MEM_MB = 2048               # podman --memory per collaborator container
CPUS = 2.0                  # podman --cpus per collaborator container
RESERVE_MB = 2048           # never spend below this; protects the 18 existing services
MAX_COLLABORATORS = 3       # hard cap; (9804-2048)//2048 == 3 at time of writing

PORT_BASE = 47100           # container publish ports, 127.0.0.1 only
PORT_SPAN = 100              # rule 2: bounded search space for next_port()

SHIM_PORT = 4535            # Subsonic-shaped surface Wizarr calls (free on the collab host)
BROKER_PORT = 4537          # collaborator landing page, loopback (free on the collab host)
SERVE_PORT = 4536           # tailscale-serve front the paths hang off (see C8)


STATE_PATH = "/var/lib/pscode-collab/state.json"
SECRET_FILE = "/etc/pscode-collab/admin.secret"     # inbound Subsonic admin pw
GITEA_TOKEN_FILE = "/etc/pscode-collab/gitea.token"  # admin token used to mint others
ADMIN_USER = "admin"

GITEA_BASE = "http://100.64.0.10:18792"
GITEA_SSH = "ssh://git@100.64.0.10:2222"
# The invited repo is PER-DEPLOYMENT, so it is not hardcoded here (C9).
# The default is deliberately invalid: an unset deployment 404s at the gitea
# call rather than silently forking the wrong repository -- which is exactly
# what happened on the first end-to-end, and was the right failure.
#
# Deployments set it in REPO_CONF rather than by editing this file, because
# deploy.sh re-pushes config.py and would silently revert a hand edit.
# Format, one per line: owner=<gitea owner>  /  name=<repo>
# Public path prefix the collaborator's browser sees, "" for none (C13).
# All three funnel-eligible ports on the media host's `jellyfin` node are already in
# use (443 jellyfin / 8443 wizarr / 10000 audiobookshelf), so C8's Option A
# mounts this front as a PATH under an existing port rather than getting a
# port of its own. That prefix is visible to the browser, so Code-OSS must be
# told about it (--server-base-path) or every asset URL it generates is wrong,
# and the broker must include it in its redirect Location.
# Set to "/collab" at deploy; "" keeps direct tailnet access working.
PUBLIC_BASE_PATH = ""

REPO_CONF = "/etc/pscode-collab/repo.conf"
REPO_OWNER = "piranesi-org"
REPO_NAME = "REPLACE_ME"


def _load_repo_conf(path=REPO_CONF):
    """Overlay the deploy-time settings, if present.

    Returns (owner, name, base_path). All three are per-deployment: the repo
    because it is whatever the collaborator was invited to, and base_path
    because it depends on which public mount fronts this install (C13).
    """
    assert path, "repo conf path required"
    assert REPO_NAME, "module default must be set before overlay"
    owner, name, base = REPO_OWNER, REPO_NAME, PUBLIC_BASE_PATH
    if not os.path.exists(path):
        return owner, name, base
    with open(path, encoding="utf-8") as fh:
        for _ in range(16):                    # rule 2: bounded
            line = fh.readline()
            if not line:
                break
            key, _, value = line.strip().partition("=")
            if key == "owner" and value:
                owner = value
            elif key == "name" and value:
                name = value
            elif key == "base_path":
                base = value
    return owner, name, base


REPO_OWNER, REPO_NAME, PUBLIC_BASE_PATH = _load_repo_conf()
# The workspace image. 1.128.0 is the bare Code-OSS server bundle; the
# -kudzu1 layer adds the piranesi shim (as a USER extension) and the workstationhine
# settings that point its "Kudzu Mesh" model provider at the household
# inference gateway. Built by Containerfile.kudzu in this directory; the
# egress allowlist in collab-egress.nft is what lets it reach the door.
#
# -kudzu4 (2026-10-09, Piranesi-Main DECIDE: A, cs_mv0aff9e_12) carries shim
# 0.1.0-alpha.2 and a product.json with no `defaultChatAgent`, so the chat
# view is served by @piranesi and the Piranesi waist instead of a GitHub
# Copilot sign-in. Built by Containerfile.kudzu4 (an overlay on -kudzu3).
IMAGE = "localhost/pscode-collab:1.128.0-kudzu4"
RETENTION_DAYS = 14

# workstationhine-scope workbench settings mounted read-only into every collaborator
# container at launch (container.py). Kept OUT of the image because it holds
# the waist bearer (piranesiShim.waistAuthBearer): a credential in an image
# layer outlives every rotation. Absent file => the image's baked settings
# (same keys, empty bearer) apply.
MACHINE_SETTINGS = "/etc/pscode-collab/machine-settings.json"
MACHINE_SETTINGS_MOUNT = "/home/collab/.pscode/data/workstationhine/settings.json"

HTTP_TIMEOUT = 15
MAX_BODY = 1 << 20


# ---- hardware-key sign-in (WebAuthn) ---------------------------------------
# WEBAUTHN_RP_ID is the DOMAIN a credential is cryptographically bound to. It
# is set to the exact host on purpose. The registrable suffix
# "example.ts.net" would also be a legal RP ID (ts.net is on the Public
# Suffix List) and one key would then work from every door on the tailnet --
# including jellyfin's public funnel. That convenience is also the blast
# radius: any household service serving a page from a *.example.ts.net name
# could ask the key to sign. Pinning the host keeps a credential usable only
# from the door it was enrolled at.
#
# ★ CHANGING THIS INVALIDATES EVERY ENROLLED KEY. A credential records the RP
# ID it was created under and the authenticator will refuse to sign for a
# different one, so a change here means re-enrolling every key by hand.
#
# NOTE ON PORTS: WebAuthn scopes credentials by HOST, never by port. the collab host
# serves several unrelated things on this one name (:8088 the homepage, :8443
# odysseus, :443 tasks), and a credential bound to this RP ID is presentable
# from any of them. WEBAUTHN_ORIGINS is exact-match, including the port, which
# is what actually keeps this ceremony to this door -- python-fido2's default
# origin check would accept every one of those siblings.
WEBAUTHN_RP_ID = "collab-host.example.ts.net"
WEBAUTHN_RP_NAME = "The House of Piranesi"

# The https origin a security key can be used from. Browsers refuse WebAuthn
# outside a secure context, so the plain-http :4539 door cannot carry a key at
# all -- the page detects that and points here instead of failing silently.
SECURE_ORIGIN = "https://collab-host.example.ts.net:8450"

WEBAUTHN_STORE = "/var/lib/pscode-collab/webauthn.json"


def _webauthn_overlay(path=REPO_CONF):
    """Deploy-time overrides, same file and format as the repo settings.

      rp_id=<host>            the RP ID (see the warning above)
      secure_origin=<origin>  the https origin the door advertises
      extra_origin=<origin>   one additional allowed origin, repeatable

    Kept out of _load_repo_conf so that function keeps its three-value return
    and its existing callers do not have to change.
    """
    assert path, "conf path required"
    rp_id, secure, extra = WEBAUTHN_RP_ID, SECURE_ORIGIN, []
    if not os.path.exists(path):
        return rp_id, secure, tuple(extra)
    with open(path, encoding="utf-8") as fh:
        for _ in range(32):                      # rule 2: bounded
            line = fh.readline()
            if not line:
                break
            key, _, value = line.strip().partition("=")
            if key == "rp_id" and value:
                rp_id = value
            elif key == "secure_origin" and value:
                secure = value
            elif key == "extra_origin" and value and len(extra) < 8:
                extra.append(value)
    return rp_id, secure, tuple(extra)


WEBAUTHN_RP_ID, SECURE_ORIGIN, _EXTRA_ORIGINS = _webauthn_overlay()
# The allowlist the ceremony is checked against: the advertised door, plus any
# additional origin a deployment explicitly names.
WEBAUTHN_ORIGINS = (SECURE_ORIGIN,) + _EXTRA_ORIGINS
