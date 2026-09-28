// Same-origin request uses the existing HttpOnly session. No cookie access.
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || location.origin !== 'https://nenovaweb.com'
      || location.pathname !== '/sales/defect-deductions') return;
  if (message.type === 'nenova-defect-ping') { reply({ready:true}); return; }
  if (message.type === 'nenova-defect-screenshot-check') {
    const job = message.job;
    const norm = value => String(value ?? '').replace(/\s+/g,'').toLowerCase();
    const rows = [...document.querySelectorAll('tbody tr')];
    const ready = job.expires_at * 1000 > Date.now() && job.params?.length > 0 && job.params.every(item =>
      rows.some(row => {
        const rect = row.getBoundingClientRect();
        const text = norm(row.innerText + ' ' + [...row.querySelectorAll('input,select')].map(e => e.value).join(' '));
        return rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth
          && item.customerName && item.matchedProductName
          && [item.customerName,item.matchedProductName,item.quantity].every(v => text.includes(norm(v)))
          && [...document.querySelectorAll('input')].some(e => e.value === String(item.orderWeek));
      }));
    reply({ready}); return;
  }
  if (message.type !== 'nenova-defect-job') return;
  const job = message.job;
  if (job.expires_at * 1000 <= Date.now() || !['GET','POST'].includes(job.method)) return;
  if (job.method === 'POST' && !['save','rematch'].includes(job.body?.action)) return;
  (async () => {
    try {
      const url = new URL('/api/sales/defect-deductions',location.origin);
      if (job.method === 'GET') {
        url.search = new URLSearchParams(job.params).toString();
      }
      const response = await fetch(url, {method:job.method,credentials:'same-origin',redirect:'error',
        headers:{'Content-Type':'application/json'},
        body:job.method==='POST'?JSON.stringify(job.body):undefined,
        signal:AbortSignal.timeout(12000)});
      const data = await response.json();
      reply({status:response.status,data});
    } catch { reply({status:0,data:{success:false}}); }
  })();
  return true;
});
