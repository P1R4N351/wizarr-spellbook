"""The workshop door: the sign-in page PSCode Web serves.

Kept apart from broker.py because it is a design artifact, not routing logic,
and mixing a 300-line template into the request handler made the handler
unreadable. Self-contained by requirement: no external stylesheet, font or
script. A sign-in page that reaches a third party on load leaks the fact of
the attempt to that third party, and this door is on the public internet via
the media host's funnel.

Visual language is inherited verbatim from the :8088 House of Piranesi
homepage (navy ground, layered radials, glass, bronze) so the door looks like
it belongs to the house it opens.

THE SIGNATURE: the House mark is a portico -- open pediment, three columns, a
base. Those three columns are not decoration here; each one stands for one
real phase of the WebAuthn ceremony (ask the house for a challenge / wait for
the touch / verify the signature), and they light in sequence as the ceremony
advances. The mark is therefore a progress indicator that was already the
house's logo.
"""

import json

_MARK = (
    '<svg class="mark" viewBox="13 20 60 60" fill="none" stroke="currentColor" '
    'stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true">'
    '<path class="pediment" d="M23 39 L43 28 L63 39"/>'
    '<path class="col c1" d="M30 45 L30 69"/>'
    '<path class="col c2" d="M43 45 L43 69"/>'
    '<path class="col c3" d="M56 45 L56 69"/>'
    '<path class="plinth" d="M19 72 L67 72"/>'
    '</svg>')

