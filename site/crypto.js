/* ST Vault — 浏览器端解密（WebCrypto, AES-256-GCM + PBKDF2-SHA256）
   与 scripts/build_vault.py 的参数保持一致。密钥只存在于本页内存，不落盘。 */
const VAULT_PARAMS = { iter: 210000, aad: "ST-vault-v1" };

function b64ToBuf(s) {
  const bin = atob(s); const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return u8;
}
function bufToB64(buf) {
  const u8 = new Uint8Array(buf); let s = "";
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  return btoa(s);
}
async function deriveKey(password, saltBuf) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: saltBuf, iterations: VAULT_PARAMS.iter, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
}
/* 口令 → verifier（用于本地校验，不联网） */
async function makeVerifier(password, saltB64) {
  const salt = b64ToBuf(saltB64);
  const key = await deriveKey(password, salt);
  const raw = await crypto.subtle.exportKey("raw", key);
  const h = await crypto.subtle.digest("SHA-256", raw);
  return bufToB64(h);
}
/* 口令 → analytics JSON 对象；失败抛错 */
async function unlockVault(vault, password) {
  const salt = b64ToBuf(vault.salt);
  const key = await deriveKey(password, salt);
  const raw = await crypto.subtle.exportKey("raw", key);
  const h = await crypto.subtle.digest("SHA-256", raw);
  if (bufToB64(h) !== vault.verifier) throw new Error("WRONG_PASSWORD");
  const ct = b64ToBuf(vault.data);
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: b64ToBuf(vault.iv), additionalData: new TextEncoder().encode(VAULT_PARAMS.aad), tagLength: 128 },
    key, ct);
  return JSON.parse(new TextDecoder().decode(pt));
}
