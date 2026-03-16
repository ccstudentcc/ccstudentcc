from pathlib import Path
import sys
import datetime

# Ensure we can import modules in .github/scripts
sys.path.insert(0, str(Path('.').resolve() / '.github' / 'scripts'))
import workflow_common as wc

path = wc.ROOT / '.github' / 'manager' / 'state' / 'test-persist.json'
# Enqueue a test payload
wc.enqueue_json(path, {"test": "payload-from-local"})
print('enqueued', path)
# Force flush queued writes to disk
wc.flush_json_writes(force=True)
print('flushed')

# Print persistence metrics and errors
metrics_path = wc.ROOT / '.github' / 'manager' / 'state' / 'persistence-metrics.json'
errors_path = wc.ROOT / '.github' / 'manager' / 'state' / 'persistence-errors.log'
print('\n--- persistence-metrics.json ---')
if metrics_path.exists():
    print(metrics_path.read_text(encoding='utf-8'))
else:
    print('missing')

print('\n--- persistence-errors.log ---')
if errors_path.exists():
    print(errors_path.read_text(encoding='utf-8'))
else:
    print('missing')

# Print mtimes for README and key state files
files_to_check = [
    wc.ROOT / 'README.md',
    wc.ROOT / '.github' / 'manager' / 'state' / 'metadata-store.json',
    wc.ROOT / '.github' / 'manager' / 'state' / 'state.json',
    wc.ROOT / '.github' / 'manager' / 'state' / 'queue.json',
    wc.ROOT / '.github' / 'manager' / 'state' / 'dag.json',
    path,
]
print('\n--- mtimes ---')
for p in files_to_check:
    if p.exists():
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        print(f"{p.relative_to(wc.ROOT)} -> {mtime.isoformat()}")
    else:
        print(f"{p.relative_to(wc.ROOT)} -> missing")
