"""Table-14 layouts from font draw commands and the ROM font-file table.

806FBEF0 uses 80754A34 (8 six-byte rows: first file, count, glyph height,
kerning). Dimensions and formats below are the actual tile/load commands in
code_100180.c, not inferred from byte lengths. 80703CF8 loads entry 5F as I4.
"""
from .control_states import global_asm_data, GLOBAL_ASM_DATA_VRAM


def usages(rom):
    from .texture_bank import TextureUsage, table_entry, SIZES
    data = global_asm_data(rom)
    at = 0x80754A34-GLOBAL_ASM_DATA_VRAM
    # G_SETTILESIZE gives atlas dimensions, not glyph dimensions.
    layouts = ((3,0,512,16),(0,2,76,24),(4,0,512,8),(0,2,32,32),
               (4,0,1024,8),(4,0,1024,8),(3,1,176,13),(0,2,32,32))
    result = {}
    for style, (fmt,size,width,height) in enumerate(layouts):
        first,count = data[at+style*6:at+style*6+2]
        if first+count > 43:
            raise ValueError("HUD font table is outside its 43 cached files")
        for index in range(first,first+count):
            result.setdefault(index,[]).append(TextureUsage(fmt,size,width,height,False,None,f"HUD font style {style} (806FBEF0)"))
    result[0x5F] = [TextureUsage(4,0,64,64,False,None,"HUD overlay (80703CF8)")]
    # Literal displayImage arguments in the original draw routines. Dynamic
    # ranges below have explicit bounded index arithmetic in those routines.
    layouts = (
        ((0x2C,),0,2,224,128,"DKTV, code_117880"),
        ((0x35,0x38,0x3A,0x3C,0x44,0x45),3,1,64,64,"HUD icons, code_103AB0"),
        ((0x4A,),3,1,16,16,"Race icon, race/code_7BD0"),
        ((0x4B,),3,1,64,64,"Race icon, race/code_7BD0"),
        (range(0x5A,0x5E),0,2,48,42,"Health, code_10D2D0"),
        ((0x60,),4,0,64,64,"Multiplayer overlay, multiplayer/code_0"),
        (range(0x61,0x6B),0,2,16,32,"Overlay tile, code_10E1D0"),
        (range(0x75,0x7F),0,2,40,51,"Character overlay, code_A2F10 (unk15F 20..29)"),
        (range(0x83,0x8F),0,2,32,16,"Animated overlay, code_910A0"),
        (range(0x8F,0x9F),0,2,32,32,"Animated overlay, code_910A0"),
        ((0x9F,),0,2,32,32,"HUD icon, code_ACDC0"),
        ((0xA0,),3,0,32,32,"Menu marker, menu/code_3E10"),
        ((0xA1,),3,0,64,64,"Menu marker, menu/code_3E10"),
        ((0xA2,),0,2,32,32,"Menu icon, menu/code_0"),
        ((0xA3,),4,0,240,150,"Blueprint atlas, menu/code_0"),
    )
    for indices,fmt,size,width,height,evidence in layouts:
        for index in indices:
            raw=table_entry(rom,14,index)
            if raw is None or len(raw) < (width*height*SIZES[size]+7)//8:
                continue  # C routes with bounds that do not fit this ROM are not proof.
            result.setdefault(index,[]).append(TextureUsage(fmt,size,width,height,False,None,f"HUD {evidence}"))
    return result