_STYLE = """
:root{
  --ink:#EAF1FF; --muted:rgba(214,226,247,.62); --faint:rgba(214,226,247,.38);
  --glass:rgba(255,255,255,.09); --glass-edge:rgba(255,255,255,.16);
  --bronze:#E0B278; --bronze-lit:#F2D2A4; --ground:#070F22;
  --bad:#E88C86;
  --sf:-apple-system,BlinkworkstationSystemFont,'SF Pro Display','SF Pro Text','Inter',
      'Helvetica Neue',Helvetica,Arial,sans-serif;
  --mono:ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{background:var(--ground)}
body{
  margin:0; min-height:100dvh; color:var(--ink); font-family:var(--sf);
  -webkit-font-smoothing:antialiased;
  background:
    radial-gradient(46% 42% at 16% 18%, rgba(78,141,240,.42), transparent 70%),
    radial-gradient(42% 40% at 84% 22%, rgba(38,196,186,.26), transparent 72%),
    radial-gradient(52% 50% at 72% 88%, rgba(108,92,231,.34), transparent 72%),
    radial-gradient(34% 30% at 26% 92%, rgba(199,158,116,.16), transparent 70%),
    linear-gradient(157deg,#11254C 0%,#0B1A37 52%,#060E22 100%);
  background-attachment:fixed; background-repeat:no-repeat;
  display:grid; place-items:center; padding:clamp(18px,5vh,56px) 18px;
}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(120% 90% at 50% 120%, rgba(2,6,16,.55), transparent 58%)}

.portico{position:relative;z-index:1;width:min(420px,100%)}

/* the plinth the panel stands on -- echoes the mark's own base stroke */
.portico::after{content:"";display:block;height:1px;margin:0 auto;
  width:78%;background:linear-gradient(90deg,transparent,rgba(224,178,120,.34),transparent)}
.portico::before{content:"";display:block;position:absolute;left:8%;right:8%;
  bottom:-13px;height:12px;border-radius:50%;z-index:-1;
  background:radial-gradient(closest-side,rgba(3,8,20,.7),transparent)}

.panel{
  background:var(--glass); border:1px solid var(--glass-edge);
  border-radius:20px 20px 4px 4px;
  backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);
  padding:clamp(26px,5vw,38px) clamp(20px,5vw,34px) 24px;
  box-shadow:0 24px 60px -22px rgba(2,6,18,.86);
}

.crown{display:grid;place-items:center;gap:2px;margin-bottom:26px}
.markwrap{position:relative;width:96px;height:96px;display:grid;place-items:center}
.markwrap::before{content:"";position:absolute;inset:4px;border-radius:50%;
  background:radial-gradient(closest-side,rgba(224,178,120,.34),
             rgba(224,178,120,.05) 66%,transparent 78%);
  animation:halo 5.5s ease-in-out infinite}
.mark{position:relative;width:86px;height:86px;color:#EAD0A6;
  filter:drop-shadow(0 0 10px rgba(224,178,120,.6))}
.mark .col{transition:stroke .45s ease, filter .45s ease}
/* Each column is one ceremony phase, and they light in order. The columns
   DIM the moment a ceremony starts (and only then -- at rest the mark is just
   the house's logo), because lit-vs-unlit has to be legible at a glance and
   two neighbouring shades of bronze are not. */
.s1 .col,.s2 .col,.s3 .col{stroke:rgba(234,208,166,.24);filter:none}
.s1 .c1,.s2 .c1,.s2 .c2,.s3 .col,.ok .col{stroke:#FBE6C4;
  filter:drop-shadow(0 0 8px rgba(251,230,196,.95))}
.s2 .c2{animation:waiting 1.15s ease-in-out infinite}
.ok .pediment,.ok .plinth{stroke:var(--bronze-lit);
  filter:drop-shadow(0 0 12px rgba(242,210,164,.95))}
.bad{animation:shake .38s ease}

@keyframes halo{0%,100%{opacity:.55;transform:scale(.98)}50%{opacity:1;transform:scale(1.06)}}
@keyframes waiting{0%,100%{opacity:1}50%{opacity:.34}}
@keyframes shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-7px)}
  50%{transform:translateX(6px)}80%{transform:translateX(-3px)}}
@media(prefers-reduced-motion:reduce){
  .markwrap::before,.s2 .c2,.bad{animation:none}
  *{transition:none!important}
}

.eyebrow{font-family:var(--mono);font-size:10.5px;letter-spacing:.19em;
  text-transform:uppercase;color:var(--faint);margin-top:10px}
h1{margin:1px 0 0;font-size:29px;letter-spacing:-.021em;font-weight:640;line-height:1.1}
.sub{margin:7px 0 0;font-size:13.5px;color:var(--muted);text-align:center;line-height:1.45}

.key{width:100%;margin-top:8px;padding:14px 18px;border:0;border-radius:13px;
  font-family:var(--sf);font-size:15.5px;font-weight:620;letter-spacing:-.01em;
  color:#1A1207;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px;
  background:linear-gradient(168deg,var(--bronze-lit),var(--bronze) 58%,#C89457);
  box-shadow:0 10px 26px -12px rgba(224,178,120,.8);
  transition:transform .14s cubic-bezier(.2,.85,.2,1),box-shadow .14s,filter .14s}
.key:hover{transform:translateY(-1px);filter:brightness(1.05)}
.key:active{transform:translateY(0) scale(.988)}
.key:disabled{opacity:.55;cursor:default;transform:none;filter:none}
.key svg{width:17px;height:17px;flex:0 0 auto}

.note{margin:11px 0 0;font-size:12.5px;color:var(--faint);text-align:center;line-height:1.5}

.rule{display:flex;align-items:center;gap:13px;margin:23px 0 19px;
  color:var(--faint);font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;
  text-transform:uppercase}
.rule::before,.rule::after{content:"";flex:1;height:1px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.15),transparent)}

details summary{list-style:none;cursor:pointer;text-align:center;
  font-size:13.5px;color:var(--muted);padding:6px;border-radius:9px;
  transition:color .15s,background .15s}
details summary::-webkit-details-marker{display:none}
details summary:hover{color:var(--ink);background:rgba(255,255,255,.05)}
details[open] summary{color:var(--ink);margin-bottom:5px}

label{display:block;font-family:var(--mono);font-size:10.5px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--faint);margin:13px 0 6px}
input{width:100%;padding:11px 13px;border-radius:11px;color:var(--ink);
  font-family:var(--sf);font-size:15px;
  background:rgba(6,12,28,.5);border:1px solid var(--glass-edge);
  transition:border-color .15s,background .15s}
input:hover{background:rgba(6,12,28,.62)}
input:focus{outline:none;border-color:rgba(224,178,120,.75);
  background:rgba(6,12,28,.72);box-shadow:0 0 0 3px rgba(224,178,120,.16)}
.pw-go{width:100%;margin-top:17px;padding:12px;border-radius:11px;cursor:pointer;
  font-family:var(--sf);font-size:14.5px;font-weight:560;color:var(--ink);
  background:rgba(255,255,255,.08);border:1px solid var(--glass-edge);
  transition:background .15s,border-color .15s}
.pw-go:hover{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.28)}

:focus-visible{outline:2px solid var(--bronze-lit);outline-offset:2px}

.say{margin:13px 0 0;min-height:17px;text-align:center;font-size:13px;
  line-height:1.5;color:var(--muted)}
.say.err{color:var(--bad)}
.say a{color:var(--bronze-lit)}

.foot{margin:15px auto 0;text-align:center;font-family:var(--mono);
  font-size:10.5px;letter-spacing:.1em;color:rgba(214,226,247,.3);line-height:1.7;
  word-break:break-all}
.foot b{font-weight:400;color:rgba(214,226,247,.46)}
"""

