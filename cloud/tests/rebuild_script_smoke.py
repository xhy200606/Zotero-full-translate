from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
rebuild=ROOT/'scripts/rebuild.sh'
update=ROOT/'scripts/update.sh'
for script in (rebuild, update):
    assert script.exists() and (script.stat().st_mode & 0o111), script
    subprocess.run(['bash','-n',str(script)],check=True)

rebuild_help=subprocess.check_output(['bash',str(rebuild),'--help'],text=True)
assert '--reset-data' in rebuild_help and '--fresh' in rebuild_help
text=rebuild.read_text()
assert '--no-cache' in text and 'FRESH' in text
                                                                           
assert 'Cached rebuild: no prune, no --no-cache' in text

update_help=subprocess.check_output(['bash',str(update),'--help'],text=True)
assert 'backend/app-only changes' in update_help
update_text=update.read_text()
assert 'docker compose restart' in update_text
assert 'docker builder prune' not in update_text
assert 'docker compose build --no-cache' not in update_text
print('rebuild-script-smoke: ok')
