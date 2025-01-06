#!/bin/env python3
import argparse
import collections
import re
import sys

# https://devicetree-specification.readthedocs.io/en/v0.3/flattened-format.html#header
# struct fdt_header
HEADER_MAGIC = 0
HEADER_TOTALSIZE = 4
HEADER_OFF_DT_STRUCT = 8
HEADER_OFF_DT_STRINGS = 12
HEADER_OFF_MEM_RSVMAP = 16
HEADER_VERSION = 20
HEADER_LAST_COMP_VERSION = 24
HEADER_BOOT_CPUID_PHYS = 28
HEADER_SIZE_DT_STRINGS = 32
HEADER_SIZE_DT_STRUCT = 36

# https://devicetree-specification.readthedocs.io/en/v0.3/flattened-format.html#structure-block
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

TOKEN_TYPE_TO_STRING = {
    FDT_BEGIN_NODE: "BEGIN_NODE",
    FDT_END_NODE: "END_NODE",
    FDT_PROP: "PROP",
    FDT_NOP: "NOP",
    FDT_END: "END",
}

BeginNodeToken = collections.namedtuple("BeginNodeToken", "type, name, path")
EndNodeToken = collections.namedtuple("EndNodeToken", "type")
PropToken = collections.namedtuple("PropToken", "type, name, value")
NopToken = collections.namedtuple("NopToken", "type")
EndToken = collections.namedtuple("EndToken", "type")


def read_uint32(buf: bytes) -> int:
    # This is not the same thing as int.from_bytes(). If the sign bit is set, it raises an
    # exception. That means it can only parse numbers up to 2^31-1.

    ret = (buf[0] << 24) + (buf[1] << 16) + (buf[2] << 8) + (buf[3] << 0)
    assert ret == int.from_bytes(buf[:4])
    return ret


def read_uint64(buf: bytes) -> int:
    ret = (
        (buf[0] << 56)
        + (buf[1] << 48)
        + (buf[2] << 40)
        + (buf[3] << 32)
        + (buf[4] << 24)
        + (buf[5] << 16)
        + (buf[6] << 8)
        + (buf[7] << 0)
    )
    assert ret == int.from_bytes(buf[:8])
    return ret


Header = collections.namedtuple(
    "Header",
    "magic, totalsize, off_dt_struct, off_dt_strings, off_mem_rsvmap, version, "
    + "last_comp_version, boot_cpuid_phys, size_dt_strings, size_dt_struct",
)


def get_header(do_debug: bool, buf: bytes):
    def get(off: int):
        return read_uint32(buf[off : off + 4])

    hdr = Header(
        magic=get(HEADER_MAGIC),
        totalsize=get(HEADER_TOTALSIZE),
        off_dt_struct=get(HEADER_OFF_DT_STRUCT),
        off_dt_strings=get(HEADER_OFF_DT_STRINGS),
        off_mem_rsvmap=get(HEADER_OFF_MEM_RSVMAP),
        version=get(HEADER_VERSION),
        last_comp_version=get(HEADER_LAST_COMP_VERSION),
        boot_cpuid_phys=get(HEADER_BOOT_CPUID_PHYS),
        size_dt_strings=get(HEADER_SIZE_DT_STRINGS),
        size_dt_struct=get(HEADER_SIZE_DT_STRUCT),
    )

    for field in hdr._fields:
        debug_print(do_debug, field, hex(hdr._asdict()[field]))

    assert hdr.magic == 0xD00DFEED
    assert hdr.totalsize == len(buf)
    assert hdr.version >= 17, "only version >=17 is supported"
    assert hdr.last_comp_version == 16
    SECTION_ORDERING_ASSERT_MSG = (
        "sections MUST be ordered as: memory reservation THEN structure THEN strings"
    )
    assert hdr.off_mem_rsvmap < hdr.off_dt_struct, SECTION_ORDERING_ASSERT_MSG
    assert hdr.off_dt_struct < hdr.off_dt_strings, SECTION_ORDERING_ASSERT_MSG
    assert hdr.off_dt_strings + hdr.size_dt_strings <= hdr.totalsize
    assert hdr.off_dt_struct + hdr.size_dt_struct <= hdr.off_dt_strings
    assert (
        hdr.off_mem_rsvmap % 8 == 0
    ), "memory reservation block MUST be 8-byte aligned"
    assert hdr.off_dt_struct % 4 == 0, "structure block MUST be 4-byte aligned"

    return hdr


