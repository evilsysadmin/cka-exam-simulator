"""Owned, expiring GNU nano PTYs and validated edits of live lab resources."""
from __future__ import annotations

from copy import deepcopy
import codecs
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import secrets
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

import yaml
from .candidate_kubectl import CandidateCommandError, NAME, _cpu


class EditRejected(ValueError):
    def __init__(self, code='invalid'):
        self.code = code if code in {'invalid','conflict','unavailable'} else 'invalid'
        super().__init__('Editor request rejected')


def edit_target(command, scenario):
    if (type(command) is not str or not 1 <= len(command) <= 200
            or any(c in command for c in '\n\r\0;|&`$><')):
        raise CandidateCommandError()
    try:
        words = shlex.split(command)
    except ValueError:
        raise CandidateCommandError() from None
    if words[:2] in (['kubectl','edit'], ['k','edit']):
        words = words[2:]
    elif words[:1] == ['nano']:
        words = words[1:]
    else:
        raise CandidateCommandError()
    if len(words) == 1 and '/' in words[0]:
        words = words[0].split('/')
    if len(words) != 2:
        raise CandidateCommandError()
    kind, name = words
    if kind in {'svc','service','services'} and name == scenario.service:
        return 'service', name
    if kind in {'deploy','deployment','deployments'} and name == scenario.deployment:
        return 'deployment', name
    raise CandidateCommandError()


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        pairs = self.construct_pairs(node, deep=deep)
        result = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise EditRejected()
            result[key] = value
        return result


def document(raw):
    if type(raw) is not str or len(raw.encode()) > 65536:
        raise EditRejected()
    try:
        for token in yaml.scan(raw):
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
                raise EditRejected()
        value = yaml.load(raw, Loader=UniqueLoader)
        if type(value) is not dict:
            raise EditRejected()
        json.dumps(value, allow_nan=False, sort_keys=True)
        return value
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise EditRejected() from None


def edit_patch(original, updated):
    """Only permitted exercise fields change; immutable identity/version must match.

    A JSON Patch resourceVersion test makes concurrent updates fail atomically.
    The namespace-scoped candidate identity, RBAC and admission also apply.
    """
    try:
        metadata = original['metadata']
        if type(metadata['resourceVersion']) is not str or not metadata['resourceVersion']:
            raise EditRejected()
        copy = deepcopy(updated)
        changes = []
        def field(parts, validate):
            before, after, normalized = original, updated, copy
            for part in parts[:-1]:
                before, after, normalized = before[part], after[part], normalized[part]
            key = parts[-1]
            value, old = after[key], before[key]
            validate(value)
            normalized[key] = deepcopy(old)
            if type(value) is not type(old) or value != old:
                changes.append({'op':'replace','path':'/'+'/'.join(map(str,parts)),'value':value})
        if original.get('kind') == 'Service':
            def selector(value):
                if type(value) is not str or not NAME.fullmatch(value):
                    raise EditRejected()
            field(['spec','selector','app'], selector)
        elif original.get('kind') == 'Deployment':
            def replicas(value):
                if type(value) is not int or not 0 <= value <= 4:
                    raise EditRejected()
            field(['spec','replicas'], replicas)
            containers = original['spec']['template']['spec']['containers']
            index = next(i for i,c in enumerate(containers) if c['name'] == 'web')
            def cpu(value):
                if type(value) is not str:
                    raise EditRejected()
                _cpu('cpu='+value)
            for kind in ('requests','limits'):
                field(['spec','template','spec','containers',index,'resources',kind,'cpu'], cpu)
        else:
            raise EditRejected()
        if json.dumps(copy,allow_nan=False,sort_keys=True) != json.dumps(original,allow_nan=False,sort_keys=True):
            raise EditRejected()
        return ([{'op':'test','path':'/metadata/resourceVersion','value':metadata['resourceVersion']}] + changes) if changes else []
    except (KeyError, IndexError, StopIteration, TypeError, ValueError, RecursionError):
        raise EditRejected() from None


@dataclass
class Editor:
    identity: str
    owner: str
    lease: str
    original: dict
    kind: str
    name: str
    directory: Path
    deadline: float
    attach_deadline: float
    process: subprocess.Popen | None = None
    master: int | None = None
    attached: bool = False
    decoder: object = None
    pending: bytearray = field(default_factory=bytearray)


