"""Unprivileged Linux filesystem/syscall confinement for the real nano child.

Executed in a fresh Python child with only its PTY descriptors and a scrubbed
process environment. Failure to install either kernel policy refuses the editor.
Never install either policy in the API process or a shared worker thread.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import platform
import resource
import stat

READ_FILE, READ_DIR, EXECUTE, WRITE_FILE = 4, 8, 1, 2


def confine(directory: Path, *, executable=Path('/usr/bin/nano')):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    def checked(result):
        if result < 0:
            raise OSError(ctypes.get_errno(), 'Editor isolation unavailable')
        return result
    abi = checked(libc.syscall(444, 0, 0, 1))
    if abi < 3:
        raise OSError('Editor requires Landlock ABI 3')
    checked(libc.prctl(38, 1, 0, 0, 0))  # no_new_privs, including standalone CI
    checked(libc.prctl(4, 0, 0, 0, 0))   # not dumpable
    handled = (1 << 15) - 1
    class Ruleset(ctypes.Structure):
        _fields_ = [('handled_access_fs', ctypes.c_uint64)]
    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]
    ruleset = Ruleset(handled)
    descriptor = checked(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0))
    try:
        def allow(path, rights):
            path = Path(path).resolve(strict=True)
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                if not path.is_dir():
                    rights &= READ_FILE | WRITE_FILE | EXECUTE | (1 << 14)
                rule = PathRule(rights, fd)
                checked(libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0))
            finally:
                os.close(fd)
        allow(directory, handled & ~EXECUTE)
        allow(executable, READ_FILE | EXECUTE)
        for loader in ('/lib64/ld-linux-x86-64.so.2', '/lib/ld-linux-aarch64.so.1'):
            if Path(loader).exists():
                allow(loader, READ_FILE | EXECUTE)
        for name in ('/lib', '/lib64', '/usr/lib', '/usr/lib64', '/usr/share/terminfo',
                     '/lib/terminfo', '/usr/share/locale', '/etc/ld.so.cache'):
            if Path(name).exists():
                allow(name, READ_FILE | READ_DIR)
        for name in ('/dev/null', '/dev/tty', '/dev/urandom'):
            if Path(name).exists():
                allow(name, READ_FILE | WRITE_FILE)
        checked(libc.syscall(446, descriptor, 0))
    finally:
        os.close(descriptor)

    # No network, child processes, process inspection/signalling, namespaces,
    # privileged handles or io_uring. Landlock permits exec only of nano itself.
    machine = platform.machine()
    if machine == 'x86_64':
        arch = 0xC000003E
        denied = {41,42,43,44,45,46,47,49,50,53,56,57,58,62,101,165,166,200,234,
                  272,288,298,304,308,310,311,321,322}
    elif machine == 'aarch64':
        arch = 0xC00000B7
        denied = {39,40,97,117,129,130,131,198,199,200,201,202,203,206,207,211,212,
                  220,241,242,265,268,270,271,280,281}
    else:
        raise OSError('Unsupported editor architecture')
    denied |= {424,425,426,427,434,435,438}
    class Filter(ctypes.Structure):
        _fields_ = [('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [('length',ctypes.c_ushort),('filter',ctypes.POINTER(Filter))]
    instructions = [(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),
                    (0x20,0,0,0),(0x35,0,1,0x40000000),(0x06,0,0,0x80000000)]
    for number in sorted(denied):
        instructions.extend([(0x15,0,1,number),(0x06,0,0,0x00050000 | errno.EPERM)])
    instructions.append((0x06,0,0,0x7FFF0000))
    filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), filters)
    checked(libc.prctl(22, 2, ctypes.byref(program), 0, 0))


def main():
    import fcntl
    import sys
    import termios
    try:
        if len(sys.argv) != 2:
            return 64
        directory = Path(sys.argv[1])
        scratch = Path('/tmp/cka-editors')
        if (not directory.is_absolute() or directory.parent != scratch or directory.is_symlink()
                or directory.resolve() != directory or not directory.name.startswith('edit-')):
            return 64
        for path in (scratch, directory, directory / 'resource.yaml'):
            info = path.lstat()
            expected = 0o600 if path.name == 'resource.yaml' else 0o700
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != expected):
                return 64
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        resource.setrlimit(resource.RLIMIT_CORE, (0,0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (65536,65536))
        resource.setrlimit(resource.RLIMIT_CPU, (30,30))
        resource.setrlimit(resource.RLIMIT_AS, (64*1024**2,64*1024**2))
        resource.setrlimit(resource.RLIMIT_NOFILE, (16,16))
        os.chdir(directory)
        confine(directory)
        os.execve('/usr/bin/nano', ['nano','--restricted','--ignorercfiles','--operatingdir',str(directory),'resource.yaml'],
                  {'PATH':'/usr/bin','LANG':'C.UTF-8','TERM':'xterm-256color','HOME':str(directory)})
    except Exception:
        os.write(2, b'Editor isolation unavailable\r\n')
        return 1


def probe():
    from copy import deepcopy
    from types import SimpleNamespace
    import subprocess,time,yaml
    from cka_sim.lab_editor import NanoEditors,edit_patch
    version=subprocess.run(['/usr/bin/nano','--version'],capture_output=True,text=True,check=True)
    assert 'GNU nano' in version.stdout
    scenario=SimpleNamespace(namespace='cka-lab-'+'a'*32,deployment='test-api',service='test-api-svc')
    original={'apiVersion':'v1','kind':'Service','metadata':{'name':scenario.service,'namespace':scenario.namespace,'resourceVersion':'1'},'spec':{'selector':{'app':'before'},'ports':[{'port':80,'targetPort':8080}]}}
    manager=NanoEditors()
    try:
     opened=manager.open('synthetic-owner','synthetic-lease',scenario,'kubectl edit svc/test-api-svc',original,120)
     editor=manager.owned('synthetic-owner','synthetic-lease',opened['editor'])
     manager.attach(editor,100,24)
     def drain(seconds):
      end=time.monotonic()+seconds
      while time.monotonic()<end:
       manager.read(editor);time.sleep(.01)
     output='';ready=time.monotonic()+30
     while 'nano' not in output.lower() and time.monotonic()<ready and editor.process.poll() is None:
      output+=manager.read(editor);time.sleep(.02)
     assert 'nano' in output.lower()
     updated=deepcopy(original);updated['spec']['selector']['app']='after'
     text=yaml.safe_dump(updated,sort_keys=False)
     keys='\x1b\\'+'\x0b'*(len(yaml.safe_dump(original,sort_keys=False).splitlines())+2)+text.replace('\n','\r')+'\x0f\r\x18'
     manager.input(editor,keys)
     for _ in range(300):
      drain(.03)
      if editor.process.poll() is not None:break
     assert editor.process.poll()==0
     saved=manager.saved(editor)
     assert saved==updated
     assert edit_patch(original,saved)[-1]=={'op':'replace','path':'/spec/selector/app','value':'after'}
    finally:manager.close()
    print('CKA_REAL_NANO_SANDBOX_IMAGE_OK')


if __name__ == '__main__':
    raise SystemExit(main())
