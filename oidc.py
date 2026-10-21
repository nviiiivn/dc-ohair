"""Sign in with OpenID Connect (VoidAuth, Authelia, Authentik, Keycloak, Google…).

The authorization-code flow with PKCE. The ID token comes straight from the
provider's token endpoint over HTTPS, so per OpenID Connect Core 3.1.3.7 the
TLS connection vouches for it and its signature isn't re-checked here; the
issuer, audience, expiry and nonce are. Only people listed in allowed_users
get in, unless allow_any_user is set on purpose.

    hub:
      auth: oidc
      oidc:
        issuer: https://auth.example.com/oidc
        client_id: dc-ohair
        client_secret_env: DC_OIDC_SECRET      # or client_secret_cmd: "pass show oidc/dc"
        allowed_users: [me@example.com]
        name: VoidAuth                          # what the login button says
"""
import base64, hashlib, json, os, secrets, subprocess, threading, time, urllib.error, urllib.parse, urllib.request

_meta = {}
_pending = {}
_lock = threading.Lock()


def _get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=dict({'Accept': 'application/json', 'User-Agent': 'dc-ohair'}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def check_issuer(issuer):
    u = urllib.parse.urlparse(issuer or '')
    if u.scheme != 'https' and u.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('The issuer must be an https:// address.')
    return issuer.rstrip('/')


def metadata(cfg, fresh=False):
    issuer = check_issuer(cfg.get('issuer'))
    hit = _meta.get(issuer)
    if hit and not fresh and time.time() - hit[0] < 600:
        return hit[1]
    meta = _get(issuer + '/.well-known/openid-configuration')
    if meta.get('issuer', '').rstrip('/') != issuer:
        raise ValueError("The provider says its issuer is %r, not %r." % (meta.get('issuer'), issuer))
    for key in ('authorization_endpoint', 'token_endpoint'):
        if not meta.get(key):
            raise ValueError('The provider has no %s.' % key)
    _meta[issuer] = (time.time(), meta)
    return meta


def client_secret(cfg):
    if cfg.get('client_secret_env'):
        return os.environ.get(cfg['client_secret_env'], '')
    if cfg.get('client_secret_cmd'):
        cmd = cfg['client_secret_cmd']
        return subprocess.run(cmd, shell=isinstance(cmd, str), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=30, universal_newlines=True).stdout.strip()
    return ''


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def start(cfg, redirect_uri, next_path):
    """Returns the provider URL to send the browser to."""
    meta = metadata(cfg)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    with _lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if v['exp'] < now]:
            _pending.pop(k)
        if len(_pending) > 500:
            raise ValueError('Too many logins in progress; try again in a few minutes.')
        _pending[state] = {'verifier': verifier, 'nonce': nonce, 'next': next_path, 'redirect': redirect_uri, 'exp': now + 600}
    q = {'response_type': 'code', 'client_id': cfg['client_id'], 'redirect_uri': redirect_uri,
         'scope': cfg.get('scope') or 'openid email profile', 'state': state, 'nonce': nonce,
         'code_challenge': _b64(hashlib.sha256(verifier.encode()).digest()), 'code_challenge_method': 'S256'}
    return meta['authorization_endpoint'] + ('&' if '?' in meta['authorization_endpoint'] else '?') + urllib.parse.urlencode(q)


def _claims(id_token):
    try:
        payload = id_token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise ValueError('The provider sent an ID token that could not be read.')


def finish(cfg, state, code):
    """Swaps the code for tokens and returns (who, next path). Raises ValueError with a readable reason."""
    with _lock:
        pending = _pending.pop(state or '', None)
    if not pending or pending['exp'] < time.time():
        raise ValueError('That login link expired or was already used. Try again.')
    meta = metadata(cfg)
    form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': pending['redirect'],
            'client_id': cfg['client_id'], 'code_verifier': pending['verifier']}
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    secret = client_secret(cfg)
    if secret and cfg.get('token_auth') == 'basic':
        pair = '%s:%s' % (urllib.parse.quote(cfg['client_id'], safe=''), urllib.parse.quote(secret, safe=''))
        headers['Authorization'] = 'Basic ' + base64.b64encode(pair.encode()).decode()
    elif secret:
        form['client_secret'] = secret
    try:
        tokens = _get(meta['token_endpoint'], urllib.parse.urlencode(form).encode(), headers)
    except urllib.error.HTTPError as e:
        raise ValueError('The provider refused the login (%s): %s' % (e.code, e.read().decode(errors='replace')[:200]))
    claims = _claims(tokens.get('id_token', ''))
    aud = claims.get('aud')
    if claims.get('iss', '').rstrip('/') != meta['issuer'].rstrip('/'):
        raise ValueError('The ID token came from a different issuer.')
    if cfg['client_id'] not in (aud if isinstance(aud, list) else [aud]):
        raise ValueError('The ID token was meant for a different app.')
    if claims.get('exp', 0) < time.time() - 60:
        raise ValueError('The ID token has expired.')
    if claims.get('nonce') != pending['nonce']:
        raise ValueError('The ID token does not match this login.')
    if not claims.get('email') and meta.get('userinfo_endpoint') and tokens.get('access_token'):
        try:
            claims.update(_get(meta['userinfo_endpoint'], headers={'Authorization': 'Bearer ' + tokens['access_token']}))
        except (OSError, ValueError):
            pass
    names = {str(claims.get(k, '')).lower() for k in ('email', 'preferred_username', 'sub') if claims.get(k)}
    if claims.get('email_verified') is False:
        names.discard(str(claims.get('email', '')).lower())
    allowed = {str(u).lower() for u in cfg.get('allowed_users') or []}
    if not cfg.get('allow_any_user') and not (names & allowed):
        raise ValueError('%s signed in, but is not on the allowed list for this hub.' % (claims.get('email') or claims.get('sub')))
    who = claims.get('email') or claims.get('preferred_username') or claims.get('sub')
    return who, pending['next']
