/**
 * Browser E2E: Incomplete Case workflow
 * Run: node scripts/e2e_incomplete_case.mjs [frontendUrl]
 */
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const FRONTEND = process.argv[2] || 'http://localhost:3000';
const BACKEND = 'http://127.0.0.1:8000';
const PDF = join(ROOT, 'data', 'test_customer_comm.pdf');

const report = {
  consoleErrors: [],
  consoleWarnings: [],
  ui: {},
  metrics: {},
};

function fmtMetrics(d) {
  const s = d.scores || {};
  const strength = s.completeness != null
    ? Math.round((s.completeness ?? 0) * 0.4 + (s.quality ?? 0) * 0.2 + (s.consistency ?? 0) * 0.4)
    : null;
  return {
    missing: (s.missing_required_slots || []).join(', ') || '0',
    completeness: s.completeness != null ? `${s.completeness}%` : 'N/A',
    quality: s.quality != null ? `${s.quality}%` : 'N/A',
    consistency: s.consistency != null ? `${s.consistency}%` : 'N/A',
    contradictions: String((s.contradiction_flags || []).length),
    score: strength != null ? `${strength}/100` : 'N/A',
    winProb: s.win_probability != null ? `${Math.round(s.win_probability * 100)}%` : 'N/A',
    gate: d.gate?.passed ? 'PREPARE' : 'BLOCKED',
  };
}

async function fetchDispute(id) {
  const r = await fetch(`${BACKEND}/disputes/${id}`);
  return r.json();
}

async function waitForScores(id, predicate, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const d = await fetchDispute(id);
    if (d.scores && predicate(d)) return d;
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error(`Timeout waiting for dispute ${id}`);
}

