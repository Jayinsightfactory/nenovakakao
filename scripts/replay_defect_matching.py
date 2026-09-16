"""Replay matching offline without modifying operational request records."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.moyi_inbound import parse_export
from core.defect_deduction_parser import extract
from core.defect_approval import rematch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--week',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    master_path=ROOT/'data'/'defect_master.json'
    master=json.loads(master_path.read_text(encoding='utf-8'))
    out=[]
    for event in parse_export(args.source.read_text(encoding='utf-8-sig'),'eval'):
        source=extract(event)
        if source['staff'] and source['sequence'].startswith(str(args.week)+'-'):
            out.append({'event':event,**rematch({'extracted':source,'revision':1},master)})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={'source_sha256':hashlib.sha256(args.source.read_bytes()).hexdigest(),
              'master_sha256':hashlib.sha256(master_path.read_bytes()).hexdigest(),
              'week':args.week,'messages':len(out),'items':sum(len(r['items']) for r in out)}
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest))


if __name__=='__main__': main()
