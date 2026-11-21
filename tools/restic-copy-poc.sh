#!/usr/bin/env bash
set -Eeuo pipefail
# Disposable test repositories. Requires restic, python3, mktemp.
ROOT="$(mktemp -d)"
trap 'printf "\nTest artifacts: %s\n" "$ROOT"' EXIT
printf 'temporary-restic-test-password\n' > "$ROOT/password"
chmod 600 "$ROOT/password"
export RESTIC_PASSWORD_FILE="$ROOT/password"
mkdir -p "$ROOT/data" "$ROOT/repo-a" "$ROOT/repo-b" "$ROOT/repo-c"
printf 'first payload\n' > "$ROOT/data/example.txt"
restic version | tee "$ROOT/restic-version.txt"
for repo in a b c; do restic -r "$ROOT/repo-$repo" init; done
restic -r "$ROOT/repo-a" backup --json "$ROOT/data" > "$ROOT/backup-a.jsonl"
restic -r "$ROOT/repo-a" snapshots --json > "$ROOT/a-before.json"
restic -r "$ROOT/repo-a" copy --repo2 "$ROOT/repo-b" --password-file2 "$ROOT/password" > "$ROOT/copy-a-b-1.txt"
restic -r "$ROOT/repo-b" snapshots --json > "$ROOT/b-first.json"
restic -r "$ROOT/repo-a" copy --repo2 "$ROOT/repo-b" --password-file2 "$ROOT/password" > "$ROOT/copy-a-b-2.txt"
restic -r "$ROOT/repo-b" snapshots --json > "$ROOT/b-repeat.json"
restic -r "$ROOT/repo-b" copy --repo2 "$ROOT/repo-c" --password-file2 "$ROOT/password" > "$ROOT/copy-b-c.txt"
restic -r "$ROOT/repo-c" snapshots --json > "$ROOT/c-transitive.json"
ROOT="$ROOT" python3 - <<'PY'
import json, os, pathlib
root=pathlib.Path(os.environ['ROOT'])
def load(name):
    obj=json.loads((root/name).read_text())
    if not isinstance(obj,list): raise RuntimeError(f'{name} not a JSON list')
    return obj
for name in ('a-before.json','b-first.json','b-repeat.json','c-transitive.json'):
    snaps=load(name)
    print(f'\n{name}: {len(snaps)} snapshots')
    for s in snaps:
        print(json.dumps({k:s.get(k) for k in ('id','original','time','tree','hostname','paths','tags')},sort_keys=True))
a,b,b2,c=(load(n) for n in ('a-before.json','b-first.json','b-repeat.json','c-transitive.json'))
assert len(a)==len(b)==len(b2)==len(c)==1, 'Unexpected snapshot counts; inspect artifacts'
print('\nRepeated-copy count stable:',len(b)==len(b2))
print('A and B IDs equal:',a[0].get('id')==b[0].get('id'))
print('B and C IDs equal:',b[0].get('id')==c[0].get('id'))
print('Snapshot original metadata:',[s[0].get('original') for s in (a,b,c)])
PY
printf '\nArtifacts retained in %s\n' "$ROOT"
