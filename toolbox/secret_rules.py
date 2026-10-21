"""What the secret finder looks for.

Most content rules are gitleaks' own (config/gitleaks.toml, MIT, copyright
(c) 2019 Zachary Rice; the notice is in toolbox/LICENSES/gitleaks.txt),
ported from Go's RE2 to Python's re: an inline (?i) in the middle of a
pattern is a scoped (?i:...), as Python 3.11 requires. Changes from the
originals are marked. aws-secret-access-key and url-password are this
project's own, for what gitleaks leaves out.

Each rule: id, what it is, a regex whose group 1 (or the whole match) is the
secret, keywords (lower case; the file must contain one before the regex
runs, which is what keeps this fast), and the least Shannon entropy the
secret must have, so "password = changeme" is not a finding. The secret
itself never leaves the device: the tools agent reports the file, the line,
the rule, and a short fingerprint so the same key in five places is seen as
one key.
"""

END = r'''(?:[\x60'"\s;]|\\[nr]|$)'''
ASSIGN = r'''(?:[ \t\w.-]{0,20})[\s'"]{0,3}(?:=|>|:{1,3}=|\|\||:|=>|\?=|,)[\x60'"\s=]{0,5}'''

RULES = [
    ('aws-access-token', 'AWS access key ID', r'\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\b',
     ['a3t', 'akia', 'asia', 'abia', 'acca'], 3),
    ('aws-secret-access-key', 'AWS secret access key',
     r'''(?i)aws_?secret_?access_?key[\s'"]{0,3}(?:=|:)[\s'"]{0,5}([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])''',
     ['secret_access_key', 'secretaccesskey', 'secret-access-key'], 4),
    ('github-pat', 'GitHub personal access token', r'ghp_[0-9a-zA-Z]{36}', ['ghp_'], 3),
    ('github-fine-grained-pat', 'GitHub fine-grained token', r'github_pat_\w{82}', ['github_pat_'], 3),
    ('github-oauth', 'GitHub OAuth token', r'gho_[0-9a-zA-Z]{36}', ['gho_'], 3),
    ('github-app-token', 'GitHub app token', r'(?:ghu|ghs)_[0-9a-zA-Z]{36}', ['ghu_', 'ghs_'], 3),
    ('github-refresh-token', 'GitHub refresh token', r'ghr_[0-9a-zA-Z]{36}', ['ghr_'], 3),
    ('gitlab-pat', 'GitLab personal access token', r'glpat-[\w-]{20}', ['glpat-'], 3),
    ('slack-bot-token', 'Slack bot token', r'xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*', ['xoxb'], 3),
    ('slack-user-token', 'Slack user token', r'xox[pe](?:-[0-9]{10,13}){3}-[a-zA-Z0-9-]{28,34}', ['xoxp-', 'xoxe-'], 2),
    ('slack-app-token', 'Slack app token', r'(?i)xapp-\d-[A-Z0-9]+-\d+-[a-z0-9]+', ['xapp'], 2),
    ('slack-webhook-url', 'Slack webhook', r'(?:https?://)?hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9+/]{43,56}',
     ['hooks.slack.com'], 0),
    ('stripe-access-token', 'Stripe key', r'\b((?:sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99})' + END,
     ['sk_test', 'sk_live', 'sk_prod', 'rk_test', 'rk_live', 'rk_prod'], 2),
    ('gcp-api-key', 'Google API key', r'\b(AIza[\w-]{35})' + END, ['aiza'], 4),
    ('private-key', 'Private key', r'(?i)-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----[\s\S-]{64,}?KEY(?: BLOCK)?-----',
     ['-----begin'], 0),
    ('jwt', 'JSON Web Token', r'\b(ey[a-zA-Z0-9]{17,}\.ey[a-zA-Z0-9/\\_-]{17,}\.(?:[a-zA-Z0-9/\\_-]{10,}={0,2})?)' + END, ['ey'], 3),
    ('openai-api-key', 'OpenAI API key',
     r'\b(sk-(?:proj|svcacct|admin)-(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})T3BlbkFJ(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})\b'
     r'|sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20})' + END, ['t3blbkfj'], 3),
    ('anthropic-api-key', 'Anthropic API key', r'\b(sk-ant-api03-[a-zA-Z0-9_\-]{93}AA)' + END, ['sk-ant-api03'], 0),
    ('anthropic-admin-key', 'Anthropic admin key', r'\b(sk-ant-admin01-[a-zA-Z0-9_\-]{93}AA)' + END, ['sk-ant-admin01'], 0),
    ('discord-api-token', 'Discord token', r'(?i)[\w.-]{0,50}?(?:discord)' + ASSIGN + r'([a-f0-9]{64})' + END, ['discord'], 0),
    ('telegram-bot-token', 'Telegram bot token',
     r'(?i)[\w.-]{0,50}?(?:telegr)' + ASSIGN + r'([0-9]{5,16}:(?-i:A)[a-z0-9_\-]{34})' + END, ['telegr'], 0),
    ('npm-access-token', 'npm token', r'(?i)\b(npm_[a-z0-9]{36})' + END, ['npm_'], 2),
    ('pypi-upload-token', 'PyPI token', r'pypi-AgEIcHlwaS5vcmc[\w-]{50,1000}', ['pypi-ageichlwas5vcmc'], 3),
    ('sendgrid-api-token', 'SendGrid key', r'\b(SG\.(?i:[a-z0-9=_\-\.]{66}))' + END, ['sg.'], 2),
    ('huggingface-access-token', 'Hugging Face token', r'\b(hf_(?i:[a-z]{34}))' + END, ['hf_'], 2),
    ('digitalocean-pat', 'DigitalOcean token', r'\b(dop_v1_[a-f0-9]{64})' + END, ['dop_v1_'], 3),
    ('shopify-access-token', 'Shopify token', r'shpat_[a-fA-F0-9]{32}', ['shpat_'], 2),
    # changed: \b on both sides, or any 34-character hex run with SK in it is a "key"
    ('twilio-api-key', 'Twilio key', r'\bSK[0-9a-fA-F]{32}\b', ['sk'], 3),
    ('age-secret-key', 'age secret key', r'AGE-SECRET-KEY-1[QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L]{58}', ['age-secret-key-1'], 0),
    ('vault-service-token', 'HashiCorp Vault token', r'\b((?:hvs\.[\w-]{90,120}|s\.(?i:[a-z0-9]{24})))' + END, ['hvs.', 's.'], 3.5),
    ('url-password', 'Password in a URL', r'''\b[a-z][a-z0-9+.-]{1,20}://[^/\s:@'"]{1,64}:([^/\s:@'"]{3,128})@[\w.-]+''', ['://'], 2.5),
    ('generic-api-key', 'Key or password assigned in a file',
     r'(?i)[\w.-]{0,50}?(?:access|auth|(?-i:[Aa]pi|API)|credential|creds|key|passw(?:or)?d|secret|token)' + ASSIGN +
     r'([\w.=-]{10,150}|[a-z0-9][a-z0-9+/]{11,}={0,3})' + END,
     ['access', 'api', 'auth', 'key', 'credential', 'creds', 'passwd', 'password', 'secret', 'token'], 3.5),
]