def get_reserve_entries(buf: bytes, hdr: Header):
    for off in range(hdr.off_mem_rsvmap, hdr.off_dt_struct, 16):
        address = read_uint64(buf[off : off + 8])
        size = read_uint64(buf[off + 8 : off + 16])
        if address == 0 and size == 0:
            break
        yield (address, size)


def get_structure_tokens(do_debug: bool, buf: bytes, hdr: Header):
    off = hdr.off_dt_struct
    node_depth = 0
    parent_nodes = []
    while off < hdr.off_dt_strings and off < hdr.off_dt_struct + hdr.size_dt_struct:
        token_type = read_uint32(buf[off : off + 4])
        off += 4

        # TODO: assert that if a node has no reg it has no unit-address

        # TODO: assert that props are before child nodes

        if token_type == FDT_BEGIN_NODE:
            name_len = buf[off:].find(b"\0")
            name = buf[off : off + name_len].decode()
            off += name_len + 4 - name_len % 4

            parent_nodes.append(name)

            if node_depth == 0:
                assert name == "", "root node must NOT have a name"
                path = "/"
            else:
                assert re.match(
                    r"^[0-9a-zA-Z,\._\+\-]{1,31}(@[0-9a-zA-Z,\._\+\-]+)?$", name
                )
                path = "/".join(parent_nodes)

            token = BeginNodeToken(type=token_type, name=name, path=path)
            node_depth += 1
        elif token_type == FDT_END_NODE:
            token = EndNodeToken(type=token_type)
            parent_nodes.pop()
            node_depth -= 1
            assert node_depth >= 0
        elif token_type == FDT_PROP:
            assert node_depth > 0

            prop_len = read_uint32(buf[off : off + 4])
            name_off = read_uint32(buf[off + 4 : off + 8])
            off += 8

            name_buf = buf[hdr.off_dt_strings + name_off :]
            name = name_buf[: name_buf.find(b"\0")].decode()

            value = buf[off : off + prop_len]
            off += prop_len
            if prop_len % 4 != 0:
                off += 4 - prop_len % 4

            token = PropToken(token_type, name, value)
        elif token_type == FDT_NOP:
            token = NopToken(token_type)
        elif token_type == FDT_END:
            assert off == hdr.off_dt_struct + hdr.size_dt_struct
            assert node_depth == 0
            token = EndToken(token_type)
        else:
            assert False, f"unknown token type {token_type}"

        if do_debug:
            type = TOKEN_TYPE_TO_STRING[token_type]
            debug_print(do_debug, f"token {type}: {token}")

        yield token


def get_props_of_node(tokens, path):
    if isinstance(path, bytes):
        index = path.find(b"\0")
        assert index >= 0
        path = path[:index].decode()

    path_stack = []
    for token in tokens:
        if token.type == FDT_BEGIN_NODE:
            path_stack.append(token.path)
        elif token.type == FDT_END_NODE:
            path_stack.pop()

        if token.type == FDT_PROP and path_stack[-1] == path:
            yield token


def get_symbols(tokens):
    res = {}
    for prop in get_props_of_node(tokens, "/__symbols__"):
        index = prop.value.find(b"\0")
        assert index >= 0
        res[prop.name] = prop.value[:index].decode()
    return res


def pretty_value_bytes(value, prop_name):
    # Try to parse as string array.
    parts = value.split(b"\0")
    heuristic = len(parts) != 0 and parts[-1] == b"" and b"" not in parts[:-1]
    if heuristic or prop_name.endswith("-names"):
        try:
            pretty_strings = []
            for string in parts[:-1]:
                pretty_strings.append(f'"{string.decode()}"')
            return ", ".join(pretty_strings)
        except UnicodeDecodeError:
            pass

    # Special rendering for 4-bytes multiples.
    if len(value) % 4 == 0:
        res = []
        for i in range(0, len(value), 4):
            res.append(hex(read_uint32(value[i : i + 4])))
        return " ".join(res)

    # Ugly rendering for the remaining (mostly MAC addresses)?
    return f"0x{value.hex()}"


