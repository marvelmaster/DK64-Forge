"""Exact actor-dispatch call sites from DK64 US ROM and CC0 decomp source.

Generated constants retain source/ROM evidence. Labels describe script context,
not official clip names. Movement ordinals are not inferred idle/walk semantics.
"""

CALLS = {
    0x16: (
        ('script', 0x2A3, 'Seal action', 'global_asm/code_C2A90.c:960'),
        ('script', 0x2A4, 'Seal action', 'global_asm/code_C2A90.c:979'),
        ('script', 0x2A6, 'Seal action', 'global_asm/code_C2A90.c:995'),
    ),
    0x19: (
        ('script', 0x1F8, 'Beaver Blue movement 1', 'ROM 806AD58C -> 8072B79C'),
        ('script', 0x1F9, 'Beaver Blue defeat', 'ROM 806AD58C -> 8072B79C'),
    ),
    0x1A: (
        ('script', 0x1F8, 'Beaver Gold movement 1', 'ROM 806AD7E8 -> 8072B79C'),
        ('script', 0x1F9, 'Beaver Gold defeat', 'ROM 806AD7E8 -> 8072B79C'),
    ),
    0x1F: (
        ('script', 0x24F, 'Kaboom action', 'global_asm/code_BA790.c:226'),
    ),
    0x23: (
        ('script', 0x1F8, 'Klaptrap Teeth movement 1', 'ROM 806AD7E8 -> 8072B79C'),
        ('script', 0x1F9, 'Klaptrap Teeth defeat', 'ROM 806AD7E8 -> 8072B79C'),
    ),
    0x26: (
        ('script', 0x28E, 'Troff action', 'global_asm/code_C2A90.c:64'),
        ('script', 0x28F, 'Troff action', 'global_asm/code_C2A90.c:73'),
    ),
    0x27: (
        ('script', 0x21B, 'Toy Monster action', 'ROM 806BB4B4 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_BDEE0.c:470'),
        ('script', 0x21C, 'Toy Monster action', 'global_asm/code_BDEE0.c:492'),
    ),
    0x2B: (
        ('script', 0x321, 'Robo Kremling action', 'ROM 806B9520 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_BDEE0.c:82'),
        ('script', 0x323, 'Robo Kremling defeat', 'ROM 806B922C -> 8072B79C'),
        ('script', 0x324, 'Robo Kremling action', 'ROM 806B9730 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_BDEE0.c:105'),
    ),
    0x2C: (
        ('script', 0x28A, 'Scoff action', 'global_asm/code_C2A90.c:113'),
        ('script', 0x28B, 'Scoff action', 'global_asm/code_C2A90.c:164'),
    ),
    0x30: (
        ('script', 0x1FC, 'Kremling defeat', 'ROM 806AE5BC -> 8072B79C'),
        ('script', 0x1FD, 'Kremling action', 'ROM 806AE964 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:159'),
        ('script', 0x1FE, 'Kremling action', 'ROM 806AEAB0 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:181'),
    ),
    0x31: (
        ('clip', 0x407, 'Kosh Kremling Red action', 'global_asm/code_A2F10.c:63; global_asm/code_A2F10.c:72'),
    ),
    0x33: (
        ('script', 0x2BA, 'Mechanical Fish action', 'global_asm/done/code_CB230.c:205'),
        ('script', 0x2BB, 'Mechanical Fish action', 'global_asm/done/code_CB230.c:195'),
    ),
    0x35: (
        ('script', 0x35D, 'Giant Clam action', 'global_asm/code_B7490.c:250'),
    ),
    0x39: (
        ('script', 0x2AC, 'Klump defeat', 'ROM 806AEEF4 -> 8072B79C'),
        ('script', 0x2B0, 'Klump action', 'ROM 806AF100 -> 80614EBC; actor = gCurrentActorPointer'),
        ('script', 0x2B4, 'Klump action', 'ROM 806AF238 -> 80614EBC; actor = gCurrentActorPointer'),
    ),
    0x3C: (
        ('script', 0x2B5, 'Banana Fairy action', 'ROM 806C5E74 -> 80614EBC; actor = gCurrentActorPointer; global_asm/done/code_C8C10.c:673'),
        ('script', 0x2B6, 'Banana Fairy action', 'global_asm/done/code_C8C10.c:668'),
    ),
    0x3D: (
        ('script', 0x2BD, 'Llama action', 'global_asm/code_C2A90.c:854'),
        ('script', 0x2BE, 'Llama action', 'global_asm/code_C2A90.c:898'),
        ('script', 0x2BF, 'Llama action', 'global_asm/code_C2A90.c:924'),
    ),
    0x3E: (
        ('script', 0x2C0, 'Guard movement 1', 'ROM 806AF6BC -> 8072B79C; ROM 806AF80C -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:297'),
        ('script', 0x2C1, 'Guard defeat', 'ROM 806AF6BC -> 8072B79C'),
    ),
    0x41: (
        ('script', 0x2D9, 'Krossbones action', 'ROM 806AFE18 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:415'),
        ('script', 0x2DA, 'Krossbones action', 'ROM 806AFFEC -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:444'),
        ('script', 0x2DB, 'Krossbones action', 'global_asm/code_B2CE0.c:442'),
    ),
    0x44: (
        ('script', 0x2F0, 'KLumsy action', 'ROM 806BD6BC -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_C1E70.c:163'),
        ('script', 0x2F1, 'KLumsy action', 'ROM 806BD638 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_C1E70.c:150'),
    ),
    0x45: (
        ('script', 0x2F4, 'Spider defeat', 'ROM 806ADA38 -> 8072B79C; ROM 806ADA38 -> 8072B79C'),
        ('script', 0x2F5, 'Spider action', 'ROM 806ADCC0 -> 80614EBC; actor = gCurrentActorPointer; global_asm/done/code_B1F60.c:210'),
        ('script', 0x2F8, 'Spider action', 'ROM 806ADA70 -> 80614EBC; actor = gCurrentActorPointer; global_asm/done/code_B1F60.c:170'),
    ),
    0x46: (
        ('script', 0x300, 'Rabbit action', 'global_asm/done/code_B6C50.c:28'),
        ('script', 0x302, 'Rabbit action', 'global_asm/code_C2A90.c:301'),
        ('script', 0x305, 'Rabbit action', 'global_asm/code_C2A90.c:395'),
        ('script', 0x306, 'Rabbit action', 'global_asm/code_C2A90.c:248'),
        ('script', 0x307, 'Rabbit action', 'global_asm/code_C2A90.c:256; global_asm/done/code_B6C50.c:82'),
        ('script', 0x308, 'Rabbit action', 'global_asm/code_C2A90.c:333'),
        ('script', 0x309, 'Rabbit action', 'global_asm/code_C2A90.c:278'),
    ),
    0x47: (
        ('clip', 0x597, 'Beanstalk action', 'global_asm/done/code_A6280.c:116'),
        ('clip', 0x598, 'Beanstalk action', 'global_asm/done/code_A6280.c:120'),
        ('script', 0x328, 'Beanstalk action', 'global_asm/done/code_A6280.c:143'),
    ),
    0x49: (
        ('clip', 0x599, 'Fireball With Glasses action', 'global_asm/done/code_B6C50.c:109'),
    ),
    0x4B: (
        ('script', 0x30D, 'Skeleton Hand defeat', 'ROM 806B5170 -> 8072B79C'),
    ),
    0x4C: (
        ('script', 0x244, 'Vulture_76 action', 'global_asm/done/code_C8C10.c:360; global_asm/done/code_C8C10.c:371; global_asm/done/code_C8C10.c:410; global_asm/done/code_C8C10.c:452'),
        ('script', 0x245, 'Vulture_76 action', 'global_asm/done/code_C8C10.c:389'),
    ),
    0x4D: (
        ('script', 0x248, 'Vulture_77 action', 'global_asm/code_B7490.c:751'),
        ('script', 0x249, 'Vulture_77 action', 'global_asm/code_B7490.c:780'),
    ),
    0x4E: (
        ('script', 0x30D, 'Bat defeat', 'ROM 806B5170 -> 8072B79C'),
    ),
    0x51: (
        ('script', 0x31E, 'Ghost movement 2', 'ROM 806B031C -> 8072B79C'),
        ('script', 0x31F, 'Ghost defeat', 'ROM 806B031C -> 8072B79C'),
        ('script', 0x320, 'Ghost movement 1', 'ROM 806B031C -> 8072B79C'),
    ),
    0x52: (
        ('script', 0x331, 'Fly action', 'global_asm/code_B7490.c:890'),
    ),
    0x55: (
        ('script', 0x355, 'Owl action', 'global_asm/done/code_C8C10.c:591'),
        ('script', 0x357, 'Owl movement 1', 'ROM 806C5648 -> 8072B79C; global_asm/done/code_C8C10.c:558'),
        ('script', 0x358, 'Owl action', 'global_asm/done/code_C8C10.c:545'),
        ('script', 0x359, 'Owl defeat', 'ROM 806C5648 -> 8072B79C'),
        ('script', 0x35A, 'Owl action', 'global_asm/done/code_C8C10.c:611'),
        ('script', 0x35B, 'Owl action', 'global_asm/done/code_C8C10.c:619'),
    ),
    0x5A: (
        ('script', 0x3A4, 'Mermaid action', 'global_asm/code_C2A90.c:1184'),
    ),
    0x5B: (
        ('script', 0x379, 'Mushroom action', 'global_asm/code_B2CE0.c:562'),
        ('script', 0x37C, 'Mushroom action', 'global_asm/code_B2CE0.c:571'),
    ),
    0x60: (
        ('script', 0x35E, 'Kosha action', 'ROM 806B0F84 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:754'),
        ('script', 0x360, 'Kosha defeat', 'ROM 806B0B14 -> 8072B79C'),
        ('script', 0x363, 'Kosha action', 'ROM 806B0EDC -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_B2CE0.c:737'),
    ),
    0x61: (
        ('script', 0x31C, 'Ice Tomato action', 'ROM 806BC56C -> 80614EBC; actor = gCurrentActorPointer; ROM 806BC710 -> 80614EBC; actor = gCurrentActorPointer; global_asm/IceTomato.c:139; global_asm/IceTomato.c:177; global_asm/IceTomato.c:244; global_asm/IceTomato.c:266'),
    ),
    0x63: (
        ('clip', 0x63F, 'Boombox action', 'global_asm/done/code_A6280.c:194'),
        ('clip', 0x640, 'Boombox action', 'ROM 806A1F8C -> 80613C48; actor = gCurrentActorPointer; global_asm/done/code_A6280.c:192'),
    ),
    0x7B: (
        ('clip', 0x346, 'Cannon_123 action', 'global_asm/code_80150.c:102'),
    ),
    0x85: (
        ('clip', 0x404, 'Feather action', 'global_asm/code_936B0.c:1410'),
        ('clip', 0x405, 'Feather action', 'global_asm/code_936B0.c:1455'),
        ('clip', 0x406, 'Feather action', 'global_asm/code_936B0.c:1436'),
    ),
    0x8C: (
        ('clip', 0x482, 'Photo action', 'global_asm/done/code_BD820.c:108'),
    ),
    0x97: (
        ('script', 0x28E, 'Bananaporter Zipper action', 'global_asm/code_C2A90.c:64'),
        ('script', 0x28F, 'Bananaporter Zipper action', 'global_asm/code_C2A90.c:73'),
    ),
    0x9D: (
        ('script', 0x3DC, 'Toy Box action', 'ROM 806BB90C -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_BDEE0.c:600'),
        ('script', 0x3DD, 'Toy Box action', 'global_asm/code_BDEE0.c:619'),
    ),
    0x9F: (
        ('clip', 0x482, 'Padlock action', 'global_asm/done/code_BD820.c:108'),
    ),
    0xD6: (
        ('script', 0x291, 'Barrel_214 action', 'ROM 806BD8E4 -> 80614EBC; actor = gCurrentActorPointer; global_asm/code_C1E70.c:255'),
    ),
}
