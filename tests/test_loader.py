from cwhost import loader, pef
from cwhost.guest import Guest


def test_load_compiler_fragment(compiler_plugin):
    guest = Guest()
    parsed = pef.PEF.from_bytes(compiler_plugin.read_bytes())
    fragment = loader.load(
        guest,
        compiler_plugin,
        {item.name: guest.alloc_trap_slot(item.name) for item in parsed.imports},
    )
    transition_vector = fragment.tvector("CWPlugin_GetDropInFlags")
    code, toc = guest.u32(transition_vector), guest.u32(transition_vector + 4)
    # The unrelocated transition vector in the PEF image holds the code and TOC
    # offsets; loading must turn them into section-relative addresses.
    section, offset = parsed.exports()["CWPlugin_GetDropInFlags"]
    image = parsed.section_image(section)
    assert code - fragment.section_base[0] == pef.u32(image, offset)
    assert toc - fragment.section_base[1] == pef.u32(image, offset + 4)
