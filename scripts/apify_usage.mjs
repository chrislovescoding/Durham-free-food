// Read account usage only. Never starts an actor or changes billing settings.
// Encrypt the minimal report so account billing figures do not enter public logs.
import fs from 'node:fs';
import crypto from 'node:crypto';

async function main() {
  const token = process.env.APIFY_TOKEN;
  const publicKey = crypto.createPublicKey(Buffer.from(process.env.REPORT_PUBLIC_KEY_B64 || '', 'base64'));
  if (!token || publicKey.asymmetricKeyType !== 'rsa') throw new Error('Missing token or RSA report key');
  const get = async path => {
    const response = await fetch('https://api.apify.com/v2/users/me' + path, {
      method: 'GET', redirect: 'error', signal: AbortSignal.timeout(30000),
      headers: {Authorization: 'Bearer ' + token}
    });
    if (!response.ok) throw new Error('Apify usage request HTTP ' + response.status);
    return (await response.json()).data;
  };
  const [limits, user, usage] = await Promise.all([get('/limits'), get(''), get('/usage/monthly')]);
  const report = {
    checked_at: new Date().toISOString(), plan: user.plan?.id ?? null,
    monthly_credit_usd: user.plan?.monthlyUsageCreditsUsd ?? null,
    monthly_usage_limit_usd: limits.limits?.maxMonthlyUsageUsd ?? null,
    used_usd: limits.current?.monthlyUsageUsd ?? null,
    usage_after_discount_usd: usage.totalUsageCreditsUsdAfterVolumeDiscount ?? null,
    cycle: limits.monthlyUsageCycle ?? usage.usageCycle ?? null
  };
  const key = crypto.randomBytes(32), iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(report), 'utf8'), cipher.final()]);
  const wrapped = crypto.publicEncrypt({key: publicKey, oaepHash: 'sha256', padding: crypto.constants.RSA_PKCS1_OAEP_PADDING}, key);
  const output = {key: wrapped.toString('base64'), iv: iv.toString('base64'),
    tag: cipher.getAuthTag().toString('base64'), ciphertext: ciphertext.toString('base64')};
  fs.mkdirSync('work/apify-usage-report', {recursive: true});
  fs.writeFileSync('work/apify-usage-report/encrypted.json', JSON.stringify(output));
  console.log('Read-only account check complete; encrypted report saved. No actors started.');
}
main().catch(() => { console.error('Account usage check failed; response details withheld.'); process.exitCode = 1; });
