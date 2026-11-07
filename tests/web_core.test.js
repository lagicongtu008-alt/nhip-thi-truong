// Test cho web/index.html, chỉ dùng module có sẵn của Node.
// Chạy: node tests/web_core.test.js
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { readHtml, scripts, loadCore, loadEditions, plain, PINNED_UNTIL, LIMIT } = require('./ntt_load');

const results = [];
function test(name, fn){
  try { fn(); results.push([true, name]); }
  catch (e) { results.push([false, name, e && e.message]); }
}

const html = readHtml();
const blocks = scripts(html);
const N = loadCore();
const mainCode = blocks.map(b => b.code).find(c => c.includes('document.')) || '';

/* TC2: cú pháp mọi thẻ <script> (node --check trên file tạm) */
test('TC2 node --check mọi khối <script>', () => {
  assert.ok(blocks.length >= 2, 'phải có ít nhất 2 khối script');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ntt-'));
  blocks.forEach((b, i) => {
    const f = path.join(dir, 'script' + i + '.js'); fs.writeFileSync(f, b.code);
    execFileSync(process.execPath, ['--check', f], { stdio: 'pipe' });
  });
  fs.rmSync(dir, { recursive: true, force: true });
});

/* TC3: không thư viện ngoài */
test('TC3 không có <script src=', () => { assert.ok(!/<script[^>]*\ssrc=/i.test(html)); });

/* TC4: đã bỏ phần thừa, còn câu miễn trừ */
test('TC4 đã bỏ phần trang trí và còn câu miễn trừ', () => {
  ['hero-chart', 'drawHero', 'tape-track', '@keyframes tape', 'thư mục data/ của dự án', 'linear-gradient(var(--grid', 'repeating', 'renderTape', 'wrap-inner', '--grid', '--glow', '--candle', '--trend']
    .forEach(s => assert.ok(!html.includes(s), 'vẫn còn: ' + s));
  assert.ok(html.includes('không phải khuyến nghị đầu tư'));
  assert.ok(!/linear-gradient|radial-gradient/.test(html), 'còn gradient nền');
});

/* TC5: tên trường JSON vẫn được đọc */
test('TC5 còn đọc đủ tên trường JSON', () => {
  ['banking', 'securities', 'metrics', 'news', 'articles', 'sectors', 'sectorsSummary', 'anomalies', 'themes', 'watchlist', 'updatedAt']
    .forEach(f => assert.ok(html.includes(f), 'thiếu ' + f));
});

/* TC6: parseVN */
test('TC6 parseVN đủ ca kiểm thử bắt buộc', () => {
  const cases = [
    ['1.725,36', 1725.36, false], ['25.615', 25615, false], ['8,88', 8.88, false], ['+436', 436, false],
    ['−225', -225, false], ['≈ −453', -453, true], ['~15.800', 15800, true], ['> 19.900', 19900, true],
    ['3,70', 3.7, false]
  ];
  cases.forEach(([s, v, ap]) => assert.deepStrictEqual(plain(N.parseVN(s)), { v, approx: ap }, s));
  ['26.000–26.050', '', 'abc', null, undefined, '0,2–0,5', '357 / 352', '+1,22%', '22+'].forEach(s => assert.strictEqual(N.parseVN(s), null, String(s)));
});
test('TC6 parseVN đối chứng âm: bỏ kiểm tra khoảng giá thì "26.000–26.050" không còn null', () => {
  // Biến thể cố tình sai: chỉ đổi dấu và bỏ dấu chấm, không chặn khoảng giá.
  const naive = s => { const n = Number(String(s).replace(/^[−–]/, '-').replace(/\./g, '').replace(',', '.').replace(/[–−]/g, '')); return Number.isFinite(n) ? n : null; };
  assert.notStrictEqual(naive('26.000–26.050'), null);
  assert.strictEqual(N.parseVN('26.000–26.050'), null);
});

/* TC7: nextEdition (giờ VN, nhập bằng mốc UTC để không phụ thuộc múi giờ máy) */
const vn = s => new Date(s + '+07:00');
test('TC7 nextEdition các mốc thời gian', () => {
  assert.strictEqual(N.nextEdition(vn('2026-11-06T10:00:00')), '~16h45 chiều nay');
  assert.ok(N.nextEdition(vn('2026-11-06T17:00:00')).includes('sáng thứ Bảy 07/11'));
  assert.ok(N.nextEdition(vn('2026-11-07T10:00:00')).includes('sáng Chủ nhật 08/11'));
  assert.ok(N.nextEdition(vn('2026-11-08T05:00:00')).includes('sáng nay'));
  assert.ok(N.nextEdition(vn('2026-11-08T18:00:00')).includes('sáng thứ Hai 09/11'));
  assert.strictEqual(N.nextEdition(vn('2026-11-05T06:44:00')), '~6h45 sáng nay');
  assert.strictEqual(N.nextEdition(vn('2026-11-05T06:45:00')), '~16h45 chiều nay');
  assert.strictEqual(N.nextEdition(vn('2026-11-05T16:45:00')), '~6h45 sáng mai');
  assert.ok(N.nextEdition(vn('2026-11-07T06:50:00')).includes('sáng Chủ nhật 08/11'));
});
test('TC7 đối chứng âm: thứ Bảy 10:00 không ra 16h45; mốc UTC 03:00 thứ Bảy không bị coi là giờ máy', () => {
  assert.ok(!N.nextEdition(vn('2026-11-07T10:00:00')).includes('16h45'));
  // 2026-11-06T23:30Z = 06:30 sáng thứ Bảy giờ VN → "sáng nay"
  assert.strictEqual(N.nextEdition(new Date('2026-11-06T23:30:00Z')), '~6h45 sáng nay');
});

