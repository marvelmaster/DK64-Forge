"""Offline DK64 discovery. Candidates are never automatically runtime artwork.

Run: python -m tools.discover_connected_textures ROM --output local_output/connected
Stages: decode, geometry, search, sheets (or all). Review decisions persist by hash.
"""
import argparse
import hashlib
import json
from collections import defaultdict, Counter
from pathlib import Path
import numpy as np
from dk64_forge.session import load_rom
from dk64_forge.core import texture_bank as tb
from dk64_forge import static_model as sm


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def source_key(table, index):
    return f'{table}:{index}:0'


def decode(rom, root):
    pixels = {}; rows = []; failures = []; counts = {}
    for table in (25, 7, 14):
        items = tb.build_bank_items(rom, table=table)
        counts[str(table)] = dict(entries=tb.entry_count(rom, table), nonempty=len(items))
        for item in items:
            key = source_key(table, item.index)
            if item.usage is None or item.is_palette:
                failures.append(dict(key=key, reason='No known image descriptor / palette only'))
                continue
            try:
                value = tb.decode_item(rom, item)
                if value is None: raise ValueError('Decoder returned no pixels')
                w, h, rgba = value
                pixels[key] = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
                rows.append(dict(key=key, table=table, image=item.index, frame=0,
                    width=w, height=h, usage=dict(fmt=item.usage.fmt, size=item.usage.size,
                    interleaved=item.usage.interleaved, palette=item.usage.palette),
                    users=list(item.references), conflicting=item.conflicting))
            except Exception as exc:
                failures.append(dict(key=key, reason=str(exc)))
        print('Decoded bank', table, len(pixels), flush=True)
    np.savez_compressed(root/'pixels.npz', **pixels)
    save(root/'sources.json', dict(rom_sha256=hashlib.sha256(rom).hexdigest(),
        banks=counts, decoded=len(pixels), sources=rows, unresolved=failures,
        orientation='source_v = render_v; top-first decoder rows, unflipped GPU upload'))
    from dk64_forge.texture_sequences import sequences
    groups={str(t):[list(frames) for frames,_,_ in {row for rows in sequences(rom,t).values() for row in rows}] for t in (25,7,14)}
    save(root/'sequences.json',groups)


def model_edges(model):
    data=model.render; edges=defaultdict(list); joins=Counter()
    for batch in data.batches:
        if batch.texture_index is None: continue
        t=model.texture_sources[batch.texture_index]; w,h=t.usage.width,t.usage.height
        for start in range(batch.first_vertex,batch.first_vertex+batch.vertex_count,3):
            p=np.asarray(data.positions[start:start+3]); uv=np.asarray(data.uvs[start:start+3])*[w,h]
            normal=np.cross(p[1]-p[0],p[2]-p[0]); length=np.linalg.norm(normal)
            if length<1e-6: continue
            normal/=length
            for i,j in ((0,1),(1,2),(2,0)):
                a,b=tuple(np.round(p[i],3)),tuple(np.round(p[j],3))
                if a==b: continue
                q=uv[[i,j]] if a<b else uv[[j,i]]; sides=[]
                for axis,size in enumerate((w,h)):
                    for end in (0,size):
                        if np.max(abs(q[:,axis]-end))<1.1 and np.min(q[:,1-axis])>=-1.1 and np.max(q[:,1-axis])<=(h,w)[axis]+1.1:
                            sides.append((axis,end))
                if sides: edges[tuple(sorted((a,b)))].append((source_key(t.table,t.image),q,normal,sides))
    for rows in edges.values():
        for i,(a,ua,na,sa) in enumerate(rows):
            for b,ub,nb,sb in rows[i+1:]:
                if a==b: continue
                for axis,end in sa:
                    for axisb,endb in sb:
                        if axis!=axisb or (end==0)==(endb==0): continue
                        delta=ua-ub; offset=np.round(delta.mean(0))
                        if np.max(abs(delta-offset))>1.1: continue
                        offset[axis]=end-endb; offset=tuple(map(int,offset))
                        coplanar=abs(float(na@nb))>.98
                        key=(a,b,offset,coplanar) if a<b else (b,a,tuple(-v for v in offset),coplanar)
                        joins[key]+=1
    return [dict(a=a,b=b,offset=offset,coplanar=flat,votes=v) for (a,b,offset,flat),v in joins.items()]