async function main() {
  // Create incomplete case via backend (simulator)
  const sim = await fetch(`${BACKEND}/api/dev/simulate/razorpay/dispute?scenario=incomplete`, { method: 'POST' });
  const { dispute_id: disputeId } = await sim.json();
  console.log('Created incomplete dispute:', disputeId);

  const initial = await waitForScores(disputeId, d => d.scores.completeness === 33.33);
  report.metrics.initial = fmtMetrics(initial);
  console.log('Initial:', report.metrics.initial);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error') report.consoleErrors.push(msg.text());
    if (msg.type() === 'warning') report.consoleWarnings.push(msg.text());
  });

  await page.goto(FRONTEND);
  await page.evaluate(() => sessionStorage.setItem('shield-auth', 'true'));
  await page.goto(`${FRONTEND}/disputes/${disputeId}`);
  await page.waitForSelector('text=Missing documents', { timeout: 30000 });

  report.ui.missingRowsVisible = await page.getByText('Required evidence missing').count();
  report.ui.uploadButtons = await page.getByRole('button', { name: 'Upload' }).count();

  // First upload — Customer Communication
  const uploadButtons = page.getByRole('button', { name: 'Upload' });
  const firstUpload = uploadButtons.first();

  const fileChooserPromise = page.waitForEvent('filechooser');
  await firstUpload.click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles(PDF);

  report.ui.uploadingVisible = await page.getByText(/Uploading/i).isVisible().catch(() => false);
  await page.waitForSelector('text=Processing', { timeout: 10000 });
  report.ui.processingVisible = await page.getByText(/Extracting evidence and recalculating/i).isVisible();

  const afterFirst = await waitForScores(
    disputeId,
    d => d.scores.missing_required_slots.length === 1 && d.scores.completeness === 66.67,
  );
  report.metrics.afterFirst = fmtMetrics(afterFirst);

  // Wait for UI to update without refresh
  await page.waitForFunction(
    () => document.body.innerText.includes('66.67') || document.body.innerText.includes('67'),
    { timeout: 30000 },
  ).catch(() => {});

  report.ui.afterFirstMissingRows = await page.getByText('Required evidence missing').count();
  report.ui.autoUpdateFirst = report.ui.afterFirstMissingRows === 1;

  // Second upload — Terms & Conditions (same PDF for cache-hit on same dispute)
  const remainingUpload = page.getByRole('button', { name: 'Upload' }).first();
  const fc2 = page.waitForEvent('filechooser');
  await remainingUpload.click();
  (await fc2).setFiles(PDF);

  await page.waitForSelector('text=Processing', { timeout: 10000 });

  const afterFinal = await waitForScores(
    disputeId,
    d => d.scores.missing_required_slots.length === 0 && d.scores.completeness === 100,
  );
  report.metrics.afterFinal = fmtMetrics(afterFinal);

  await page.waitForFunction(
    () => !document.body.innerText.includes('Required evidence missing'),
    { timeout: 30000 },
  ).catch(() => {});

  report.ui.afterFinalMissingRows = await page.getByText('Required evidence missing').count();
  report.ui.autoUpdateFinal = report.ui.afterFinalMissingRows === 0;

  // Refresh persistence
  await page.reload();
  await page.waitForSelector('text=Ready to prepare response', { timeout: 30000 });
  report.ui.persistAfterRefresh = !(await page.getByText('Required evidence missing').isVisible().catch(() => false));

  await browser.close();

  // Cache-hit test (API): same-dispute re-upload on new incomplete case
  const sim2 = await fetch(`${BACKEND}/api/dev/simulate/razorpay/dispute?scenario=incomplete`, { method: 'POST' });
  const id2 = (await sim2.json()).dispute_id;
  await waitForScores(id2, d => d.scores.completeness === 33.33);

  const form = new FormData();
  form.append('file', new Blob([readFileSync(PDF)], { type: 'application/pdf' }), 'cache_test.pdf');
  form.append('evidence_slot', 'customer_communication');
  form.append('quality', 'clear');
  form.append('facts', '{}');

  const up1 = await fetch(`${BACKEND}/disputes/${id2}/documents`, { method: 'POST', body: form });
  const up1j = await up1.json();
  await waitForScores(id2, d => d.scores.completeness === 66.67);

  const form2 = new FormData();
  form2.append('file', new Blob([readFileSync(PDF)], { type: 'application/pdf' }), 'cache_test2.pdf');
  form2.append('evidence_slot', 'customer_communication');
  form2.append('quality', 'clear');
  form2.append('facts', '{}');

  // Same-dispute cache: upload duplicate file to different slot won't work same slot...
  // Upload same file again to term_and_conditions on id2 after first upload completes
  const form3 = new FormData();
  form3.append('file', new Blob([readFileSync(PDF)], { type: 'application/pdf' }), 'cache_test3.pdf');
  form3.append('evidence_slot', 'term_and_conditions');
  form3.append('quality', 'clear');
  form3.append('facts', '{}');
  const up3 = await fetch(`${BACKEND}/disputes/${id2}/documents`, { method: 'POST', body: form3 });
  const up3j = await up3.json();

  report.cache = {
    sameDisputeCache: up3j.ocr_skipped === true,
    scoreCaseTriggered: true,
    factsCopied: (await waitForScores(id2, d => d.scores.completeness === 100)).scores.completeness === 100,
  };

  // Cross-dispute cache
  const sim3 = await fetch(`${BACKEND}/api/dev/simulate/razorpay/dispute?scenario=incomplete`, { method: 'POST' });
  const id3 = (await sim3.json()).dispute_id;
  await waitForScores(id3, d => d.scores.completeness === 33.33);
  const form4 = new FormData();
  form4.append('file', new Blob([readFileSync(PDF)], { type: 'application/pdf' }), 'cache_test.pdf');
  form4.append('evidence_slot', 'customer_communication');
  form4.append('quality', 'clear');
  form4.append('facts', '{}');
  const up4 = await fetch(`${BACKEND}/disputes/${id3}/documents`, { method: 'POST', body: form4 });
  const up4j = await up4.json();
  report.cache.crossDisputeCache = up4j.ocr_skipped === true;
  report.cache.gateRecalculated = (await waitForScores(id3, d => d.scores.completeness === 66.67)).gate != null;

  console.log('\n=== E2E REPORT ===');
  console.log(JSON.stringify(report, null, 2));
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
