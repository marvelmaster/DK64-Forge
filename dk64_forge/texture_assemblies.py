"""Reviewed contiguous artwork only. All source pixels are copied unchanged."""
from dataclasses import dataclass
import json
from pathlib import Path
import numpy as np
from .core import texture_bank

@dataclass(frozen=True)
class Piece:
    image: int
    x: int
    y: int
    width: int
    height: int
    table: int = 25
    frame: int = 0
    usage: dict | None = None

@dataclass(frozen=True)
class Assembly:
    key: str
    name: str
    table: int
    kind: str
    width: int
    height: int
    pieces: tuple[Piece, ...]
    evidence: str
    users: tuple[str, ...] = ()


def catalog(items, table):
    """Read the reviewed catalog; common model membership never creates a layout."""
    result=[]
    records=json.loads(Path(__file__).with_name('data').joinpath('connected_textures.json').read_text(encoding='utf-8'))['images']
    for row in records:
        pieces=tuple(Piece(**p) for p in row['pieces'])
        if table not in {p.table for p in pieces}:continue
        result.append(Assembly(row['key'],row['name'],pieces[0].table,'assembled',
            row['width'],row['height'],pieces,row['evidence'],tuple(row.get('users',()))))
    return tuple(result)


def related(assemblies, image):
    return tuple(a for a in assemblies if any(p.image==image for p in a.pieces))


def validate(assembly):
    if any(not isinstance(v,int) or isinstance(v,bool) for v in (assembly.width,assembly.height)) or not 0<assembly.width<=8192 or not 0<assembly.height<=8192:
        raise ValueError('Invalid texture assembly dimensions')
    if assembly.kind!='assembled' or len(assembly.pieces)<2:
        raise ValueError('Only contiguous multi-part artwork is supported')
    coverage=np.zeros((assembly.height,assembly.width),dtype=np.uint8)
    for p in assembly.pieces:
        values=(p.image,p.table,p.frame,p.x,p.y,p.width,p.height)
        if any(not isinstance(v,int) or isinstance(v,bool) for v in values):raise ValueError('Piece fields must be integers')
        # Each animated source is a separate ROM entry. Frame is currently zero;
        # reject unsupported frame requests instead of silently exporting frame 0.
        if p.table not in (7,14,25) or p.image<0 or p.frame!=0:raise ValueError('Invalid source bank, index or frame')
        if p.width<=0 or p.height<=0 or p.x<0 or p.y<0 or p.x+p.width>assembly.width or p.y+p.height>assembly.height:
            raise ValueError('Texture piece exceeds its assembly')
        rect=coverage[p.y:p.y+p.height,p.x:p.x+p.width]
        if rect.any():raise ValueError('Texture pieces overlap')
        rect[:]=1
    if not coverage.all():raise ValueError('Texture assembly has gaps')


def compose(rom, assembly, lookup):
    validate(assembly)
    pixels=np.empty((assembly.height,assembly.width,4),dtype=np.uint8)
    for p in assembly.pieces:
        item=lookup.get((p.table,p.image)) or lookup.get(p.image)
        if item is not None and item.table!=p.table:item=None
        if p.usage is not None:
            descriptor=dict(p.usage)
            width=descriptor.pop('width',p.width);height=descriptor.pop('height',p.height)
            if (width,height)!=(p.width,p.height):raise ValueError('Source texture dimensions changed')
            usage=texture_bank.TextureUsage(width=width,height=height,user='reviewed connected artwork',**descriptor)
            raw=texture_bank.table_entry(rom,p.table,p.image)
            palette=texture_bank.table_entry(rom,p.table,usage.palette) if usage.palette is not None else None
            rgba=texture_bank.decode(raw,usage,palette) if raw else None
            decoded=(p.width,p.height,rgba) if rgba else None
        else:
            decoded=texture_bank.decode_item(rom,item) if item else None
        if decoded is None:raise ValueError(f'Texture {p.table}:{p.image:04X} cannot be decoded')
        w,h,rgba=decoded
        if (w,h)!=(p.width,p.height) or len(rgba)!=w*h*4:raise ValueError('Source texture dimensions changed')
        source=np.frombuffer(rgba,dtype=np.uint8).reshape(h,w,4)
        for y in range(h):pixels[p.y+y,p.x:p.x+w]=source[y]
    return assembly.width,assembly.height,pixels.tobytes(),()


def layout(assembly, missing=()):
    validate(assembly)
    return dict(version=2,key=assembly.key,name=assembly.name,kind=assembly.kind,
        width=assembly.width,height=assembly.height,evidence=assembly.evidence,
        coordinate_origin='top-left; original decoded row order',
        pieces=[dict(table=p.table,image=p.image,frame=p.frame,x=p.x,y=p.y,width=p.width,height=p.height,usage=p.usage) for p in assembly.pieces])