_KEY_GLYPH = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="8" cy="12" r="4.2"/><path d="M12.2 12H21"/>'
    '<path d="M17.4 12v3.4"/><path d="M20.2 12v2.4"/></svg>')


def _shell(title, body, script=""):
    """Wrap one page body in the house chrome."""
    assert title and body, "title and body are required"
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1,"
            "viewport-fit=cover'>"
            "<meta name=color-scheme content=dark>"
            "<meta name=theme-color content='#070F22'>"
            "<meta name=robots content='noindex,nofollow'>"
            "<title>%s</title><style>%s</style></head><body>%s%s</body></html>"
            % (title, _STYLE, body, script)).encode("utf-8")


def render_login(rp_id, secure_origin, base="", notice=None,
                 origins=()):
    """The door itself.

    `base` is the public path prefix this door is mounted under ("/collab" on
    the funnel, "" on the tailnet). Every URL the page builds is prefixed with
    it explicitly: a root-relative "/webauthn/..." would resolve to the FRONT
    host's root and leave the mount entirely, and a document-relative one
    changes meaning depending on whether the address had a trailing slash.

    `secure_origin` is the https origin a hardware key can actually be used
    from. It is shown -- not hidden -- when the visitor arrived over plain
    http, because "your key does not work here" is only useful if it also says
    where the key DOES work.
    """
    assert rp_id, "rp_id is required to render the door"
    assert secure_origin, "a secure origin must be named"
    assert isinstance(base, str), "base path must be a string"
    base = base.rstrip("/")
    # A failed password POST re-renders this page, so the reason has to be
    # able to travel with it -- and the password fields have to come back
    # already open, or the explanation sits above a collapsed form.
    said = _esc(notice) if notice else ""
    opened = " open" if notice else ""
    body = ("<main class=portico><div class=panel>"
            "<div class=crown><div class=markwrap id=crown>%s</div>"
            "<p class=eyebrow>The House of Piranesi</p>"
            "<h1>PSCode</h1>"
            "<p class=sub>Piranesi Studio Code, in this browser.</p></div>"
            "<button class=key id=keybtn type=button>%s"
            "<span id=keylabel>Touch your key</span></button>"
            "<p class=note id=keynote>Your security key signs you in. "
            "Nothing to type.</p>"
            "<div class=rule>or</div>"
            "<details id=pwbox%s><summary>Sign in with a password</summary>"
            "<form method=post action='%s/' id=pwform>"
            "<label for=u>Username</label>"
            "<input id=u name=username autocomplete=username autocapitalize=off "
            "autocorrect=off spellcheck=false required>"
            "<label for=p>Password</label>"
            "<input id=p name=password type=password "
            "autocomplete=current-password required>"
            "<button class=pw-go type=submit>Sign in</button>"
            "</form></details>"
            "<p class='say%s' id=say role=status aria-live=polite>%s</p>"
            "</div>"
            "<p class=foot><b>%s</b></p></main>"
            % (_MARK, _KEY_GLYPH, opened, base,
               " err" if notice else "", said, rp_id))
    allowed = json.dumps(list(origins) or [secure_origin])
    script = "<script>%s</script>" % (
        _SCRIPT.replace("__SECURE_ORIGIN__", secure_origin)
               .replace("__ORIGINS__", allowed)
               .replace("__BASE__", base))
    return _shell("Sign in &middot; PSCode", body, script)


