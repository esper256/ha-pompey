#!/usr/bin/env python3
"""Normalize a copied Prowlarr table; ignore blank lines and optional Grabs cells."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def parse(text):
    rows=[]
    for block in re.split(r'(?im)^\s*torrent\s*$',text)[1:]:
        cells=[line.strip() for line in block.splitlines() if line.strip()]
        if len(cells)<6:raise ValueError('Incomplete copied row: '+repr(cells))
        age,title,indexer,size=cells[:4]
        amount=re.fullmatch(r'([\d.]+)\s*(KiB|MiB|GiB|TiB)',size)
        peers=next((re.fullmatch(r'(\d+)\s*/\s*(\d+)',c) for c in cells[4:] if '/' in c),None)
        if not amount or not peers:raise ValueError('Missing size or peers for '+title)
        peers_index=next(i for i,c in enumerate(cells) if re.fullmatch(r'\d+\s*/\s*\d+',c))
        category=''.join(cells[peers_index+1:])
        row={'id':hashlib.sha256((indexer+'\n'+title).encode()).hexdigest()[:16],
             'title':title,'source':indexer,'size':int(float(amount[1])*1024**(['KiB','MiB','GiB','TiB'].index(amount[2])+1)),
             'seeders':int(peers[1]),'leechers':int(peers[2]),'age_days':int(age.split()[0]),'category':category,'synthetic':False}
        if peers_index>4 and cells[4].isdigit():row['grabs']=int(cells[4])
        rows.append(row)
    if not rows:raise ValueError('No torrent rows found')
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(parse(args.input.read_text()),ensure_ascii=False,indent=2)+'\n')
