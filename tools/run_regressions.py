#!/usr/bin/env python3
"""Run each regression module in its own process to isolate Panda globals."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/validation-v1.4/regression-report.json')
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter()
    results=[]
    def run(path):
        tick=time.perf_counter()
        process=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',path.name,'-v'],
                cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output=process.stdout
        count=re.search(r'Ran (\d+) tests?',output)
        result=dict(module=path.name,passed=process.returncode==0,
                    tests=int(count.group(1)) if count else 0,
                    seconds=round(time.perf_counter()-tick,2))
        if process.returncode:result['output']=output
        return result
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
        futures=[pool.submit(run,path) for path in sorted((ROOT/'tests').glob('test_*.py'))]
        for future in as_completed(futures):
            result=future.result();results.append(result)
            print(json.dumps(result),flush=True)
            report=dict(status='running',modules=sorted(results,key=lambda r:r['module']))
            args.output.write_text(json.dumps(report,indent=2)+'\n')
    report=dict(status='passed' if all(r['passed'] for r in results) else 'failed',
                tests=sum(r['tests'] for r in results),seconds=round(time.perf_counter()-start,2),
                modules=sorted(results,key=lambda r:r['module']))
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='modules'}),flush=True)
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