def render_landing(username, headline, detail):
    """Shown when a sign-in succeeds but there is nowhere to send the visitor.

    An authenticated dead end is still a success, and saying so plainly beats
    a redirect into a 404. The page names what is missing and who can create
    it rather than apologising.
    """
    assert username, "landing page needs the authenticated name"
    assert headline and detail, "landing page must say what happened and why"
    body = ("<main class=portico><div class=panel>"
            "<div class=crown><div class='markwrap ok'>%s</div>"
            "<p class=eyebrow>Signed in as %s</p>"
            "<h1>%s</h1>"
            "<p class=sub>%s</p></div>"
            "</div></main>" % (_MARK, _esc(username), _esc(headline),
                               _esc(detail)))
    return _shell("PSCode", body)


def _esc(text):
    """Minimal HTML escape for the few interpolated values on these pages."""
    assert isinstance(text, str), "only strings are escaped here"
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_SCRIPT = r"""
(function(){
  "use strict";
  var crown=document.getElementById('crown'),btn=document.getElementById('keybtn'),
      say=document.getElementById('say'),note=document.getElementById('keynote'),
      label=document.getElementById('keylabel'),pwbox=document.getElementById('pwbox');
  var SECURE='__SECURE_ORIGIN__', BASE='__BASE__', ORIGINS=__ORIGINS__;

  function phase(n){ crown.className='markwrap'+(n?' s'+n:''); }
  function tell(msg,bad){ say.textContent=msg; say.className='say'+(bad?' err':''); }
  function tellHTML(html){ say.innerHTML=html; say.className='say err'; }
  function fail(msg){ phase(0); crown.classList.add('bad');
    setTimeout(function(){crown.classList.remove('bad');},420);
    btn.disabled=false; label.textContent='Touch your key'; tell(msg,true); }

  function b64uToBuf(s){
    s=String(s).replace(/-/g,'+').replace(/_/g,'/');
    while(s.length%4)s+='=';
    var raw=atob(s),buf=new Uint8Array(raw.length);
    for(var i=0;i<raw.length;i++)buf[i]=raw.charCodeAt(i);
    return buf.buffer;
  }
  function bufTob64u(buf){
    var b=new Uint8Array(buf),s='';
    for(var i=0;i<b.length;i++)s+=String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  }

  // Two separate reasons a key cannot be used from this page, and they need
  // different sentences. (1) Not a secure context at all -- plain http.
  // (2) Secure, but the WRONG DOOR: a credential is bound to one RP ID, so a
  // key enrolled for the tailnet door simply cannot sign from the public
  // funnel origin. The browser would throw a bare SecurityError there, which
  // reads as "your key is broken" when nothing is broken.
  var canUseKey = window.isSecureContext && window.PublicKeyCredential;
  var rightDoor = ORIGINS.indexOf(location.origin) !== -1;
  if(!canUseKey || !rightDoor){
    btn.disabled=true;
    note.textContent = canUseKey
      ? 'Security keys work from a different address than this one.'
      : 'Security keys need an https connection.';
    if(location.origin!==SECURE){
      tellHTML('Open <a href="'+SECURE+BASE+'/">'+
               SECURE.replace(/^https:\/\//,'')+BASE+'/</a> to use your key.');
    }
    if(pwbox) pwbox.open=true;
    return;
  }

  btn.addEventListener('click',function(){
    var HANDLE=null;   // ceremony handle from /begin; /finish is dead without it
    btn.disabled=true; label.textContent='Working';
    tell(''); phase(1);
    fetch(BASE+'/webauthn/login/begin',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({})})
      .then(function(r){ return r.json().then(function(j){
          if(!r.ok) throw new Error(j.error||'The house refused the request.');
          return j; }); })
      .then(function(opts){
        var pk=opts.publicKey;
        HANDLE=opts.handle;
        pk.challenge=b64uToBuf(pk.challenge);
        (pk.allowCredentials||[]).forEach(function(c){ c.id=b64uToBuf(c.id); });
        phase(2); label.textContent='Waiting for your touch';
        tell('Touch the key now.');
        return navigator.credentials.get({publicKey:pk});
      })
      .then(function(cred){
        phase(3); label.textContent='Checking'; tell('Checking the signature.');
        return fetch(BASE+'/webauthn/login/finish',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            handle:HANDLE, id:cred.id, type:cred.type,
            rawId:bufTob64u(cred.rawId),
            response:{
              clientDataJSON:bufTob64u(cred.response.clientDataJSON),
              authenticatorData:bufTob64u(cred.response.authenticatorData),
              signature:bufTob64u(cred.response.signature),
              userHandle:cred.response.userHandle?
                bufTob64u(cred.response.userHandle):null
            }})});
      })
      .then(function(r){ return r.json().then(function(j){
          if(!r.ok) throw new Error(j.error||'That key was not accepted.');
          return j; }); })
      .then(function(j){
        crown.className='markwrap ok'; label.textContent='Welcome';
        tell('Opening your workspace.');
        setTimeout(function(){ location.assign(j.dest); },420);
      })
      .catch(function(e){
        if(e && (e.name==='NotAllowedError'||e.name==='AbortError')){
          fail('No touch registered. Try again when you are ready.');
        } else {
          fail((e && e.message) ? e.message : 'That key was not accepted.');
        }
      });
  });
})();
"""


