from cwhost import loader, pef
from cwhost.guest import Guest


def test_load_compiler_fragment(compiler_83):
    guest = Guest()
    parsed = pef.PEF.from_bytes(compiler_83.read_bytes())
    fragment = loader.load(
        guest,
        compiler_83,
        {item.name: guest.alloc_trap_slot(item.name) for item in parsed.imports},
    )
    transition_vector = fragment.tvector("CWPlugin_GetDropInFlags")
    code, toc = guest.u32(transition_vector), guest.u32(transition_vector + 4)
    assert code - fragment.section_base[0] == 0x101539C0 - 0x10000000
    assert toc - fragment.section_base[1] == 0x10190F30 - 0x10188F30