def geometry(rom, root):
    cache=sm.TextureCache(rom); rows=[]; failures=[]; empty=[]
    for kind,table,loader in (('actor',5,sm.actor_model),('prop',4,sm.prop_model),('map',1,sm.map_model)):
        for index in range(tb.entry_count(rom,table)):
            try:
                model=loader(rom,index,cache)
                if model is None: empty.append(f'{kind} {index}'); continue
                rows.append(dict(user=f'{kind} {index}', joins=model_edges(model)))
            except Exception as exc: failures.append(dict(user=f'{kind} {index}', reason=str(exc)))
            if index%100==0: print('Geometry',kind,index,flush=True)
    save(root/'geometry.json', dict(models=rows, failures=failures, empty=empty))


def premultiply(image):
    a=image.astype(np.float32)
    a[:,:,:3]*=a[:,:,3:4]/255
    return a


def score(a,b,dx,dy,pixels):
    pa,pb=pixels[a],pixels[b]; ha,wa=pa.shape[:2];hb,wb=pb.shape[:2]
    if dx==wa:
        lo,hi=max(0,dy),min(ha,dy+hb)
        if hi-lo<4:return None
        ea,eb,ia,ib=pa[lo:hi,-1],pb[lo-dy:hi-dy,0],pa[lo:hi,-2],pb[lo-dy:hi-dy,1]
    elif dy==ha:
        lo,hi=max(0,dx),min(wa,dx+wb)
        if hi-lo<4:return None
        ea,eb,ia,ib=pa[-1,lo:hi],pb[0,lo-dx:hi-dx],pa[-2,lo:hi],pb[1,lo-dx:hi-dx]
    elif dx==-wb:return score(b,a,wb,-dy,pixels)
    elif dy==-hb:return score(b,a,-dx,hb,pixels)
    else:return None
    visible=(ea[:,3]>16)|(eb[:,3]>16)
    if visible.mean()<.25 or min(ea.std(0).mean(),eb.std(0).mean())<3:return None
    error=abs(ea[visible]-eb[visible]).mean()
    natural=(abs(ea[visible]-ia[visible]).mean()+abs(eb[visible]-ib[visible]).mean())/2
    return float(error+max(0,error-natural)*.5)


def normalized(pos,raw):
    minx=min(x for x,y in pos.values()); miny=min(y for x,y in pos.values())
    rows=[]; rects=[]
    for k,(x,y) in sorted(pos.items()):
        h,w=raw[k].shape[:2];x-=minx;y-=miny
        if any(x<X+W and x+w>X and y<Y+H and y+h>Y for X,Y,W,H in rects):return None
        rects.append((x,y,w,h));table,image,frame=map(int,k.split(':'))
        rows.append(dict(table=table,image=image,frame=frame,x=x,y=y,width=w,height=h))
    width=max(x+w for x,y,w,h in rects);height=max(y+h for x,y,w,h in rects)
    if width*height!=sum(w*h for x,y,w,h in rects) or max(width,height)>4096:return None
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()[:16]
    return dict(key=digest,width=width,height=height,pieces=rows)


