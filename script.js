// https://devicetree-specification.readthedocs.io/en/v0.3/flattened-format.html#structure-block
const FDT_BEGIN_NODE = 1
const FDT_END_NODE = 2
const FDT_PROP = 3
const FDT_NOP = 4
const FDT_END = 9

function parseHeader(buf) {
	// https://devicetree-specification.readthedocs.io/en/v0.3/flattened-format.html#header
	// struct fdt_header
	const view = new DataView(buf, 0, 40)
	const hdr = {
		magic: view.getUint32(0),
		totalsize: view.getUint32(4),
		off_dt_struct: view.getUint32(8),
		off_dt_strings: view.getUint32(12),
		off_mem_rsvmap: view.getUint32(16),
		version: view.getUint32(20),
		last_comp_version: view.getUint32(24),
		boot_cpuid_phys: view.getUint32(28),
		size_dt_strings: view.getUint32(32),
		size_dt_struct: view.getUint32(36),
	}

	console.assert(hdr.magic == 0xD00DFEED)
	console.assert(hdr.totalsize == buf.byteLength)
	console.assert(hdr.version >= 17)
	console.assert(hdr.last_comp_version == 16)
	console.assert(hdr.off_mem_rsvmap < hdr.off_dt_struct)
	console.assert(hdr.off_dt_struct < hdr.off_dt_strings)
	console.assert(hdr.off_dt_strings + hdr.size_dt_strings <= hdr.totalsize)
	console.assert(hdr.off_dt_struct + hdr.size_dt_struct <= hdr.off_dt_strings)
	console.assert(hdr.off_mem_rsvmap % 8 == 0)
	console.assert(hdr.off_dt_struct % 4 == 0)

	return hdr
}

const NodeRegexp = /^[0-9a-zA-Z,\._\+\-]{1,31}(@[0-9a-zA-Z,\._\+\-]+)?$/;

function* parseTokens(buf, hdr) {
	let off = hdr.off_dt_struct
	let parentNodes = []
	const view = new DataView(buf)
	const dec = new TextDecoder();

	// TODO: assert that all props come before children

	while (off < hdr.off_dt_struct + hdr.size_dt_struct) {
		let token = {type: view.getUint32(off)}
		off += 4

		if (token.type == FDT_BEGIN_NODE) {
			// Find & decode the NUL-terminated string.
			let nameLength = 0
			while (view.getUint8(off + nameLength) != 0)
				nameLength++
			token.name = dec.decode(buf.slice(off, off + nameLength))
			off += nameLength + 4 - nameLength%4

			// Check name is valid.
			if (parentNodes.length == 0)
				console.assert(token.name == "")
			else
				console.assert(NodeRegexp.test(token.name))

			parentNodes.push(token.name)
			token.path = parentNodes.length == 1 ? "/" : parentNodes.join('/')
		} else if (token.type == FDT_END_NODE) {
			parentNodes.pop()
		} else if (token.type == FDT_PROP) {
			console.assert(parentNodes.length > 0)

			const propLen = view.getUint32(off)
			const nameOff = view.getUint32(off+4)
			off += 8

			let nameLength = 0;
			while (view.getUint8(hdr.off_dt_strings + nameOff + nameLength) != 0)
				nameLength++;
			const name = buf.slice(hdr.off_dt_strings + nameOff,
				             hdr.off_dt_strings + nameOff + nameLength)
			token.name = dec.decode(name)

			token.value = buf.slice(off, off + propLen)
			off += propLen
			if (propLen % 4 != 0)
				off += 4 - propLen%4
		} else if (token.type == FDT_NOP) {
			// empty on purpose
		} else if (token.type == FDT_END) {
			console.assert(off == hdr.off_dt_struct + hdr.size_dt_struct)
			console.assert(parentNodes.length == 0)
		} else {
			console.assert(false)
		}

		yield token
	}
}

function getPathSymbols(tokens) {
	let res = {}
	const dec = new TextDecoder()
	let pathStack = []

	for (let token of tokens) {
		if (token.type == FDT_BEGIN_NODE) {
			pathStack.push(token.path)
		} else if (token.type == FDT_END_NODE) {
			pathStack.pop()
		} else if (token.type == FDT_PROP &&
			   pathStack[pathStack.length-1] == "/__symbols__") {
			const value = token.value.slice(0, -1)
			res[dec.decode(value)] = token.name
		}
	}

	return res
}

