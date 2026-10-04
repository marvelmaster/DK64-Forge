"""Table-14 layouts from font draw commands and the ROM font-file table.

806FBEF0 uses 80754A34 (8 six-byte rows: first file, count, glyph height,
kerning). Dimensions and formats below are the actual tile/load commands in
code_100180.c, not inferred from byte lengths. 80703CF8 loads entry 5F as I4.
"""
from .control_states import global_asm_data, GLOBAL_ASM_DATA_VRAM


def usages(rom):
    from .texture_bank import TextureUsage
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
    return result