def search(root,threshold=16):
    z=np.load(root/'pixels.npz');raw={k:z[k] for k in z.files if min(z[k].shape[:2])>=4}
    pixels={k:premultiply(v) for k,v in raw.items()}; found={}; links=[]; comparisons=0
    sequences=json.loads((root/'sequences.json').read_text())
    def animated_pair(a,b):
        ta,ia,_=a.split(':');tb,ib,_=b.split(':')
        return ta==tb and any(int(ia) in frames and int(ib) in frames for frames in sequences[ta])
    def offer(pos,evidence,user=None):
        keys=list(pos)
        if any(animated_pair(a,b) for i,a in enumerate(keys) for b in keys[i+1:]):return
        c=normalized(pos,raw)
        if not c or len(c['pieces'])<2:return
        c=found.setdefault(c['key'], dict(c,evidence=[],users=[]))
        if evidence not in c['evidence']:c['evidence'].append(evidence)
        if user and user not in c['users']:c['users'].append(user)
    geo=json.loads((root/'geometry.json').read_text(encoding='utf-8'))
    for model in geo['models']:
        for j in model['joins']:
            a,b=j['a'],j['b'];dx,dy=j['offset']
            if a not in raw or b not in raw or animated_pair(a,b):continue
            s=score(a,b,dx,dy,pixels)
            evidence='Shared UV boundary' if j['coplanar'] else 'Curved shared UV boundary (panorama review)'
            offer({a:(0,0),b:(dx,dy)},evidence,model['user'])
            if s is not None and s<threshold: links.append((s*.6,a,b,dx,dy))
    # Exact premultiplied squared-distance shortlist, then gradient-aware seam score.
    # Blocked matrices bound memory; no decoder/ROM work in the comparison loop.
    for axis in (0,1):
        groups=defaultdict(list)
        for k,p in pixels.items():
            edge=p[:,-1] if axis==0 else p[-1]
            if (edge[:,3]>16).mean()>=.25 and edge.std(0).mean()>=3:
                groups[p.shape[0] if axis==0 else p.shape[1]].append(k)
        for length,keys in groups.items():
            # Include low-information first edges only as targets; score rejects them.
            targets=[k for k,p in pixels.items() if (p.shape[0] if axis==0 else p.shape[1])==length]
            left=np.array([(pixels[k][:,-1] if axis==0 else pixels[k][-1]).ravel() for k in keys])
            right=np.array([(pixels[k][:,0] if axis==0 else pixels[k][0]).ravel() for k in targets])
            rn=(right*right).sum(1);best={};reverse={}
            for begin in range(0,len(keys),64):
                block=left[begin:begin+64]
                distances=(block*block).sum(1)[:,None]+rn[None,:]-2*(block@right.T)
                comparisons+=distances.size
                for row,a in enumerate(keys[begin:begin+64]):
                    distances[row,targets.index(a)]=np.inf
                    for col in np.argsort(distances[row])[:12]:
                        b=targets[col]
                        if animated_pair(a,b):continue
                        h,w=raw[a].shape[:2];dx,dy=(w,0) if axis==0 else (0,h)
                        s=score(a,b,dx,dy,pixels)
                        if s is None or s>threshold:continue
                        edge=(s,a,b,dx,dy)
                        if a not in best or s<best[a][0]:best[a]=edge
                        if b not in reverse or s<reverse[b][0]:reverse[b]=edge
            for a,edge in best.items():
                s,a,b,dx,dy=edge
                if reverse[b][1]!=a:continue
                links.append(edge);offer({a:(0,0),b:(dx,dy)},'Reciprocal pixel-edge match')
        print('Compared axis',axis,comparisons,'candidates',len(found),flush=True)
    clusters={k:{k:(0,0)} for k in raw};owner={k:k for k in raw}
    for s,a,b,dx,dy in sorted(links):
        ca,cb=owner[a],owner[b]
        if ca==cb:continue
        pa,pb=clusters[ca],clusters[cb];ax,ay=pa[a];bx,by=pb[b];tx,ty=ax+dx-bx,ay+dy-by
        merged=dict(pa);merged.update({k:(x+tx,y+ty) for k,(x,y) in pb.items()})
        rects=[];overlap=False
        for k,(x,y) in merged.items():
            h,w=raw[k].shape[:2]
            if any(x<X+W and x+w>X and y<Y+H and y+h>Y for X,Y,W,H in rects):overlap=True;break
            rects.append((x,y,w,h))
        if overlap:continue
        clusters[ca]=merged;del clusters[cb]
        for k in merged:owner[k]=ca
        offer(merged,'Merged UV / reciprocal edges; visual review required')
    rows=sorted(found.values(),key=lambda c:(-len(c['pieces']),c['key']))
    # Mark contained layouts before review, while retaining valid subgroups of rejected groups.
    for c in rows:
        for big in rows:
            if len(big['pieces'])<=len(c['pieces']):continue
            bp={(p['table'],p['image'],p['frame']):p for p in big['pieces']}
            cp=c['pieces'];first=cp[0];anchor=bp.get((first['table'],first['image'],first['frame']))
            if not anchor:continue
            dx=anchor['x']-first['x'];dy=anchor['y']-first['y']
            if all((q:=bp.get((p['table'],p['image'],p['frame']))) and q['x']==p['x']+dx and q['y']==p['y']+dy for p in cp):
                c['contained_by']=big['key'];break
    save(root/'candidates.json',rows)
    decisions=json.loads((root/'reviews.json').read_text()) if (root/'reviews.json').exists() else {}
    for c in rows:decisions.setdefault(c['key'],dict(status='unresolved',reason='Awaiting visual inspection'))
    save(root/'reviews.json',decisions)
    save(root/'search-metrics.json',dict(edge_comparisons=comparisons,candidates=len(rows),
        threshold=threshold,shortlist=12,covered_banks=[25,7,14],not_a_completeness_proof=True))


