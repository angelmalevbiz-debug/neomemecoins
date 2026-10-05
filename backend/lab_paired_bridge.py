"""Read-only snapshot projection. Never merges experiments into legacy trading books."""
import json
import os
import time
from pathlib import Path


def merge_paired_snapshot(state):
    path=Path(os.getenv('NEO_PAIRED_DIR','/var/lib/neo-lab-paired'))/'snapshot.json'
    try:
        source=json.loads(path.read_text())
        if source.get('version')!='LAB_PAIRED_EXITS_V1' or source.get('paper_only') is not True:
            return state
        if not isinstance(source.get('groups'),list):return state
        age=int(time.time()*1000)-int(source.get('updated_at',0))
        if not 0<=age<=20_000:source={**source,'status':'stale'}
        return {**state,'paired':source}
    except (OSError,ValueError,TypeError):return state
