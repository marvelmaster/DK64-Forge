"""Exact split images and lossless packed atlases of model-used textures.

An atlas is a new packing, not a recovered original sheet. Geometry UVs are
unchanged; the JSON layout records the source rectangles for consumers.
"""
from dataclasses import dataclass
from math import ceil, sqrt
import numpy as np
from .core import texture_bank

@dataclass(frozen=True)
class Piece:
    image: int
    x: int
    y: int
    width: int
    height: int

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

# Original assets only; no modified-ROM replacement texture indices.
SPLITS = (
    ('ship-hatch','Ship hatch / barrel face',(0x610,0x60F),32,64,
     'Map 43 shared mesh edge: 0610 right UV edge meets 060F left UV edge.'),
    ('warp-top','Bananaport pad top',(0xDF9,0xDFA),32,64,
     'DK64 Randomizer pull_images_from_rom.py: w1_pad_left / w1_pad_right.'),
    ('chunky-face','Chunky face',(0x273,0x274),32,64,
     'DK64 Randomizer Holiday.py: face_left / face_right.'),
    ('tiny-face','Tiny face',(0x276,0x275),32,64,
     'DK64 Randomizer Holiday.py: face_left / face_right.'),
    ('lanky-face','Lanky face',(0x277,0x278),32,64,
     'DK64 Randomizer Holiday.py: face_left / face_right.'),
    ('diddy-face','Diddy face',(0x279,0x27A),32,64,
     'DK64 Randomizer Holiday.py: face_left / face_right.'),
    ('dk-face','Donkey Kong face',(0x27C,0x27B),32,64,
     'DK64 Randomizer Holiday.py: face_left / face_right.'),
)

def catalog(items, table):
    lookup={item.index:item for item in items}
    result=[]
    if table==25:
        for key,label,images,width,height,evidence in SPLITS:
            if all(i in lookup and lookup[i].usage and
                   (lookup[i].usage.width,lookup[i].usage.height)==(width,height) for i in images):
                result.append(Assembly(key,label,table,'assembled',width*len(images),height,
                    tuple(Piece(i,x*width,0,width,height) for x,i in enumerate(images)),evidence,
                    tuple(user for user in lookup[images[0]].references if all(user in lookup[i].references for i in images))))
    groups={}
    for item in items:
        if item.usage is None:continue
        for user in item.references:
            if user.startswith(('actor ','prop ')):
                groups.setdefault(user,[]).append(item)
    for user,rows in sorted(groups.items()):
        if len(rows)<2:continue
        # Stable source-ID ordering, power-of-two-free shelves and two-pixel gutters.
        rows=sorted(rows,key=lambda i:i.index)
        width=max(max(i.usage.width for i in rows)+4,
                  ceil(sqrt(sum((i.usage.width+2)*(i.usage.height+2) for i in rows))))
        x=y=2;line=0;pieces=[]
        for item in rows:
            w,h=item.usage.width,item.usage.height
            if x+w+2>width:x=2;y+=line+2;line=0
            pieces.append(Piece(item.index,x,y,w,h));x+=w+2;line=max(line,h)
        label=texture_bank.user_display_name(user,{})
        result.append(Assembly('atlas-'+user.replace(' ','-'),label+' — model texture atlas',table,
            'atlas',width,y+line+2,tuple(pieces),'Packed from recorded ROM model texture usages; not original flat artwork.',(user,)))
    return tuple(result)

def related(assemblies, image):
    return tuple(a for a in assemblies if any(p.image==image for p in a.pieces))

def compose(rom, assembly, lookup):
    if not 0<assembly.width<=8192 or not 0<assembly.height<=8192:
        raise ValueError('Texture assembly dimensions are too large')
    pixels=np.zeros((assembly.height,assembly.width,4),dtype=np.uint8);missing=[]
    for piece in assembly.pieces:
        if piece.x<0 or piece.y<0 or piece.x+piece.width>assembly.width or piece.y+piece.height>assembly.height:
            raise ValueError('Texture piece exceeds its assembly')
        item=lookup.get(piece.image)
        decoded=texture_bank.decode_item(rom,item) if item else None
        if decoded is None:
            if assembly.kind=='assembled':raise ValueError(f'Texture {piece.image:04X} cannot be decoded')
            missing.append(piece.image)
            pixels[piece.y:piece.y+piece.height,piece.x:piece.x+piece.width]=(255,0,255,255)
            continue
        w,h,rgba=decoded
        if (w,h)!=(piece.width,piece.height):raise ValueError('Source texture dimensions changed')
        pixels[piece.y:piece.y+h,piece.x:piece.x+w]=np.frombuffer(rgba,dtype=np.uint8).reshape(h,w,4)
    return assembly.width,assembly.height,pixels.tobytes(),tuple(missing)

def layout(assembly, missing=()):
    return dict(version=1,name=assembly.name,kind=assembly.kind,table=assembly.table,
        width=assembly.width,height=assembly.height,evidence=assembly.evidence,
        coordinate_origin='top-left; original decoded row order',
        original_model_uvs_unchanged=True,missing=list(missing),
        pieces=[dict(image=p.image,x=p.x,y=p.y,width=p.width,height=p.height) for p in assembly.pieces])