function tokensToGraph(tokens, symbols) {
	let res = {}
	let stack = []

	for (let token of tokens) {
		if (token.type == FDT_BEGIN_NODE) {
			stack.push({
				name: token.name,
				path: token.path,
				symbol: symbols[token.path],
				props: {},
				children: [],
			})
		} else if (token.type == FDT_END_NODE) {
			if (stack.length >= 2)
				stack[stack.length-2].children.push(stack[stack.length-1])
			if (stack.length != 1)
				stack.pop()
		} else if (token.type == FDT_PROP) {
			stack[stack.length-1].props[token.name] = token.value
		}
	}

	return stack[0]
}

function prettyPropValue(propValue, propName) {
	console.assert(propValue.byteLength != 0)
	const arr = new Uint8Array(propValue);
	const dec = new TextDecoder()

	// Try to parse as string array.
	let heuristic = arr[propValue.byteLength-1] == 0
	if (heuristic) {
		for (let i = 1; i < propValue.byteLength; i++) {
			if (arr[i-1] == 0 && arr[i] == 0) {
				heuristic = false
				break
			}
		}
	}
	if (heuristic || propName.endsWith('-names')) {
		let res = []
		let start = 0
		for (let i = 0; i < propValue.byteLength; i++) {
			if (arr[i] == 0) {
				res.push('"' + dec.decode(propValue.slice(start, i)) + '"')
				start = i
			}
		}
		return res.join(', ')
	}

	if (propValue.byteLength % 4 == 0) {
		const view = new DataView(propValue)
		let res = []
		for (let i = 0; i < propValue.byteLength; i += 4) {
			res.push("0x" + view.getUint32(i).toString(16))
		}
		return res.join(' ')
	} else {
		const chars = "0123456789ABCDEF"
		let res = "0x"
		for (let i = 0; i < propValue.byteLength; i++) {
			const x = arr[i]
			res += chars[(x >> 4) & 0xF] + chars[x & 0xF]
		}
		return res
	}
}

function buildDOM(node) {
	let res =`<div class="node">`
	res += `<div class="node-header">`

	let name = node.name || '/'
	if (node.symbol)
		name = `${node.symbol}: ${name}`
	res += `<p class="node-name">${name}</p>`

	if (node.props) {
		res += `<ul class="node-props">`
		for (let propName of Object.keys(node.props)) {
			const propValue = node.props[propName]
			if (propValue.byteLength == 0) {
				res += `<li>${propName};</li>`
			} else {
				const value = prettyPropValue(propValue, propName)
				res += `<li>${propName}: ${value};</li>`
			}
		}
		res += `</ul>` // .node-props
	}
	res += `</div>` // .node-header

	if (Object.keys(node.children).length) {
		res += `<div class="children">`
		for (let child of node.children) {
			res += buildDOM(child)
		}
		res += `</div>` // .children
	}

	res += `</div>` // .node
	return res
}

function load(buf) {
	// document.querySelector('section#file-input').classList.add('disabled')

	const hdr = parseHeader(buf)
	const tokens = Array.from(parseTokens(buf, hdr))
	const symbols = getPathSymbols(tokens)
	const root = tokensToGraph(tokens, symbols)

	const section = document.querySelector('section#tree')
	section.innerHTML = buildDOM(root)

	section.querySelectorAll('.node-header').forEach(el => {
		el.addEventListener('click', () => {
			if (document.getSelection().type !== 'Range')
				el.classList.toggle('active')
		})
	})
}

function loadDemo() {
	fetch('demo.dtb')
		.then(resp => resp.arrayBuffer())
		.then(load)
}

document.addEventListener('DOMContentLoaded', function () {
	const urlParams = new URLSearchParams(window.location.search)
	if (urlParams.has('demo')) {
		loadDemo()
	} else {
		const fileInput = document.querySelector('section#file-input input[type=file]')
		fileInput.addEventListener('input', (e) => {
			const reader = new FileReader()
			reader.addEventListener('load', (e) => load(e.target.result))
			reader.readAsArrayBuffer(e.target.files[0])
		})
		const demoButton = document.querySelector('section#file-input input[type=button]')
		demoButton.addEventListener('click', loadDemo)
	}
})