def sheets(root, pending=False):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage,QPainter,QColor
    from PySide6.QtCore import Qt
    app=QApplication.instance() or QApplication([])
    rows=json.loads((root/'candidates.json').read_text());z=np.load(root/'pixels.npz')
    if pending:
        reviews=json.loads((root/'reviews.json').read_text())
        rows=[c for c in rows if reviews[c['key']]['status']=='unresolved' and 'Reciprocal pixel-edge match' in c['evidence'] and 'contained_by' not in c]
        save(root/'pending-sheet-index.json',[c['key'] for c in rows])
    for page in range((len(rows)+23)//24):
        sheet=QImage(1440,1200,QImage.Format.Format_RGB32);sheet.fill(QColor('#383838'));painter=QPainter(sheet);painter.setPen(QColor('white'))
        for slot,c in enumerate(rows[page*24:(page+1)*24]):
            canvas=np.zeros((c['height'],c['width'],4),np.uint8)
            for part in c['pieces']:
                x,y,w,h=(part[v] for v in ('x','y','width','height'));key=source_key(part['table'],part['image'])
                canvas[y:y+h,x:x+w]=z[key]
            image=QImage(canvas.tobytes(),c['width'],c['height'],c['width']*4,QImage.Format.Format_RGBA8888).copy()
            image.save(str(root/f"candidate-{c['key']}.png"));x=(slot%6)*240;y=(slot//6)*300
            painter.drawText(x+4,y+16,f"{page*24+slot}: {c['key'][:8]} ({len(c['pieces'])})")
            painter.drawText(x+4,y+32,f"{c['width']}x{c['height']} "+','.join(f"{v['table']}:{v['image']:X}" for v in c['pieces'])[:27])
            painter.drawImage(x+4,y+40,image.scaled(230,250,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation))
        painter.end();sheet.save(str(root/f"{'pending' if pending else 'review'}-{page:03}.png"))
    print('Contact sheets', (len(rows)+23)//24,flush=True)


def publish(root,target):
    """Only explicit review decisions become runtime layouts. No image assets published."""
    from dk64_forge.texture_assemblies import Assembly,Piece,validate
    candidates=json.loads((root/'candidates.json').read_text());reviews=json.loads((root/'reviews.json').read_text())
    sources={r['key']:r for r in json.loads((root/'sources.json').read_text())['sources']}
    approved=[c for c in candidates if reviews[c['key']]['status']=='accepted']
    approved_keys={c['key'] for c in approved};records=[];digests=set();z=np.load(root/'pixels.npz')
    for c in approved:
        if c.get('contained_by') in approved_keys:continue
        pieces=[dict(p,usage=dict(sources[source_key(p['table'],p['image'])]['usage'],width=p['width'],height=p['height'])) for p in c['pieces']]
        decision=reviews[c['key']];name=decision.get('name',f"Connected artwork {c['key'][:8]}")
        spec=Assembly(c['key'],name,pieces[0]['table'],'assembled',c['width'],c['height'],tuple(Piece(**p) for p in pieces),'',())
        validate(spec)
        pixels=np.zeros((c['height'],c['width'],4),np.uint8)
        for p in pieces:
            x,y,w,h=(p[v] for v in ('x','y','width','height'));pixels[y:y+h,x:x+w]=z[source_key(p['table'],p['image'])]
        digest=hashlib.sha256(str(pixels.shape).encode()+pixels.tobytes()).hexdigest()
        if digest in digests:continue
        digests.add(digest)
        records.append(dict(key=c['key'],name=name,width=c['width'],height=c['height'],pieces=pieces,
            evidence='; '.join(c['evidence'])+'; visual review: '+decision['reason'],users=c['users']))
    old=json.loads(target.read_text(encoding='utf-8')) if target.exists() else dict(version=2,images=[])
    # Preserve separately reviewed seed layouts; deduplicate exact layouts.
    signature=lambda row:json.dumps([{k:p[k] for k in ('table','image','frame','x','y','width','height')} for p in row['pieces']],sort_keys=True)
    seen={signature(r) for r in records}
    records.extend(r for r in old['images'] if r['key'] in ('ship-hatch','warp-top','chunky-face','tiny-face','lanky-face','diddy-face','dk-face') and signature(r) not in seen)
    save(target,dict(version=2,images=sorted(records,key=lambda r:r['name'])))
    active=[reviews[c['key']] for c in candidates]
    stats=dict(runtime_images=len(records),accepted=sum(v['status']=='accepted' for v in active),
        rejected=sum(v['status']=='rejected' for v in active),unresolved=sum(v['status']=='unresolved' for v in active),
        retired_decisions=len(reviews)-len(active))
    save(root/'review-metrics.json',stats);print(stats,flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('rom',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stage',choices=('all','decode','geometry','search','sheets','review','publish'),default='all');parser.add_argument('--threshold',type=float,default=16)
    parser.add_argument('--pending',action='store_true');parser.add_argument('--catalog',type=Path,default=Path('dk64_forge/data/connected_textures.json'))
    parser.add_argument('--key');parser.add_argument('--decision',choices=('accepted','rejected','unresolved'));parser.add_argument('--reason');parser.add_argument('--name')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    rom=load_rom(args.rom).normalized if args.stage in ('all','decode','geometry') else None
    if args.stage=='geometry':
        expected=json.loads((args.output/'sources.json').read_text())['rom_sha256']
        if hashlib.sha256(rom).hexdigest()!=expected:parser.error('Cache belongs to another ROM; decode first')
    for stage,run in (('decode',lambda:decode(rom,args.output)),('geometry',lambda:geometry(rom,args.output)),('search',lambda:search(args.output,args.threshold)),('sheets',lambda:sheets(args.output,args.pending))):
        if args.stage in ('all',stage):run()
    if args.stage=='publish':publish(args.output,args.catalog)
    if args.stage=='review':
        rows=json.loads((args.output/'reviews.json').read_text())
        if args.key not in rows or not args.decision or not args.reason:parser.error('Review requires known --key, --decision and --reason')
        rows[args.key]=dict(status=args.decision,reason=args.reason)
        if args.name:rows[args.key]['name']=args.name
        save(args.output/'reviews.json',rows)


if __name__=='__main__':main()