class NanoEditors:
    def __init__(self):
        self.lock = threading.RLock()
        self.current = None
        self.scratch = Path('/tmp/cka-editors')

    def open(self, owner, lease, scenario, command, snapshot, remaining):
        kind, name = edit_target(command, scenario)
        if (type(snapshot) is not dict or snapshot.get('kind') != {'service':'Service','deployment':'Deployment'}[kind]
                or snapshot.get('metadata',{}).get('name') != name
                or snapshot.get('metadata',{}).get('namespace') != scenario.namespace):
            raise EditRejected()
        raw = yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True)
        document(raw)
        with self.lock:
            self.reap()
            if self.current:
                raise EditRejected('conflict')
            self.scratch.mkdir(mode=0o700, exist_ok=True)
            info = self.scratch.lstat()
            if self.scratch.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise EditRejected('unavailable')
            directory = Path(tempfile.mkdtemp(prefix='edit-',dir=self.scratch))
            path = directory / 'resource.yaml'
            path.write_text(raw); path.chmod(0o600)
            editor = Editor(secrets.token_hex(16), owner, lease, deepcopy(snapshot), kind, name, directory,
                            time.monotonic()+min(1200,remaining)-12, time.monotonic()+15)
            self.current = editor
            return {'editor':editor.identity}

    def owned(self, owner, lease, identity):
        with self.lock:
            editor = self.current
            if (not editor or editor.identity != identity or editor.owner != owner or editor.lease != lease
                    or time.monotonic() >= editor.deadline):
                raise EditRejected('unavailable')
            return editor

    def attach(self, editor, columns, rows):
        with self.lock:
            if self.current is not editor or editor.attached or time.monotonic() >= editor.attach_deadline:
                raise EditRejected('unavailable')
            master, slave = pty.openpty()
            try:
                self.resize_fd(slave, columns, rows)
                process = subprocess.Popen([sys.executable,'-m','cka_sim.nano_sandbox',str(editor.directory)],
                    stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True,
                    env={'PATH':'/usr/local/bin:/usr/bin:/bin','PYTHONPATH':os.environ.get('PYTHONPATH','/app'),
                         'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'})
                os.set_blocking(master,False)
            except Exception:
                os.close(master)
                raise EditRejected('unavailable') from None
            finally:
                os.close(slave)
            editor.master, editor.process, editor.attached = master, process, True
            editor.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

    def resize(self, editor, columns, rows):
        with self.lock:
            if self.current is not editor or editor.master is None:
                raise EditRejected('unavailable')
            self.resize_fd(editor.master,columns,rows)

    @staticmethod
    def resize_fd(fd, columns, rows):
        if type(columns) is not int or type(rows) is not int or not 20 <= columns <= 300 or not 6 <= rows <= 100:
            raise EditRejected()
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH',rows,columns,0,0))

    def read(self, editor):
        with self.lock:
            if self.current is not editor or editor.master is None:
                raise EditRejected('unavailable')
            if editor.pending:
                try:
                    written = os.write(editor.master,editor.pending[:4096])
                    del editor.pending[:written]
                except BlockingIOError:
                    pass
            try:
                raw = os.read(editor.master,16384)
            except BlockingIOError:
                return ''
            except OSError:
                return ''
            return editor.decoder.decode(raw)

    def input(self, editor, value):
        if type(value) is not str or len(value.encode()) > 4096:
            raise EditRejected()
        with self.lock:
            if self.current is not editor or editor.master is None or time.monotonic() >= editor.deadline:
                raise EditRejected('unavailable')
            data = value.encode()
            if len(editor.pending)+len(data) > 65536:
                raise EditRejected('unavailable')
            editor.pending.extend(data)

    def saved(self, editor):
        with self.lock:
            if self.current is not editor or not editor.process or editor.process.poll() != 0:
                raise EditRejected('unavailable')
            path = editor.directory / 'resource.yaml'
            fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > 65536:
                    raise EditRejected()
                return document(file.read(65537).decode())

    def close_lease(self, lease):
        with self.lock:
            if self.current and self.current.lease == lease:
                self.close()

    def reap(self):
        with self.lock:
            editor = self.current
            if editor and (time.monotonic() >= editor.deadline or
                           (not editor.attached and time.monotonic() >= editor.attach_deadline)):
                self.close()

    def close(self):
        with self.lock:
            editor, self.current = self.current, None
            if not editor:
                return
            if editor.process:
                if editor.process.poll() is None:
                    try:
                        os.killpg(editor.process.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                editor.process.wait(timeout=2)
            if editor.master is not None:
                os.close(editor.master)
            import shutil
            shutil.rmtree(editor.directory)