/* TC8: keyOrder */
test('TC8 keyOrder sắp chiều trên sáng cùng ngày', () => {
  const ks = ['2026-11-06-sang', '2026-11-06-chieu', '2026-11-07-sang'];
  assert.deepStrictEqual(ks.slice().sort((a, b) => N.keyOrder(b) - N.keyOrder(a)), ['2026-11-07-sang', '2026-11-06-chieu', '2026-11-06-sang']);
  assert.deepStrictEqual(plain(N.sortKeysDesc(ks)), ['2026-11-07-sang', '2026-11-06-chieu', '2026-11-06-sang']);
  assert.strictEqual(N.keyOrder('xyz'), -1);
});
test('TC8 đối chứng âm: .sort().reverse() cũ cho 06-sang trước 06-chieu', () => {
  const old = ['2026-11-06-sang', '2026-11-06-chieu', '2026-11-07-sang'].sort().reverse();
  assert.ok(old.indexOf('2026-11-06-sang') < old.indexOf('2026-11-06-chieu'));
});
test('TC8 loadData dùng sortKeysDesc, không còn .sort().reverse() cho bản tin ngày', () => {
  assert.ok(/N\.sortKeysDesc\(idx\.daily/.test(mainCode));
  assert.ok(!/idx\.daily[^\n]*\.sort\(\)\.reverse\(\)/.test(mainCode));
});

/* TC10: số liệu tab Phân tích. Test dùng số cố định chạy trên tập bản tin chốt mốc PINNED_UNTIL,
   nên bản tin mới thêm hằng ngày không làm test đỏ. */
const eds = loadEditions(N, { until: PINNED_UNTIL });
const S = plain(N.allSeries(eds));
test('TC10 VN-Index 7 điểm đúng thứ tự, thay đổi kỳ −48,79', () => {
  const exp = [['2026-10-23', 1774.15], ['2026-10-30', 1741.62], ['2026-11-02', 1710.72], ['2026-11-03', 1699.62], ['2026-11-04', 1733.02], ['2026-11-05', 1740.73], ['2026-11-06', 1725.36]];
  assert.deepStrictEqual(S.vni.map(p => [p.date, p.v]), exp);
  assert.strictEqual(Math.round((S.vni[6].v - S.vni[0].v) * 100) / 100, -48.79);
});
test('TC10 không điểm nào lấy từ bản sáng', () => {
  Object.keys(S).forEach(id => S[id].forEach(p => assert.ok(/-chieu$/.test(p.key), id + ' ' + p.key)));
});
test('TC10 tỷ giá 06/11 = 25.615; khối ngoại 06/11 +436, 05/11 −225, 02/11 có ≈', () => {
  assert.strictEqual(S.usd.find(p => p.date === '2026-11-06').v, 25615);
  assert.strictEqual(S.foreign.find(p => p.date === '2026-11-06').v, 436);
  assert.strictEqual(S.foreign.find(p => p.date === '2026-11-05').v, -225);
  const d2 = S.foreign.find(p => p.date === '2026-11-02'); assert.strictEqual(d2.v, -453); assert.strictEqual(d2.approx, true);
  assert.strictEqual(S.foreign.find(p => p.date === '2026-11-06').scope, '3 sàn');
});
test('TC10 khối ngoại không lấy label có mã cổ phiếu hoặc số cả tuần', () => {
  S.foreign.forEach(p => assert.ok(!/\b(VCB|ACB)\b|tuần|tháng/.test(p.label), p.label));
});
test('TC10 thanh khoản đổi "nghìn tỷ" sang tỷ đồng và giữ label gốc', () => {
  const p = S.liq.find(x => x.date === '2026-10-23'); assert.strictEqual(p.v, 14100); assert.ok(p.conv.includes('nghìn tỷ'));
  assert.strictEqual(S.liq.find(x => x.date === '2026-11-02').approx, true);
  S.liq.forEach(x => assert.ok(x.label.length > 0));
});
test('TC10 chuỗi ít hơn 2 điểm thì UI ghi "Chưa đủ dữ liệu (cần ≥ 2 phiên)"', () => {
  assert.ok(mainCode.includes('Chưa đủ dữ liệu (cần ≥ 2 phiên)'));
  assert.deepStrictEqual(plain(N.seriesPoints([], N.SERIES[0])), []);
});
test('TC10 thống kê không ném lỗi với dữ liệu thiếu', () => {
  const bad = [{}, { key: '2026-11-01-sang', sectors: null, banking: { news: null } }, null];
  N.sectorStats(bad); N.tickerStats(bad); N.sentimentStats(bad); N.sourceStats(bad); N.allSeries(bad);
});
test('TC10 tiêu đề kỳ "DD/MM – DD/MM/YYYY" (tập chốt mốc)', () => { assert.strictEqual(N.dateRange(eds), '23/10 – 07/11/2026'); });
test('TC10 tập chốt mốc không chứa key sau mốc; tập đầy đủ tối đa ' + LIMIT + ' bản', () => {
  eds.forEach(e => assert.ok(N.keyOrder(e.key) <= N.keyOrder(PINNED_UNTIL), e.key));
  const all = loadEditions(N); assert.ok(all.length <= LIMIT && all.length >= eds.length);
});
test('TC10 dữ liệu hiện tại (không chốt mốc): chuỗi chỉ lấy bản chiều, VN-Index các ngày đã biết không đổi', () => {
  const cur = plain(N.allSeries(loadEditions(N)));
  Object.keys(cur).forEach(id => cur[id].forEach(p => assert.ok(/-chieu$/.test(p.key), id + ' ' + p.key)));
  const known = { '2026-10-23': 1774.15, '2026-11-06': 1725.36 };
  Object.keys(known).forEach(d => { const p = cur.vni.find(x => x.date === d); if (p) assert.strictEqual(p.v, known[d], d); });
});

/* TC14: không có mã màu hex trong phần JS vẽ SVG/biểu đồ */
test('TC14 phần JS tab Phân tích không có mã màu hex', () => {
  const a = mainCode.indexOf('/* ---------- Phân tích'), b = mainCode.indexOf('/* ---------- Header');
  assert.ok(a > 0 && b > a, 'không tìm thấy khối Phân tích');
  const part = mainCode.slice(a, b);
  assert.ok(!/#[0-9a-fA-F]{3,6}\b/.test(part), 'có mã hex');
  assert.ok(/viewBox/.test(part));
  assert.ok(/\.chart\{[^}]*width:100%;height:auto/.test(html), 'SVG phải width:100%;height:auto');
});

/* TC16 + A6: danh sách bài mặc định đóng */
test('TC16 danh sách bài và ngành mặc định đóng', () => {
  assert.ok(!/if \(i === 0\) d\.open = true/.test(mainCode));
  assert.ok(/details', 'all-arts'/.test(mainCode));
  assert.ok(/open: false/.test(mainCode));
});

/* A7 + C1: tab Hỏi đáp ẩn sẵn, có tab Phân tích */
test('A7/C1 tab Hỏi đáp ẩn mặc định, tab Phân tích sau 5 ngành', () => {
  assert.ok(/<button[^>]*id="t-qa"[^>]*hidden/.test(html));
  assert.ok(html.indexOf('id="t-sectors"') < html.indexOf('id="t-ana"'));
  assert.ok(/id="p-ana"/.test(html));
  assert.ok(/TABS = \['bank','sec','sectors','ana','week','qa'\]/.test(mainCode));
  assert.ok(/fetch\('api\/health'/.test(mainCode));
});

/* D: tự cập nhật */
test('D1/D2 có visibilitychange, cờ loading, toast aria-live', () => {
  assert.ok(mainCode.includes("addEventListener('visibilitychange'"));
  assert.ok(/if \(loading\) return/.test(mainCode));
  assert.ok(/id="toast"[^>]*role="status"[^>]*aria-live="polite"/.test(html));
  assert.ok(mainCode.includes('Có bản tin mới') && mainCode.includes('Xem bản mới'));
});
test('D (vòng sửa 1) bỏ qua tải định kỳ khi tab ẩn; không dựng lại panel khi dữ liệu không đổi', () => {
  assert.ok(/setInterval\(\(\) => \{ if \(!document\.hidden\) loadData\(\); \}, 5 \* 60 \* 1000\)/.test(mainCode));
  assert.ok(!/setInterval\(loadData,/.test(mainCode));
  assert.ok(/if \(dataSig\(\) === before\) \{ renderStamp\(\); updateNext\(\); \}\s*else renderAll\(\);/.test(mainCode));
  assert.ok(/TICKER_IN_LABEL = N\.TICKER/.test(mainCode), 'regex mã cổ phiếu dùng chung');
});

let fail = 0;
results.forEach(([ok, name, msg]) => { console.log((ok ? 'ĐẠT  ' : 'LỖI  ') + name + (ok ? '' : '\n      ' + msg)); if (!ok) fail++; });
console.log('\n' + (results.length - fail) + '/' + results.length + ' test đạt');
process.exit(fail ? 1 : 0);