def render_enroll(username, label, ticket, base="", secure_origin=""):
    """The one-time page that enrols a key.

    Reached only with a live ticket minted out of band, so the page itself
    carries no password field and no account chooser: the ticket already said
    who this is for, and offering a choice here would be offering a way to
    enrol a key against somebody else's name.
    """
    assert username and ticket, "enrollment needs a user and a ticket"
    assert isinstance(base, str), "base path must be a string"
    base = base.rstrip("/")
    body = ("<main class=portico><div class=panel>"
            "<div class=crown><div class=markwrap id=crown>%s</div>"
            "<p class=eyebrow>Enrolling for %s</p>"
            "<h1>Add your key</h1>"
            "<p class=sub>Insert your security key, then touch it when it "
            "blinks. It will be named %s.</p></div>"
            "<button class=key id=enrolbtn type=button>%s"
            "<span id=keylabel>Register this key</span></button>"
            "<p class=note id=keynote>This link works once, and only for the "
            "next half hour.</p>"
            "<p class=say id=say role=status aria-live=polite></p>"
            "</div></main>"
            % (_MARK, _esc(username), _esc(label), _KEY_GLYPH))
    script = "<script>%s</script>" % (
        _ENROLL_SCRIPT.replace("__BASE__", base)
                      .replace("__TICKET__", _esc(ticket))
                      .replace("__SECURE_ORIGIN__", secure_origin))
    return _shell("Add your key &middot; PSCode", body, script)


