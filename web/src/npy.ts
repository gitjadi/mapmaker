// Reading and writing NumPy .npy arrays – the format the desktop app stores data tables in.

export type NumArray = Float64Array | Int32Array | Int16Array | Int8Array | Uint8Array | Float32Array;
export interface Npy { data: NumArray; shape: number[] }

export function readNpy(buf: Uint8Array): Npy {
  if (buf[0] !== 0x93 || String.fromCharCode(...buf.subarray(1, 6)) !== 'NUMPY') throw new Error('Not an .npy array');
  const major = buf[6];
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const hlen = major === 1 ? dv.getUint16(8, true) : dv.getUint32(8, true);
  const hstart = major === 1 ? 10 : 12;
  const header = new TextDecoder('latin1').decode(buf.subarray(hstart, hstart + hlen));
  const descr = /'descr':\s*'([^']+)'/.exec(header)?.[1] || '';
  const fortran = /'fortran_order':\s*True/.test(header);
  const shape = (/'shape':\s*\(([^)]*)\)/.exec(header)?.[1] || '').split(',').map(s => s.trim()).filter(Boolean).map(Number);
  if (fortran && shape.length > 1) throw new Error('Fortran-ordered arrays are not supported');
  const n = shape.reduce((a, b) => a * b, 1);
  const off = hstart + hlen;
  const bytes = buf.slice(off);                   // a copy, so the typed array is aligned
  const b = bytes.buffer;
  const little = !descr.startsWith('>');
  const t = descr.replace(/^[<>|=]/, '');
  if (!little && t !== 'i1' && t !== 'u1' && t !== 'b1') throw new Error('Big-endian arrays are not supported');
  let data: NumArray;
  switch (t) {
    case 'f8': data = new Float64Array(b, 0, n); break;
    case 'f4': data = new Float32Array(b, 0, n); break;
    case 'i4': data = new Int32Array(b, 0, n); break;
    case 'i2': data = new Int16Array(b, 0, n); break;
    case 'i1': data = new Int8Array(b, 0, n); break;
    case 'u1': case 'b1': data = new Uint8Array(b, 0, n); break;
    case 'i8': {                                  // int64 → float64 (offsets and counts fit easily)
      const src = new BigInt64Array(b, 0, n);
      data = new Float64Array(n);
      for (let i = 0; i < n; i++) data[i] = Number(src[i]);
      break;
    }
    default: throw new Error(`Unsupported array type ${descr}`);
  }
  return { data, shape };
}

/** Write an array as .npy. `int64` stores a Float64Array of whole numbers as '<i8' (like the desktop app). */
export function writeNpy(data: NumArray, shape: number[] = [data.length], int64 = false): Uint8Array {
  let descr: string, body: Uint8Array;
  if (int64) {
    const a = new BigInt64Array(data.length);
    for (let i = 0; i < data.length; i++) a[i] = BigInt(Math.round(data[i]));
    descr = '<i8'; body = new Uint8Array(a.buffer);
  } else {
    descr = data instanceof Float64Array ? '<f8' : data instanceof Float32Array ? '<f4' : data instanceof Int32Array ? '<i4'
      : data instanceof Int16Array ? '<i2' : data instanceof Int8Array ? '|i1' : '|u1';
    body = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  const sh = shape.length === 1 ? `(${shape[0]},)` : `(${shape.join(', ')})`;
  let header = `{'descr': '${descr}', 'fortran_order': False, 'shape': ${sh}, }`;
  const total = 10 + header.length + 1;
  header += ' '.repeat((64 - (total % 64)) % 64) + '\n';
  const out = new Uint8Array(10 + header.length + body.length);
  out.set([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 1, 0]);
  new DataView(out.buffer).setUint16(8, header.length, true);
  for (let i = 0; i < header.length; i++) out[10 + i] = header.charCodeAt(i);
  out.set(body, 10 + header.length);
  return out;
}
