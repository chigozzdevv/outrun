/* Verify NEP-413 payload digests derived from the archival verifier instructions. */
import { createPublicKey, verify } from 'node:crypto';

const alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const ed25519Header = Buffer.from('302a300506032b6570032100', 'hex');

function decodeBase58(encoded) {
  let value = 0n;
  for (const character of encoded) {
    const digit = alphabet.indexOf(character);
    if (digit < 0) throw Error('Invalid base58 character');
    value = value * 58n + BigInt(digit);
  }
  const bytes = [];
  while (value > 0n) {
    bytes.unshift(Number(value & 255n));
    value >>= 8n;
  }
  const leadingZeros = encoded.match(/^1*/)[0].length;
  return Buffer.concat([Buffer.alloc(leadingZeros), Buffer.from(bytes)]);
}

let input = '';
for await (const chunk of process.stdin) input += chunk;
for (const { hash, pk, sig, collector } of JSON.parse(input)) {
  const [keyType, encodedKey] = pk.split(':');
  const [signatureType, encodedSignature] = sig.split(':');
  const publicBytes = decodeBase58(encodedKey);
  if (keyType !== 'ed25519' || signatureType !== keyType || publicBytes.length !== 32 || publicBytes.toString('hex') !== collector) {
    throw Error('Public key does not match the implicit collector account');
  }
  const key = createPublicKey({ key: Buffer.concat([ed25519Header, publicBytes]), format: 'der', type: 'spki' });
  const digest = Buffer.from(hash, 'hex');
  const signature = decodeBase58(encodedSignature);
  if (!verify(null, digest, key, signature)) throw Error('Invalid collector signature');
  const changed = Buffer.from(digest);
  changed[0] ^= 1;
  if (verify(null, changed, key, signature)) throw Error('Modified NEP-413 digest accepted');
}