# A generic match whose value is one of these is an example, not a secret.
STOPWORDS = ['example', 'xxxx', 'changeme', 'placeholder', 'your_', 'your-', 'yourkey', 'redacted', 'dummy',
             'sample', '${', '{{', '<', 'process.env', 'os.environ', 'getenv', 'null', 'none', 'true', 'false']

# Files that are secrets by their name alone; the content is never read.
NAMES = [
    ('ssh-private-key-file', 'SSH private key', r'(^|/)id_(rsa|dsa|ecdsa|ed25519)(_sk)?$'),
    # .pem is left to the private-key content rule: most .pem files are public certificates
    ('key-file', 'Key store or key file', r'\.(key|p12|pfx|jks|keystore|ppk)$'),
    ('env-file', '.env file', r'(^|/)\.env(\.(?!example|sample|template|dist)[\w-]+)?$'),
    ('netrc', 'Login file (.netrc, .pgpass, .git-credentials)', r'(^|/)(\.netrc|_netrc|\.pgpass|\.git-credentials)$'),
    ('package-token-file', 'Package registry login (.npmrc, .pypirc)', r'(^|/)(\.npmrc|\.pypirc)$'),
    ('cloud-credentials', 'Cloud credentials', r'(^|/)(\.aws/credentials|\.kube/config|gcloud/credentials\.db|'
                                                r'service[-_]?account[\w.-]*\.json|credentials\.json)$'),
]

# Never worth reading: the operating system, caches, other people's code and media; not her keys.
SKIP_DIRS = ['.git/objects', 'node_modules', '__pycache__', '.venv', 'venv', 'site-packages', '.cache', '.npm',
             '.cargo/registry', '.rustup', '.gradle', '.m2', 'Library/Caches', '.Trash', '.local/share/Trash',
             'steamapps', '.snapshots', '.var/app', 'proc', 'sys', 'dev', 'run', 'tmp', 'usr', 'lib', 'lib64', 'bin',
             'sbin', 'boot', 'snap', 'var/lib', 'var/cache', 'var/log', 'etc/ssl', 'etc/ca-certificates', 'System',
             'Applications', 'Library/Developer', 'private/var', 'Windows', 'Program Files', 'Program Files (x86)',
             'ProgramData', '$Recycle.Bin', 'System Volume Information']
SKIP_EXT = ['.mkv', '.mp4', '.avi', '.mov', '.m4v', '.ts', '.webm', '.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus',
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.raw', '.psd', '.pdf', '.epub', '.zip', '.gz', '.xz',
            '.zst', '.bz2', '.7z', '.rar', '.tar', '.iso', '.img', '.dmg', '.so', '.dll', '.exe', '.o', '.a', '.class',
            '.jar', '.pyc', '.woff', '.woff2', '.ttf', '.otf', '.sqlite', '.db', '.pmtiles', '.mbtiles', '.gguf',
            '.safetensors', '.bin', '.pt', '.onnx', '.navipack']


def spec(roots, exclude=()):
    """The tools agent's spec for a secret search of [roots]."""
    return {'mode': 'secrets', 'roots': list(roots), 'exclude': list(exclude),
            'rules': [list(r) for r in RULES], 'stopwords': STOPWORDS, 'names': [list(n) for n in NAMES],
            'skip_dirs': SKIP_DIRS, 'skip_ext': SKIP_EXT, 'max_file': 1024 * 1024}