_ENROLL_SCRIPT = r"""
(function(){
  "use strict";
  var crown=document.getElementById('crown'),btn=document.getElementById('enrolbtn'),
      say=document.getElementById('say'),note=document.getElementById('keynote'),
      label=document.getElementById('keylabel');
  var BASE='__BASE__', TICKET='__TICKET__', SECURE='__SECURE_ORIGIN__';

  function phase(n){ crown.className='markwrap'+(n?' s'+n:''); }
  function tell(msg,bad){ say.textContent=msg; say.className='say'+(bad?' err':''); }
  function fail(msg){ phase(0); crown.classList.add('bad');
    setTimeout(function(){crown.classList.remove('bad');},420);
    btn.disabled=false; label.textContent='Register this key'; tell(msg,true); }

  function b64uToBuf(s){
    s=String(s).replace(/-/g,'+').replace(/_/g,'/');
    while(s.length%4)s+='=';
    var raw=atob(s),buf=new Uint8Array(raw.length);
    for(var i=0;i<raw.length;i++)buf[i]=raw.charCodeAt(i);
    return buf.buffer;
  }
  function bufTob64u(buf){
    var b=new Uint8Array(buf),s='';
    for(var i=0;i<b.length;i++)s+=String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  }

  if(!window.isSecureContext || !window.PublicKeyCredential){
    btn.disabled=true;
    note.textContent='Security keys need an https connection.';
    tell('Open this link on '+SECURE.replace(/^https:\/\//,'')+' to enrol.',true);
    return;
  }

  btn.addEventListener('click',function(){
    var HANDLE=null;   // ceremony handle from /begin; /finish is dead without it
    btn.disabled=true; label.textContent='Working'; tell(''); phase(1);
    fetch(BASE+'/webauthn/register/begin',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ticket:TICKET})})
      .then(function(r){ return r.json().then(function(j){
          if(!r.ok) throw new Error(j.error||'The house refused the request.');
          return j; }); })
      .then(function(opts){
        var pk=opts.publicKey;
        HANDLE=opts.handle;
        pk.challenge=b64uToBuf(pk.challenge);
        pk.user.id=b64uToBuf(pk.user.id);
        (pk.excludeCredentials||[]).forEach(function(c){ c.id=b64uToBuf(c.id); });
        phase(2); label.textContent='Waiting for your touch';
        tell('Touch the key now.');
        return navigator.credentials.create({publicKey:pk});
      })
      .then(function(cred){
        phase(3); label.textContent='Saving'; tell('Recording the key.');
        return fetch(BASE+'/webauthn/register/finish',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            ticket:TICKET, handle:HANDLE, id:cred.id, type:cred.type,
            rawId:bufTob64u(cred.rawId),
            response:{
              clientDataJSON:bufTob64u(cred.response.clientDataJSON),
              attestationObject:bufTob64u(cred.response.attestationObject)
            }})});
      })
      .then(function(r){ return r.json().then(function(j){
          if(!r.ok) throw new Error(j.error||'That key was not accepted.');
          return j; }); })
      .then(function(){
        crown.className='markwrap ok'; btn.disabled=true;
        label.textContent='Enrolled';
        note.textContent='This link has now been used.';
        tell('Your key is enrolled. Sign in with it from the door.');
      })
      .catch(function(e){
        if(e && e.name==='InvalidStateError'){
          fail('That key is already enrolled for this account.');
        } else if(e && (e.name==='NotAllowedError'||e.name==='AbortError')){
          fail('No touch registered. Try again when you are ready.');
        } else if(e && e.name==='ConstraintError'){
          fail('This key cannot store a resident credential. Set a PIN on it '
               +'with YubiKey Manager, then try again.');
        } else {
          fail((e && e.message) ? e.message : 'That key was not accepted.');
        }
      });
  });
})();
"""
