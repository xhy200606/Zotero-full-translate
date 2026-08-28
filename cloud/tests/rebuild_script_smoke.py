from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
script=ROOT/'scripts/rebuild.sh'
assert script.exists() and (script.stat().st_mode & 0o111)
subprocess.run(['bash','-n',str(script)],check=True)
out=subprocess.check_output(['bash',str(script),'--help'],text=True)
assert '--reset-data' in out and '--deep-cache' in out and 'PRESERVED' in script.read_text()
print('rebuild-script-smoke: ok')
