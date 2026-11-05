import json
import os
import shutil
from pathlib import Path


def load(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    if config.get('schema_version') != 1:
        raise ValueError('Unsupported settings schema')
    config['_path'] = str(path)
    # Persist relative configuration; resolve at the process boundary, never
    # against a worker's changing cwd. Existing config/ layout defaults to '..'.
    base = Path(config.get('path_base', '..')).expanduser()
    config['_base'] = str((path.parent / base).resolve())
    for options in (config.get('worker_host', {}), config.get('router_agent', {}),
                    config.get('observer', {}).get('agent', {})):
        if options.get('python'):
            options['python'] = executable(config, options['python'])
        if options.get('dsh_command'):
            options['dsh_command'] = command(config, options['dsh_command'])
        if options.get('dsh_credentials_home'):
            options['dsh_credentials_home'] = str(local_path(config, options['dsh_credentials_home']))
    feishu = config.get('feishu', {})
    if feishu.get('cli_command'):
        feishu['cli_command'] = command(config, feishu['cli_command'])
    feishu['cli_config_dir'] = str(local_path(config, feishu.get('cli_config_dir', '.state/feishu-cli')))
    chats = feishu.get('chats', [])
    if len({c['chat_id'] for c in chats}) != len(chats):
        raise ValueError('Duplicate Feishu chat')
    config.setdefault('sources', {})['feishu'] = dict(
        chats=[c['chat_id'] for c in chats],
        members={c['chat_id']:c.get('allowed_users',[]) for c in chats},
        allowed_users=sorted({u for c in chats for u in c.get('allowed_users', [])}),
        bot_ids=[v for v in (feishu.get('bot_id'), feishu.get('app_id')) if v],
        allowed_stages=[s for s in feishu.get('allowed_stages', ['initialize']) if s in ('initialize','research','terminal')],
        auto_framework_requests=False)
    # Local desktop calls use DSH's authenticated owner RPC, never a browser-stored token.
    from .desktop_workflows import SOURCE,POLICY
    config['sources'][SOURCE] = dict(POLICY)
    return config


def local_path(config, value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (Path(config['_base']) / path).resolve()


def executable(config, value):
    """Bare commands use PATH; paths use the configured base, preserving venv links."""
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('Invalid executable')
    path = Path(value).expanduser()
    if not path.is_absolute() and not any(c in value for c in ('/', '\\')) and not value.startswith('.'):
        found = shutil.which(value)
        if not found:
            return value  # Configuration inspection must work before installation.
        path = Path(found)
    elif not path.is_absolute():
        path = Path(config['_base']) / path
    # Do not resolve(): Python must see the venv's executable, not its symlink target.
    return os.path.abspath(path)


def command(config, values):
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        raise ValueError('Command must be an argv array')
    output = list(values)
    output[0] = executable(config, output[0])
    for index, value in enumerate(values[1:], 1):
        # Entry scripts and explicit path flags are anchored. Literal arguments,
        # code following -c/-e, profile names and URLs are never rewritten.
        path_flag = values[index - 1] in ('--config', '--patch')
        profile_path = values[index - 1] == '--profile' and any(c in value for c in ('/', '\\'))
        script = index == 1 and value.endswith(('.js', '.mjs', '.cjs', '.py'))
        if (script or path_flag or profile_path) and not value.startswith('-') and '://' not in value:
            output[index] = str(local_path(config, value))
    return output