Node = collections.namedtuple("Node", "name, path, props, children")


def to_graph(tokens):
    stack = []
    for t in tokens:
        if t.type == FDT_BEGIN_NODE:
            stack.append(Node(t.name, t.path, {}, []))
        elif t.type == FDT_END_NODE:
            if len(stack) >= 2:
                stack[-2].children.append(stack[-1])
            if len(stack) != 1:  # keep the root element
                stack.pop()
        elif t.type == FDT_PROP:
            stack[-1].props[t.name] = t.value
            pass

    return stack[0]


def generate_html(tokens, reserve_entries, f):
    f.write("""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>DTBiz</title>
        </head>
        <body>

        <main>
        <section id="tree">
    """)

    symbols = get_symbols(tokens)
    path_to_symbol_mapping = {v: k for k, v in symbols.items()}

    def dump_html_node(node):
        f.write('<div class="node">')

        f.write('<div class="node-header">')
        name = node.name or "/"
        if symbol := path_to_symbol_mapping.get(node.path):
            name = f"{symbol}: {name}"
        f.write(f'<p class="node-name">{name}</p>')
        if node.props:
            f.write('<ul class="node-props">')
            for prop, value in node.props.items():
                if value == b"":
                    f.write(f"<li>{prop};</li>")
                else:
                    value = pretty_value_bytes(value, prop)
                    f.write(f"<li>{prop}: {value};</li>")
            f.write("</ul>")
        f.write("</div>")

        if node.children:
            f.write('<div class="children">')
            for child in node.children:
                dump_html_node(child)
            f.write("</div>")
        f.write("</div>")

    root = to_graph(tokens)
    dump_html_node(root)

    f.write("""
        </section>
        </main>
        </body>
        <style type="text/css">
        section#tree { font-size: 1.2em; }
        section#tree { max-width: fit-content; }
        section#tree > div.node { border: solid 1px; }
        section#tree div.node {
            display: flex;
            flex-direction: row;
            align-items: stretch;
            background-color: rgba(0, 0, 150, 0.04);
            flex-grow: 1;
        }
        section#tree div.node:not(:last-child) { border-bottom: solid 1px; }
        section#tree .node-header { display: flex; flex-direction: column; padding: .1em 1em; justify-content: center; }
        section#tree .node-header .node-name { margin-top: 0; margin-bottom: 0; align-content: center; }
        section#tree .node-header .node-props { margin-top: 0; margin-bottom: 0; max-width: 30ch; }
        section#tree .node-header .node-props { display: none; }
        section#tree .node-header.active .node-props { display: block; }
        section#tree div.children { flex-grow: 1; display: flex; flex-direction: column; }
        </style>

        <script type="text/javascript">
        document.addEventListener('DOMContentLoaded', function () {
            document.querySelectorAll('section#tree .node-header').forEach((el) => {
                el.addEventListener('click', () => {
                    if (document.getSelection().type !== 'Range')
                        el.classList.toggle('active')
                })
            })
        })
        </script>
        </html>
    """)


def debug_print(do_debug: bool, *args):
    if do_debug:
        print(*args, file=sys.stderr, flush=True)


def main():
    # Parse arguments.
    parser = argparse.ArgumentParser(
        description="turn a .DTB into a .SVG visualisation"
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("-o", "--output", default="-")
    parser.add_argument("dtb_filepath", help="path to DTB file")
    args = parser.parse_args()
    do_debug = args.debug

    # Get buffer from file/stdin.
    if args.dtb_filepath == "-":
        buf = sys.stdin.buffer.read()
    else:
        with open(args.dtb_filepath, "rb") as f:
            buf = f.read()

    hdr = get_header(do_debug, buf)

    reserve_entries = list(get_reserve_entries(buf, hdr))
    tokens = list(get_structure_tokens(do_debug, buf, hdr))

    if args.output == "-":
        generate_html(tokens, reserve_entries, sys.stdout)
    else:
        with open(args.output, "w") as html_file:
            generate_html(tokens, reserve_entries, html_file)


if __name__ == "__main__":
    main()
