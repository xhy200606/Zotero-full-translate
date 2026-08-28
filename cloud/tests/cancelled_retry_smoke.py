from pathlib import Path
import ast
ROOT=Path(__file__).resolve().parents[1]
adapter=(ROOT/'backend/app/services/babeldoc_adapter.py').read_text()
manager=(ROOT/'backend/app/task_manager.py').read_text()
ast.parse(adapter); ast.parse(manager)
for token in ['disable_split: bool = False','if "CancelledError" in message and not cancel_requested(job_id)','BabelDOC internal CancelledError']:
    assert token in adapter, token
for token in ['except asyncio.CancelledError as exc','if cancel_requested(job_id):','disable_split=True','retrying BabelDOC']:
    assert token in manager, token
print('cancelled-retry-smoke: ok')
