// Nạp phần lõi tính toán (khối <script> đầu của web/index.html) và dữ liệu data/ cho test.
// Chỉ dùng module có sẵn của Node.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const HTML_PATH = path.join(ROOT, 'web', 'index.html');

function readHtml(){ return fs.readFileSync(HTML_PATH, 'utf8'); }

function scripts(html){
  const out = []; const re = /<script(\s[^>]*)?>([\s\S]*?)<\/script>/gi; let m;
  while ((m = re.exec(html))) out.push({ attrs: m[1] || '', code: m[2] });
  return out;
}

function loadCore(){
  const html = readHtml();
  const core = scripts(html).find(s => s.code.includes('__ntt') && !s.code.includes('document.'));
  if (!core) throw new Error('Không tìm thấy khối script lõi (window.__ntt) trong index.html');
  const ctx = vm.createContext({});
  vm.runInContext(core.code, ctx, { filename: 'index.html#core' });
  return ctx.__ntt;
}

// Giống loadData của trang: tối đa 40 bản tin mới nhất.
const LIMIT = 40;
// Mốc chốt cho test dùng số cố định: dữ liệu mới thêm sau mốc này không làm test đỏ.
const PINNED_UNTIL = '2026-11-07-sang';

// opts.until: chỉ lấy key có keyOrder <= keyOrder(until). Không có until = toàn bộ (như trang).
function editionKeys(N, opts){
  const idx = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'index.json'), 'utf8'));
  let keys = N.sortKeysDesc(idx.daily || []);
  if (opts && opts.until) { const lim = N.keyOrder(opts.until); keys = keys.filter(k => N.keyOrder(k) <= lim); }
  return keys.slice(0, LIMIT);
}
function loadEditions(N, opts){
  return editionKeys(N, opts).map(k => JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'daily', k + '.json'), 'utf8')));
}

// Chuyển giá trị từ vm context sang realm hiện tại để so sánh deepStrictEqual.
function plain(x){ return JSON.parse(JSON.stringify(x)); }

module.exports = { ROOT, HTML_PATH, LIMIT, PINNED_UNTIL, readHtml, scripts, loadCore, editionKeys, loadEditions, plain };

// Chạy trực tiếp: in số liệu tab Phân tích dạng JSON (test Python dùng để đối chiếu).
// Kèm danh sách keys đã dùng để phía Python đọc đúng cùng tập bản tin.  Tùy chọn: --until <key>
if (require.main === module) {
  const N = loadCore();
  const i = process.argv.indexOf('--until'); const opts = i > 0 ? { until: process.argv[i + 1] } : {};
  const keys = editionKeys(N, opts); const eds = loadEditions(N, opts);
  process.stdout.write(JSON.stringify({
    keys: keys,
    editions: eds.length,
    series: plain(N.allSeries(eds)),
    sectors: plain(N.sectorStats(eds)),
    sources: plain(N.sourceStats(eds)),
    tickers: plain(N.tickerStats(eds)).slice(0, 15),
    sentiment: plain(N.sentimentStats(eds))
  }));
}
