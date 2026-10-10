"""Explicit evidence collection shared by hardware acceptance runners."""
import base64
import io
import os
from pathlib import Path
import shlex
import tarfile


def _python_command(code):
    return 'cd / && sudo -n /usr/bin/python3 -I -c ' + shlex.quote(code)


def validate_source_file(executor, source, root):
    """Reject paths outside the job and symlinks before privileged copying."""
    source, root = Path(source), Path(root)
    if not source.is_absolute() or not root.is_absolute() or '..' in source.parts or '..' in root.parts:
        raise ValueError('unsafe evidence source path')
    source.relative_to(root)
    code = """import pathlib,stat
source=pathlib.Path(SOURCE)
for path in (source,*source.parents):
 if path.is_symlink(): raise RuntimeError('symlink evidence source')
if not stat.S_ISREG(source.stat().st_mode): raise RuntimeError('not a regular evidence source')
""".replace('SOURCE', repr(str(source)), 1)
    executor.checked('server', _python_command(code), timeout=60)


def collect_server_file(executor, source, dest, *, source_root=None):
    source = Path(source)
    root = Path(source_root) if source_root is not None else source.parent
    validate_source_file(executor, source, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    executor.checked('server', 'sudo -n install -o ' + str(os.getuid())
                          + ' -g ' + str(os.getgid()) + ' -m 0600 -- '
                          + shlex.quote(str(source)) + ' ' + shlex.quote(str(dest)), timeout=60)

def collect_tree(executor, role, source, dest):
    # Server binaries remain available for offline SHA checking. Client
    # evidence remains the daemon's small, original whitelist archive.
    if role=='server':
        code = """import pathlib
root=pathlib.Path(SOURCE)
for path in (root,*root.parents,*root.rglob('*')):
 if path.is_symlink(): raise RuntimeError('symlink in evidence source tree')
assert root.is_dir()
""".replace('SOURCE', repr(str(source)), 1)
        executor.checked(role, _python_command(code), timeout=60)
        dest.mkdir(parents=True,exist_ok=True)
        executor.checked(role,'sudo -n cp -a '+shlex.quote(source+'/.')+' '+shlex.quote(str(dest)),timeout=120)
        # cp -a also preserves the source directory's root ownership on
        # dest. Return only the copied tree to the archive caller; do not
        # follow copied symlinks into runtime paths or other archives.
        executor.checked(role,'sudo -n chown -R -h -- '
                              +str(os.getuid())+':'+str(os.getgid())+' '
                              +shlex.quote(str(dest)),timeout=120)
        return
    code = """import base64,io,pathlib,tarfile
root=pathlib.Path(SOURCE)
assert root.is_dir() and not any(p.is_symlink() for p in (root,*root.parents)),str(root)
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w:gz') as tar:
 for p in sorted(root.rglob('*')):
  if p.is_symlink(): raise RuntimeError('symlink in evidence')
  if p.is_file():
   if p.suffix=='.bin' or p.stat().st_size>16*1024*1024: raise RuntimeError('large binary in client evidence')
   tar.add(p,arcname=str(p.relative_to(root)),recursive=False)
print(base64.b64encode(buf.getvalue()).decode())""".replace('SOURCE',repr(source),1)
    blob = base64.b64decode(executor.checked(role, _python_command(code),timeout=120))
    dest.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as tar:
        for member in tar.getmembers():
            relative=Path(member.name)
            if not member.isfile() or relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError('unsafe evidence archive member')
            target=dest/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(member) as src:
                target.write_bytes(src.read())

